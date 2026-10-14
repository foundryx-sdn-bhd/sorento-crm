"""HA-MONITOR: scripts/ops/cf-proxy.py, the Cloudflare emergency switch.

When Cloudflare itself is the outage (18 Nov 2025: the dashboard login was down too),
the owner unproxies the Sorento hosts through the API so traffic goes straight to the
origins. The zone foundryx.my also holds other clients' hosts, which must never be
touched, so the script works from an explicit allowlist and re-checks every record it
is about to change. The default is a dry run; only --yes writes. The token and the
record contents (origin IPs) are never printed.

The Cloudflare API is replaced by a fake that holds a small zone.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ops" / "cf-proxy.py"

pytestmark = pytest.mark.skipif(not SCRIPT.parent.parent.is_dir(), reason="scripts/ not present (backend-only checkout)")

TOKEN = "tok-SECRET-do-not-print"
ZONE_ID = "zone-1"
ORIGIN_IP = "203.0.113.77"
OTHER_IP = "198.51.100.9"

SORENTO = ["fe-sorento.foundryx.my", "automate-sorento.foundryx.my", "chat.foundryx.my", "ss.foundryx.my"]


def _load():
    spec = importlib.util.spec_from_file_location("cf_proxy", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cf():
    return _load()


class FakeCloudflare:
    """Just enough of /client/v4 for the script: zone lookup, record list, record patch."""

    def __init__(self, records=None, zones=None, fail_patch=False):
        self.zones = zones if zones is not None else [{"id": ZONE_ID, "name": "foundryx.my"}]
        self.records = records if records is not None else _zone_records(proxied=True)
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail_patch = fail_patch

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/zones?"):
            name = path.split("name=")[1].split("&")[0]
            return [z for z in self.zones if z["name"] == name]
        if method == "GET" and "/dns_records?" in path:
            zone = path.split("/")[2]
            name = path.split("name=")[1].split("&")[0]
            return [r for r in self.records if r["zone_id"] == zone and r["name"] == name]
        if method == "PATCH" and "/dns_records/" in path:
            if self.fail_patch:
                raise RuntimeError("Cloudflare API error 10000: Authentication error")
            rid = path.rsplit("/", 1)[1]
            rec = next(r for r in self.records if r["id"] == rid)
            rec.update(body)
            return dict(rec)
        raise AssertionError(f"unexpected call {method} {path}")

    def patches(self):
        return [(p.rsplit("/", 1)[1], b) for m, p, b in self.calls if m == "PATCH"]


def _rec(rid, name, rtype="A", content=ORIGIN_IP, proxied=True, ttl=1, zone=ZONE_ID):
    return {"id": rid, "zone_id": zone, "name": name, "type": rtype, "content": content, "proxied": proxied, "ttl": ttl}


def _zone_records(proxied=True):
    ttl = 1 if proxied else 60
    recs = [_rec(f"r{i}", host, proxied=proxied, ttl=ttl) for i, host in enumerate(SORENTO)]
    # Other clients' hosts in the same zone, and a TXT on a Sorento name: never touched.
    recs.append(_rec("other1", "shop.foundryx.my", content=OTHER_IP, proxied=proxied, ttl=ttl))
    recs.append(_rec("other2", "foundryx.my", content=OTHER_IP, proxied=proxied, ttl=ttl))
    recs.append(_rec("txt1", "chat.foundryx.my", rtype="TXT", content="v=spf1 -all", proxied=False, ttl=300))
    return recs


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    p = tmp_path / "cf.token"
    p.write_text(TOKEN + "\n")
    p.chmod(0o600)
    monkeypatch.setenv("CF_TOKEN_FILE", str(p))
    return p


def _run(cf, argv, api, capsys):
    code = cf.main(argv, api=api)
    out = capsys.readouterr()
    return code, out.out + out.err


def _assert_no_secrets(text):
    assert TOKEN not in text
    assert ORIGIN_IP not in text
    assert OTHER_IP not in text


# --- the allowlist ---------------------------------------------------------------------


def test_allowlist_is_exactly_the_four_sorento_hosts(cf):
    assert cf.ZONE_NAME == "foundryx.my"
    assert sorted(cf.ALLOWED_HOSTS) == sorted(SORENTO)


# --- status ----------------------------------------------------------------------------


def test_status_lists_each_host_without_ips_and_writes_nothing(cf, token_file, capsys):
    api = FakeCloudflare()
    code, out = _run(cf, ["status"], api, capsys)
    assert code == 0
    for host in SORENTO:
        assert host in out
    assert "shop.foundryx.my" not in out
    assert api.patches() == []
    _assert_no_secrets(out)


def test_status_only_reads_records_for_allowlisted_names(cf, token_file, capsys):
    api = FakeCloudflare()
    _run(cf, ["status"], api, capsys)
    names = [p.split("name=")[1].split("&")[0] for m, p, b in api.calls if "/dns_records?" in p]
    assert sorted(set(names)) == sorted(SORENTO)


# --- off / on --------------------------------------------------------------------------


def test_off_without_yes_is_a_dry_run(cf, token_file, capsys):
    api = FakeCloudflare()
    code, out = _run(cf, ["off"], api, capsys)
    assert code == 0
    assert api.patches() == []
    assert "dry run" in out.lower()
    for host in SORENTO:
        assert host in out
    _assert_no_secrets(out)


def test_off_with_yes_unproxies_only_the_sorento_address_records(cf, token_file, capsys):
    api = FakeCloudflare()
    code, out = _run(cf, ["off", "--yes"], api, capsys)
    assert code == 0
    patched = dict(api.patches())
    assert sorted(patched) == ["r0", "r1", "r2", "r3"]
    for body in patched.values():
        assert body == {"proxied": False, "ttl": 60}
    by_id = {r["id"]: r for r in api.records}
    assert by_id["other1"]["proxied"] is True and by_id["other2"]["proxied"] is True
    assert by_id["txt1"]["ttl"] == 300
    _assert_no_secrets(out)


def test_on_with_yes_reproxies_with_auto_ttl(cf, token_file, capsys):
    api = FakeCloudflare(records=_zone_records(proxied=False))
    code, out = _run(cf, ["on", "--yes"], api, capsys)
    assert code == 0
    patched = dict(api.patches())
    assert sorted(patched) == ["r0", "r1", "r2", "r3"]
    for body in patched.values():
        assert body == {"proxied": True, "ttl": 1}
    by_id = {r["id"]: r for r in api.records}
    assert by_id["other1"]["proxied"] is False
    _assert_no_secrets(out)


def test_records_already_in_the_target_state_are_left_alone(cf, token_file, capsys):
    api = FakeCloudflare()  # already proxied, ttl 1
    code, out = _run(cf, ["on", "--yes"], api, capsys)
    assert code == 0
    assert api.patches() == []


def test_a_record_from_the_api_outside_the_allowlist_is_never_patched(cf, token_file, capsys):
    """Defence in depth: even if the name filter misbehaved, a foreign record stays put."""

    class LeakyCloudflare(FakeCloudflare):
        def __call__(self, method, path, body=None):
            result = super().__call__(method, path, body)
            if method == "GET" and "/dns_records?" in path:
                result = result + [r for r in self.records if r["id"] == "other1"]
            return result

    api = LeakyCloudflare()
    code, out = _run(cf, ["off", "--yes"], api, capsys)
    assert "other1" not in dict(api.patches())
    assert code != 0


def test_a_record_from_another_zone_is_never_patched(cf, token_file, capsys):
    recs = _zone_records(proxied=True)
    recs[0]["zone_id"] = "zone-of-someone-else"

    class Wrong(FakeCloudflare):
        def __call__(self, method, path, body=None):
            result = super().__call__(method, path, body)
            if method == "GET" and "/dns_records?" in path and "fe-sorento" in path:
                result = result + [recs[0]]
            return result

    api = Wrong(records=recs)
    code, _ = _run(cf, ["off", "--yes"], api, capsys)
    assert "r0" not in dict(api.patches())
    assert code != 0


def test_a_failed_patch_exits_nonzero_without_leaking(cf, token_file, capsys):
    api = FakeCloudflare(fail_patch=True)
    code, out = _run(cf, ["off", "--yes"], api, capsys)
    assert code != 0
    _assert_no_secrets(out)


def test_wrong_zone_lookup_refuses(cf, token_file, capsys):
    api = FakeCloudflare(zones=[])
    code, out = _run(cf, ["status"], api, capsys)
    assert code != 0
    assert api.patches() == []


def test_a_missing_sorento_host_is_reported_not_fatal_for_status(cf, token_file, capsys):
    recs = [r for r in _zone_records() if r["name"] != "ss.foundryx.my"]
    api = FakeCloudflare(records=recs)
    code, out = _run(cf, ["status"], api, capsys)
    assert "ss.foundryx.my" in out
    assert "no record" in out.lower()


# --- the token -------------------------------------------------------------------------


def test_missing_token_file_env_refuses(cf, monkeypatch, capsys):
    monkeypatch.delenv("CF_TOKEN_FILE", raising=False)
    api = FakeCloudflare()
    code, out = _run(cf, ["status"], api, capsys)
    assert code != 0
    assert "CF_TOKEN_FILE" in out
    assert api.calls == []


def test_unreadable_token_file_refuses(cf, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CF_TOKEN_FILE", str(tmp_path / "nope"))
    api = FakeCloudflare()
    code, out = _run(cf, ["status"], api, capsys)
    assert code != 0
    assert api.calls == []


def test_bad_usage_exits_nonzero(cf, token_file, capsys):
    with pytest.raises(SystemExit) as exc:
        cf.main(["purge"], api=FakeCloudflare())
    assert exc.value.code != 0


def test_real_http_client_sends_bearer_token_and_never_prints_it(cf, token_file, monkeypatch, capsys):
    """The urllib client: the token goes only into the Authorization header."""
    seen = {}

    class _Resp:
        def __init__(self, payload):
            self._p = json.dumps(payload).encode()

        def read(self):
            return self._p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _Resp({"success": True, "result": [{"id": ZONE_ID, "name": "foundryx.my"}]})

    monkeypatch.setattr(cf._OPENER, "open", fake_urlopen)
    api = cf.make_api(TOKEN)
    result = api("GET", "/zones?name=foundryx.my")
    assert result == [{"id": ZONE_ID, "name": "foundryx.my"}]
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["url"].startswith("https://api.cloudflare.com/client/v4/zones")
    assert seen["timeout"] and seen["timeout"] <= 30
    assert TOKEN not in capsys.readouterr().out


def test_real_http_client_raises_cloudflare_errors_without_the_token(cf, monkeypatch):
    import io
    import urllib.error

    def fake_urlopen(req, timeout=None):
        body = json.dumps({"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(body.encode()))

    monkeypatch.setattr(cf._OPENER, "open", fake_urlopen)
    api = cf.make_api(TOKEN)
    with pytest.raises(cf.CloudflareError) as exc:
        api("GET", "/zones?name=foundryx.my")
    assert "10000" in str(exc.value)
    assert TOKEN not in str(exc.value)


# --- review round 1 (security + reviewer findings) -------------------------------------


class LeakyCloudflare(FakeCloudflare):
    """Returns a foreign host's record in the answer for an allowlisted name."""

    def __call__(self, method, path, body=None):
        result = super().__call__(method, path, body)
        if method == "GET" and "/dns_records?" in path and "fe-sorento" in path:
            result = result + [r for r in self.records if r["id"] == "other1"]
        return result


@pytest.mark.parametrize("argv", [["status"], ["off"]])
def test_the_list_filter_alone_refuses_a_foreign_record(cf, token_file, capsys, argv):
    """Pins the first allowlist check (the one that builds the record list)."""
    api = LeakyCloudflare()
    code, out = _run(cf, argv, api, capsys)
    assert code == 1
    assert "REFUSED" in out
    assert api.patches() == []


def test_the_write_guard_alone_refuses_a_foreign_record(cf, token_file, capsys, monkeypatch):
    """Pins the second allowlist check (just before each PATCH)."""
    foreign = _rec("other1", "shop.foundryx.my", content=OTHER_IP, proxied=True)
    by_host = {h: [] for h in SORENTO}
    by_host["fe-sorento.foundryx.my"] = [foreign]
    monkeypatch.setattr(cf, "_records", lambda api, zone_id: (by_host, []))
    api = FakeCloudflare()
    code, out = _run(cf, ["off", "--yes"], api, capsys)
    assert api.patches() == []
    assert code != 0
    _assert_no_secrets(out)


def test_a_record_id_that_is_not_a_plain_id_is_never_patched(cf, token_file, capsys):
    recs = _zone_records(proxied=True)
    recs[0]["id"] = "../dns_records/other1"
    api = FakeCloudflare(records=recs)
    code, out = _run(cf, ["off", "--yes"], api, capsys)
    assert [m for m, p, b in api.calls if m == "PATCH" and ".." in p] == []
    assert code != 0


def test_two_zones_with_the_same_name_refuses(cf, token_file, capsys):
    zones = [{"id": ZONE_ID, "name": "foundryx.my"}, {"id": "zone-2", "name": "foundryx.my"}]
    api = FakeCloudflare(zones=zones)
    code, out = _run(cf, ["status"], api, capsys)
    assert code == 2
    assert "found 2" in out
    assert [c for c in api.calls if "/dns_records" in c[1]] == []


@pytest.mark.parametrize(
    "content",
    [
        TOKEN + "\n# my cloudflare token\n",
        "label: " + TOKEN,
        TOKEN + " trailing",
    ],
)
def test_a_token_file_that_is_not_one_plain_token_refuses_without_echoing_it(
    cf, monkeypatch, tmp_path, capsys, content
):
    """A second line in the file once reached the Authorization header, and http.client's
    ValueError printed the whole header (token included)."""
    p = tmp_path / "cf.token"
    p.write_text(content)
    monkeypatch.setenv("CF_TOKEN_FILE", str(p))
    api = FakeCloudflare()
    code, out = _run(cf, ["status"], api, capsys)
    assert code == 2
    assert api.calls == []
    assert TOKEN not in out
    assert "label" not in out


def test_an_unexpected_exception_prints_only_its_type(cf, token_file, capsys):
    def api(method, path, body=None):
        raise ValueError(f"Invalid header value b'Bearer {TOKEN}'")

    code, out = _run(cf, ["status"], api, capsys)
    assert code == 2
    assert "ValueError" in out
    assert TOKEN not in out


def test_a_patch_exception_prints_only_its_type(cf, token_file, capsys):
    class Boom(FakeCloudflare):
        def __call__(self, method, path, body=None):
            if method == "PATCH":
                raise ValueError(f"leaky {TOKEN} {ORIGIN_IP}")
            return super().__call__(method, path, body)

    code, out = _run(cf, ["off", "--yes"], Boom(), capsys)
    assert code != 0
    _assert_no_secrets(out)


def test_real_http_client_does_not_follow_a_redirect_with_the_token(cf, monkeypatch):
    """urllib would copy the Authorization header to wherever a redirect points."""
    import http.server
    import threading

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/client/v4/zones"):
                self.send_response(302)
                self.send_header("Location", "/stolen")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"success": true, "result": []}')

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(cf, "API_BASE", f"http://127.0.0.1:{server.server_port}/client/v4")
        api = cf.make_api(TOKEN)
        with pytest.raises(cf.CloudflareError) as exc:
            api("GET", "/zones?name=foundryx.my")
        assert TOKEN not in str(exc.value)
    finally:
        server.shutdown()
    assert [p for p, _ in hits if p == "/stolen"] == []
