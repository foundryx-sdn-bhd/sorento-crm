"""PII-SCRUB AC-14: `chatbot_record_turn._scrub_contact` must not write the real
Respond.io contact id anywhere into the scrubbed contact. Ids are synthetic and
built at runtime."""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "chatbot_record_turn_ac14", _BACKEND / "scripts" / "chatbot_record_turn.py"
)
assert _spec is not None and _spec.loader is not None
recorder = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("chatbot_record_turn_ac14", recorder)
_spec.loader.exec_module(recorder)



class _LazyGuard:
    """`scripts/pii_guard.py` lives at the repo root, which the backend Docker image
    does not carry: load it on first use and skip the test when it is absent."""

    _mod = None

    def __getattr__(self, name):
        if _LazyGuard._mod is None:
            path = _BACKEND.parent / "scripts" / "pii_guard.py"
            if not path.is_file():
                pytest.skip("repo-root scripts/pii_guard.py not present (backend-only checkout)")
            spec = importlib.util.spec_from_file_location("pii_guard_ac14", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules.setdefault("pii_guard_ac14", module)
            spec.loader.exec_module(module)
            _LazyGuard._mod = module
        return getattr(_LazyGuard._mod, name)


guard = _LazyGuard()


def _real_id(rng: random.Random) -> str:
    return "".join(rng.choice("13579") for _ in range(9))


def _scrubbed(real_id: str) -> dict:
    contact = {
        "id": int(real_id),
        "firstName": "Real",
        "lastName": "Person",
        "email": "someone@example.org",
        "phone": "+60" + real_id,
    }
    recorder._scrub_contact(contact)
    return contact


def test_no_real_contact_id_survives_in_the_scrubbed_contact():
    rng = random.Random(21)
    real = _real_id(rng)
    contact = _scrubbed(real)
    assert real not in json.dumps(contact)
    assert real not in str(contact["id"])


def test_fake_id_is_stable_and_distinct_per_real_id():
    rng = random.Random(22)
    a, b = _real_id(rng), _real_id(rng)
    assert a != b
    assert _scrubbed(a) == _scrubbed(a)
    assert _scrubbed(a)["id"] != _scrubbed(b)["id"]
    assert _scrubbed(a)["phone"] != _scrubbed(b)["phone"]


def test_fields_derive_from_one_fake_id_and_pass_the_guard():
    rng = random.Random(23)
    contact = _scrubbed(_real_id(rng))
    fake_id = str(contact["id"])
    assert fake_id in contact["firstName"]
    assert fake_id in contact["email"]
    assert guard.is_fake_phone(contact["phone"])
    assert guard.scan_text(json.dumps(contact)) == []


# AC-22 ---------------------------------------------------------------------

def _recording(real: str) -> tuple[dict, list]:
    envelope = {
        "contact": {"id": int(real), "firstName": "Real", "lastName": "P", "phone": "+60" + real},
        "message": {
            "contact": {"id": int(real), "firstName": "Real", "lastName": "P"},
            "message": {"contactId": int(real), "text": "hi"},
        },
    }
    tool_results = [
        {
            "tool": "lookup",
            "args": {"contact_id": real, "q": "x"},
            "envelope": {"data": {"contact_id": real, "nested": [{"contactId": int(real)}]}},
        }
    ]
    return envelope, tool_results


def test_every_contact_id_position_carries_the_same_fake_id():
    rng = random.Random(31)
    real = _real_id(rng)
    envelope, tool_results = _recording(real)
    scrubbed_env = recorder._scrub_pii(envelope)
    scrubbed_tools = recorder._scrub_nested_pii(json.loads(json.dumps(tool_results)))
    blob = json.dumps([scrubbed_env, scrubbed_tools])
    assert real not in blob
    fake = str(scrubbed_env["contact"]["id"])
    assert str(scrubbed_env["message"]["message"]["contactId"]) == fake
    assert str(scrubbed_tools[0]["args"]["contact_id"]) == fake
    assert str(scrubbed_tools[0]["envelope"]["data"]["contact_id"]) == fake
    assert str(scrubbed_tools[0]["envelope"]["data"]["nested"][0]["contactId"]) == fake


def test_default_contact_output_name_does_not_hold_the_real_id(monkeypatch):
    rng = random.Random(32)
    real = _real_id(rng)
    slugs: list[str] = []

    class _Result:
        def fetchall(self):
            return [object()]

        def first(self):
            return None

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return _Result()

    class _Url:
        database = "ac22"

    class _Engine:
        url = _Url()

        def connect(self):
            return _Conn()

    monkeypatch.setattr(recorder, "create_engine", lambda *a, **k: _Engine())
    monkeypatch.setattr(recorder, "_source_switches", lambda conn: {})
    monkeypatch.setattr(recorder, "_row_to_dict", lambda row: {})
    monkeypatch.setattr(recorder, "_record_row", lambda row, **k: {})

    def _fake_write(group, slug, turns):
        slugs.append(slug)
        return recorder.REPLAY_ROOT / group / f"{slug}.json"

    monkeypatch.setattr(recorder, "_write", _fake_write)
    argv = ["--db-url", "postgresql://x/y", "--group", "console", "--contact", real, "--chain-by", "none"]
    assert recorder.main(argv) == 0
    assert slugs and all(real not in s for s in slugs)


def test_fake_id_space_has_no_collisions_and_phones_pass_the_guard():
    for count, seed in ((40, 41), (200, 42)):
        rng = random.Random(seed)
        reals: set[str] = set()
        while len(reals) < count:
            reals.add(_real_id(rng))
        fakes = set()
        for real in reals:
            contact = _scrubbed(real)
            fakes.add(str(contact["id"]))
            assert guard.is_fake_phone(contact["phone"])
        assert len(fakes) == count, f"{count - len(fakes)} merged contacts in {count}"


# AC-23 ---------------------------------------------------------------------

_CDN = "https://cdn.example.invalid/"


def _attachment_urls(node, under=False):
    if isinstance(node, dict):
        for k, v in node.items():
            hit = under or k in ("attachment", "media")
            if hit and k in ("url", "source_url") and isinstance(v, str) and v:
                yield v
            yield from _attachment_urls(v, hit)
    elif isinstance(node, list):
        for item in node:
            yield from _attachment_urls(item, under)


def test_recorded_turn_has_no_real_id_in_any_string_and_placeholder_media_urls():
    rng = random.Random(51)
    real = _real_id(rng)
    cdn_url = f"https://cdn.chatapi.net/whatsapp_business/{real}/img-1.jpg?x=1"
    envelope = {
        "contact": {"id": int(real), "firstName": "Real", "lastName": "P"},
        "message": {
            "message": {
                "message": {
                    "attachment": {"type": "image", "url": cdn_url, "source_url": cdn_url},
                    "text": f"my account is {real} thanks",
                }
            }
        },
        "media": {"message": {"attachment": {"url": cdn_url, "source_url": cdn_url}}},
    }
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "created_at": None,
        "envelope": envelope,
        "trace": [
            {"stage": "received", "raw": {"session_vars": {"respond_io_id": real, "note": f"id={real};"}}}
        ],
        "response": {},
    }
    turn = recorder._record_row(row, db_label="ac23", switches={})
    blob = json.dumps(turn)
    fake = recorder._fake_contact_id(real)
    assert real not in blob
    assert str(turn["received_session_vars"]["respond_io_id"]) == fake
    assert f"id={fake};" in blob
    assert f"my account is {fake} thanks" in blob
    urls = list(_attachment_urls(turn))
    assert urls
    assert all(u.startswith(_CDN) for u in urls), urls


def test_committed_recordings_use_placeholder_media_urls():
    roots = [
        _BACKEND / "tests" / "chatbot" / "replay_turns",
        _BACKEND / "tests" / "fixtures" / "chatbot",
    ]
    bad = []
    for root in roots:
        for path in sorted(root.rglob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            bad += [str(path.name) for u in _attachment_urls(data) if not u.startswith(_CDN)]
    assert not bad, f"{len(bad)} media urls off the placeholder host, e.g. {sorted(set(bad))[:3]}"
