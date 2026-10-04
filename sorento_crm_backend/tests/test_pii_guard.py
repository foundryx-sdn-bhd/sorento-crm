"""PII-SCRUB - public-repo personal-data guard.

The repo is public. `scripts/pii_guard.py` fails CI when a tracked file holds a
Malaysian mobile number or a lorry plate that is not one of the agreed fake
values, or when a tracked path is a raw capture (`.playwright-mcp/`, `*.har`,
`*.rdb`) that carries prod-copy data. These tests pin the detector rules and
then run it over the whole checkout, so the suite is red while real data is
tracked.

Detection inputs are built at runtime from random digits, so this file never
holds a phone-shaped literal of its own.

The script lives at repo-root `scripts/pii_guard.py`; it is loaded by path so
these tests run from the backend test suite.
"""
from __future__ import annotations

import importlib.util
import random
import re
import sys

import pytest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "pii_guard.py"
_MOD_NAME = "pii_guard_test"


def _load_module():
    if not _SCRIPT.is_file():
        pytest.skip("repo-root scripts/pii_guard.py not present (backend-only checkout)")
    spec = importlib.util.spec_from_file_location(_MOD_NAME, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = module
    spec.loader.exec_module(module)
    return module


def _random_subscriber(rng: random.Random) -> str:
    # 7 odd digits: no zero run, no ascending/descending run, never one digit
    # repeated throughout, so never a value the allowlist may treat as fake.
    digits = [rng.choice("13579") for _ in range(7)]
    digits[1] = "3" if digits[0] != "3" else "5"
    return "".join(digits)


def _kinds(findings):
    return sorted({f.kind for f in findings})


def test_real_looking_mobile_is_flagged_in_every_format():
    mod = _load_module()
    rng = random.Random(1)
    sub = _random_subscriber(rng)
    for text in (
        f'"wa_id": "6017{sub}"',
        f"call +6017{sub} now",
        f"call +60 17-{sub[:3]} {sub[3:]} now",
        f"call 017-{sub[:3]} {sub[3:]} now",
        f"call 017{sub} now",
    ):
        assert _kinds(mod.scan_text(text)) == ["phone"], text


def test_fake_phones_pass():
    mod = _load_module()
    for text in (
        "+60100000001",
        "60100000519",
        "0100000001",
        "+60123456789",
        "012-345 6789",
        "+60 12-345 6789",
    ):
        assert mod.scan_text(text) == [], text


def test_long_digit_runs_are_not_phones():
    mod = _load_module()
    rng = random.Random(2)
    sub = _random_subscriber(rng)
    # Embedded in a longer number (timestamps, ids) is not a phone.
    assert mod.scan_text(f"id 99017{sub}11") == []
    assert mod.scan_text(f"ts 17{sub}123") == []


def test_plate_in_lorry_plate_context_is_flagged():
    mod = _load_module()
    # Plate-shaped samples are joined at runtime so this file holds no literal.
    plate = "VQB" + " 4821"
    for text in (
        "*Driver:* DRIVER A\\n*Lorry Plate:* " + plate + "\\n",
        '{"label": "Lorry Plate", "value": "' + plate.replace(" ", "") + '"}',
        '"lorry_plate": "' + plate + ' A"',
        # Pretty-printed JSON: label and value on separate lines.
        '{\n  "label": "Lorry Plate",\n  "value": "' + plate + '"\n}',
    ):
        assert _kinds(mod.scan_text(text)) == ["plate"], text


def test_fake_plate_passes():
    mod = _load_module()
    for text in (
        "*Lorry Plate:* PLATE-1\\n",
        '{"label": "Lorry Plate", "value": "PLATE-12"}',
        '"lorry_plate": null',
        '"lorry_plate": ""',
    ):
        assert mod.scan_text(text) == [], text


def test_raw_capture_paths_are_forbidden():
    mod = _load_module()
    assert mod.forbidden_path(".playwright-mcp/page-1.yml")
    assert mod.forbidden_path("documentation/plans/x/evidence/step.har")
    assert mod.forbidden_path("dump.rdb")
    assert not mod.forbidden_path("documentation/plans/x/PLAN-y.md")


def test_findings_never_echo_the_value():
    mod = _load_module()
    rng = random.Random(3)
    sub = _random_subscriber(rng)
    [finding] = mod.scan_text(f"x +6019{sub} y")
    assert sub not in mod.format_finding("a.md", finding)


def test_tracked_tree_is_clean():
    mod = _load_module()
    problems = mod.scan_repo(_REPO_ROOT)
    report = "\n".join(mod.format_finding(path, f) for path, f in problems[:50])
    assert problems == [], f"{len(problems)} PII findings:\n{report}"


def test_more_prefix_and_separator_shapes_are_flagged():
    mod = _load_module()
    rng = random.Random(4)
    sub = _random_subscriber(rng)
    for text in (
        f"tel 006017{sub}",
        f"to=%2B6017{sub}&x=1",
        f"contact_6017{sub}",
        f"(017) {sub[:3]}-{sub[3:]}",
    ):
        assert _kinds(mod.scan_text(text)) == ["phone"], text


def test_decimal_fractions_are_not_phones():
    mod = _load_module()
    rng = random.Random(5)
    sub = _random_subscriber(rng)
    assert mod.scan_text(f"<v>45678.017{sub}</v>") == []


def test_a_repeated_digit_run_is_not_enough_to_be_fake():
    mod = _load_module()
    assert not mod.is_fake_phone("6012" + "8888" + "4" + "7" + "3")


def test_spreadsheet_xml_is_scanned():
    import io
    import zipfile

    mod = _load_module()
    rng = random.Random(6)
    sub = _random_subscriber(rng)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f"<sst><si><t>017-{sub}</t></si></sst>")
        z.writestr("xl/vbaProject.bin", b"\x00\x01")
    assert _kinds(mod.scan_bytes("book.xlsm", buf.getvalue())) == ["phone"]


def test_text_that_is_not_utf8_is_still_scanned():
    mod = _load_module()
    rng = random.Random(7)
    sub = _random_subscriber(rng)
    raw = f"caf\xe9 017{sub}".encode("latin-1")
    assert _kinds(mod.scan_bytes("notes.csv", raw)) == ["phone"]


def test_only_the_documented_fake_shapes_pass():
    mod = _load_module()
    assert mod.is_fake_phone("+60 17-000 0501")
    assert mod.is_fake_phone("012-345 6789")
    assert mod.is_fake_phone("+60 11-111 1111")
    # a run somewhere else in a number is not enough
    assert not mod.is_fake_phone("6017" + "29" + "34567")
    assert not mod.is_fake_phone("6019" + "1" + "98765" + "3")


def test_a_value_under_a_phone_key_is_checked_whatever_its_shape():
    mod = _load_module()
    rng = random.Random(8)
    sub = _random_subscriber(rng)
    for key in ("phone", "wa_id", "contact_phone_number"):
        text = '{"' + key + '": "+604' + sub + '1"}'
        assert _kinds(mod.scan_text(text)) == ["phone"], text
    # a contact-id-derived fake stays allowed
    assert mod.scan_text('{"phone": "+60' + "9000000" + '08"}') == []


def test_more_plate_labels_are_read():
    mod = _load_module()
    plate = "VQB" + " 4821"
    for text in (
        "Lorry No: " + plate,
        '"car_plate": "' + plate.replace(" ", "") + '"',
        "No Plat: " + plate,
        "Truck No. " + plate,
    ):
        assert _kinds(mod.scan_text(text)) == ["plate"], text


def test_zero_run_only_counts_straight_after_the_prefix():
    # AC-11: a 0000 run in the middle of the subscriber number is not fake,
    # and the classic prefix with a different ending is not fake.
    mod = _load_module()
    assert mod.is_fake_phone("+60 17-000 0501")
    assert mod.is_fake_phone("+60123456789")
    assert not mod.is_fake_phone("6017" + "12" + "0000" + "3")
    rng = random.Random(9)
    ending = "".join(rng.choice("13579") for _ in range(4))
    assert not mod.is_fake_phone("012-3456 " + ending)
    assert not mod.is_fake_phone("+60123456" + ending)


def test_phone_keys_and_yaml_values_are_read():
    # AC-12: more key spellings and YAML-style values, value is not mobile-shaped.
    mod = _load_module()
    rng = random.Random(10)
    sub = _random_subscriber(rng)
    value = "+604" + sub + "1"
    for key in ("phoneNumber", "phone_no", "whatsapp", "msisdn"):
        text = '{"' + key + '": "' + value + '"}'
        assert _kinds(mod.scan_text(text)) == ["phone"], text
    for text in (
        "phone: '" + value + "'",
        'phone: "' + value + '"',
        "phone: " + value,
    ):
        assert _kinds(mod.scan_text(text)) == ["phone"], text
    assert mod.scan_text("phone: +60" + "9000000" + "08") == []


def test_utf16_text_with_bom_is_scanned():
    # AC-13
    mod = _load_module()
    rng = random.Random(11)
    sub = _random_subscriber(rng)
    text = f"call 017{sub} now"
    for enc in ("utf-16-le", "utf-16-be"):
        bom = b"\xff\xfe" if enc.endswith("le") else b"\xfe\xff"
        data = bom + text.encode(enc)
        assert _kinds(mod.scan_bytes("notes.txt", data)) == ["phone"], enc


_DRIVER_PROSE_FILES = {
    "documentation/plans/security/PLAN-pii-scrub.md",
    "documentation/plans/security/pii-scrub-acceptance-criteria.md",
    "sorento_crm_backend/tests/test_pii_guard.py",
}
_DRIVER_OK = re.compile(r"DRIVER [A-Z]{1,2}|FROM AUTOCOUNT|Documentation|-|")
_Q = r'\\*"'  # a quote, possibly backslash-escaped one or more levels
_VAL = r'(null|' + _Q + r'([^"\\]*)\\*")'
_DRIVER_FIELDS = (
    # *Driver:* <value> up to a newline or a literal backslash-n
    re.compile(r"\*Driver:\*[ \t]*(.*?)(?=\\+n|\n|$)"),
    # {"label": "Driver", "value": <value>} (also pretty-printed)
    re.compile(_Q + r"label" + _Q + r"\s*:\s*" + _Q + r"Driver" + _Q + r"\s*,\s*" + _Q + r"value" + _Q + r"\s*:\s*" + _VAL),
    # "driver_name": <value>
    re.compile(_Q + r"driver_name" + _Q + r"\s*:\s*" + _VAL),
)


def test_driver_fields_in_tracked_text_hold_only_placeholders():
    # AC-24: failures list path:line only, never the value.
    import subprocess

    mod = _load_module()
    files = subprocess.run(
        ["git", "ls-files", "-z"], cwd=_REPO_ROOT, capture_output=True, check=True
    ).stdout.decode().split("\0")
    bad: list[str] = []
    for rel in files:
        # These describe the field names in prose or in this test's own patterns.
        if not rel or rel in _DRIVER_PROSE_FILES:
            continue
        path = _REPO_ROOT / rel
        if not path.is_file() or mod.forbidden_path(rel):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8000]:
            continue
        text = data.decode("utf-8", "replace")
        for rx in _DRIVER_FIELDS:
            for m in rx.finditer(text):
                value = m.group(1) if rx is _DRIVER_FIELDS[0] else (m.group(2) or "")
                value = value.strip().rstrip("*").strip()
                if value == "null" or _DRIVER_OK.fullmatch(value):
                    continue
                bad.append(f"{rel}:{text.count(chr(10), 0, m.start()) + 1}")
    assert not bad, f"{len(bad)} driver values are not placeholders:\n" + "\n".join(sorted(set(bad))[:60])
