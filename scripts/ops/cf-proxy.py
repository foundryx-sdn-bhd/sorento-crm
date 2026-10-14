#!/usr/bin/env python3
"""Cloudflare emergency switch for the Sorento hosts (HA-MONITOR).

    CF_TOKEN_FILE=~/.config/sorento/cf.token python3 scripts/ops/cf-proxy.py status
    CF_TOKEN_FILE=... python3 scripts/ops/cf-proxy.py off          # dry run: prints the plan
    CF_TOKEN_FILE=... python3 scripts/ops/cf-proxy.py off --yes    # proxied=false, ttl=60
    CF_TOKEN_FILE=... python3 scripts/ops/cf-proxy.py on --yes     # proxied=true, ttl=auto

For when Cloudflare itself is the outage: it talks to the API only, so it works while the
dashboard login is down (18 Nov 2025). The zone also holds other clients' hosts, so only
the address records (A / AAAA / CNAME) of ALLOWED_HOSTS are ever read or changed, and
every record is re-checked against the allowlist and the zone before it is written.

Never prints the token or a record's content (origin IPs). Stdlib only, so it runs on any
machine with python3. Runbook: documentation/runbooks/monitoring.md, "Cloudflare switch".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.cloudflare.com/client/v4"
ZONE_NAME = "foundryx.my"
ALLOWED_HOSTS = (
    "fe-sorento.foundryx.my",
    "automate-sorento.foundryx.my",
    "chat.foundryx.my",
    "ss.foundryx.my",
)
SWITCHABLE_TYPES = ("A", "AAAA", "CNAME")
TARGETS = {
    "off": {"proxied": False, "ttl": 60},
    "on": {"proxied": True, "ttl": 1},  # 1 = automatic
}
HTTP_TIMEOUT_SECONDS = 20
# Cloudflare tokens and ids are plain [A-Za-z0-9_-]. Anything else in the token file (a label
# line, a comment) is refused before it can reach a header, and an id from the API that is
# not a plain id never goes into a request path.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,200}$")
ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would resend the Authorization header to wherever a redirect points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class CloudflareError(Exception):
    """An API call failed. The message carries Cloudflare's error codes, never the token."""


def make_api(token: str):
    """A callable(method, path, body=None) -> the response's `result`, over urllib."""

    def call(method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API_BASE + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with _OPENER.open(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read() or b"{}")
            except Exception:
                payload = {}
            raise CloudflareError(f"HTTP {exc.code}: {_errors(payload)}") from None
        except urllib.error.URLError as exc:
            raise CloudflareError(f"cannot reach the Cloudflare API: {type(exc.reason).__name__}") from None
        if not payload.get("success", False):
            raise CloudflareError(_errors(payload))
        return payload.get("result")

    return call


def _errors(payload: dict) -> str:
    errs = payload.get("errors") or []
    parts = [f"{e.get('code')} {e.get('message')}" for e in errs if isinstance(e, dict)]
    return "; ".join(parts) or "unknown error"


def _read_token() -> str:
    path = os.environ.get("CF_TOKEN_FILE", "").strip()
    if not path:
        raise CloudflareError("set CF_TOKEN_FILE to the path of a file holding the API token")
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError as exc:
        raise CloudflareError(f"cannot read CF_TOKEN_FILE: {exc.strerror}") from None
    if not token:
        raise CloudflareError("CF_TOKEN_FILE is empty")
    if not TOKEN_RE.match(token):
        raise CloudflareError("CF_TOKEN_FILE must hold the token alone, on one line with nothing else")
    return token


def _zone_id(api) -> str:
    zones = api("GET", "/zones?" + urllib.parse.urlencode({"name": ZONE_NAME}))
    matches = [z for z in zones or [] if z.get("name") == ZONE_NAME]
    if len(matches) != 1:
        raise CloudflareError(f"expected one zone named {ZONE_NAME}, found {len(matches)}")
    zone_id = str(matches[0].get("id", ""))
    if not ID_RE.match(zone_id):
        raise CloudflareError("the zone id from the API is not a plain id")
    return zone_id


def _records(api, zone_id: str) -> tuple[dict[str, list[dict]], list[str]]:
    """Address records per allowed host, plus a list of refusals (foreign records)."""
    by_host: dict[str, list[dict]] = {}
    refused: list[str] = []
    for host in ALLOWED_HOSTS:
        query = urllib.parse.urlencode({"name": host, "per_page": 100})
        found = api("GET", f"/zones/{zone_id}/dns_records?{query}") or []
        keep = []
        for rec in found:
            if rec.get("name") != host or rec.get("zone_id", zone_id) != zone_id:
                refused.append(f"ignored a record outside the allowlist ({rec.get('name')})")
                continue
            if not ID_RE.match(str(rec.get("id", ""))):
                refused.append(f"ignored a record of {host} whose id is not a plain id")
                continue
            if rec.get("type") in SWITCHABLE_TYPES:
                keep.append(rec)
        by_host[host] = keep
    return by_host, refused


def _fmt_ttl(ttl) -> str:
    return "auto" if ttl == 1 else str(ttl)


def _describe(rec: dict) -> str:
    state = "proxied" if rec.get("proxied") else "DNS only"
    return f"{rec['type']:<5} {state:<8} ttl={_fmt_ttl(rec.get('ttl'))}"


def run(action: str, yes: bool, api) -> int:
    zone_id = _zone_id(api)
    by_host, refused = _records(api, zone_id)
    for line in refused:
        print(f"REFUSED: {line}", file=sys.stderr)

    if action == "status":
        for host in ALLOWED_HOSTS:
            recs = by_host[host]
            if not recs:
                print(f"{host:<32} no record (A/AAAA/CNAME)")
            for rec in recs:
                print(f"{host:<32} {_describe(rec)}")
        return 1 if refused else 0

    target = TARGETS[action]
    plan = []
    for host in ALLOWED_HOSTS:
        if not by_host[host]:
            print(f"{host:<32} no record (A/AAAA/CNAME), skipped")
        for rec in by_host[host]:
            if rec.get("proxied") == target["proxied"] and rec.get("ttl") == target["ttl"]:
                print(f"{host:<32} {_describe(rec)}  (already {action})")
            else:
                plan.append((host, rec))
                print(
                    f"{host:<32} {_describe(rec)}  ->  "
                    f"{'proxied' if target['proxied'] else 'DNS only'} ttl={_fmt_ttl(target['ttl'])}"
                )

    if refused:
        print("\nNothing was written: the API returned a record outside the allowlist.", file=sys.stderr)
        return 1
    if not yes:
        print(f"\nDry run: {len(plan)} record(s) would change. Nothing was written. Re-run with --yes to apply.")
        return 0

    failed = 0
    for host, rec in plan:
        if host not in ALLOWED_HOSTS or rec.get("name") != host or not ID_RE.match(str(rec.get("id", ""))):
            failed += 1
            print(f"REFUSED: {rec.get('name')} is not an allowed host", file=sys.stderr)
            continue
        try:
            api("PATCH", f"/zones/{zone_id}/dns_records/{rec['id']}", dict(target))
            print(f"changed {host} ({rec['type']})")
        except CloudflareError as exc:
            failed += 1
            print(f"FAILED {host} ({rec['type']}): {exc}", file=sys.stderr)
        except Exception as exc:
            # Only the type: a library's message can carry a header (the token) or a body.
            failed += 1
            print(f"FAILED {host} ({rec['type']}): {type(exc).__name__}", file=sys.stderr)
    print(f"\n{len(plan) - failed} changed, {failed} failed. Check with: cf-proxy.py status")
    return 1 if failed else 0


def main(argv: list[str] | None = None, api=None) -> int:
    parser = argparse.ArgumentParser(description="Cloudflare proxy switch for the Sorento hosts only.")
    parser.add_argument("action", choices=["status", "off", "on"])
    parser.add_argument("--yes", action="store_true", help="apply the change (default is a dry run)")
    args = parser.parse_args(argv)
    try:
        token = _read_token()
        if api is None:
            api = make_api(token)
        return run(args.action, args.yes, api)
    except CloudflareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Only the type: http.client's "Invalid header value" message carries the token.
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
