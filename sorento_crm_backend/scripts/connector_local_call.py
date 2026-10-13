"""Call the claude.ai connector's tools against a LOCAL copy, the way the shared service (SS) does.

claude.ai cannot reach a local copy, so this stands in for SS: it signs one call with the copy's own
`connector_ss_to_product_secret` / `connector_connection_id` (System Settings > Integrations, env
fallback) as the given user and prints the JSON answer. The route, the permission checks
(`analytics.connector.use` + `system.chat_history.view`), the audit row and the response are the
real ones; only claude.ai and SS are left out. Local and test copies only: it reads the signing
secret from the database, which only an operator of that copy can do.

    venv/bin/python -m scripts.connector_local_call --as owner@example.com chat_list_datasets
    venv/bin/python -m scripts.connector_local_call --as owner@example.com chat_describe_dataset key=turn_costs
    venv/bin/python -m scripts.connector_local_call --as owner@example.com chat_query \\
        --json '{"dataset": "turn_costs", "group_by": ["domain"], "measures": [{"fn": "sum", "column": "cost_usd"}]}'

`--base` defaults to http://localhost:8000. Exit code 0 on HTTP 2xx, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from urllib.parse import urlencode

import httpx
from jose import jwt


def _route(tool: str) -> tuple[str, str]:
    from app.services.analytics_connector.manifest import build_manifest

    for t in build_manifest()["tools"]:
        if t["name"] == tool:
            return t["http"]["method"].upper(), t["http"]["path_template"]
    raise SystemExit(f"unknown tool {tool!r}; see chat_list_datasets, chat_describe_dataset, chat_query, ...")


def _user_id(email: str) -> str:
    from sqlalchemy import text

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        uid = db.execute(text("SELECT id FROM users WHERE lower(email) = lower(:e)"), {"e": email}).scalar()
    finally:
        db.close()
    if uid is None:
        raise SystemExit(f"no user with email {email!r}")
    return str(uid)


def build_request(tool: str, params: dict[str, str], body: object | None, sub: str) -> tuple[str, str, bytes, dict]:
    """(method, path?query, body bytes, headers) for one signed call."""
    from app.services import runtime_settings
    from app.services.analytics_connector.access import request_hash

    config = runtime_settings.get_many(("connector_ss_to_product_secret", "connector_connection_id"))
    secret, connection_id = config.get("connector_ss_to_product_secret"), config.get("connector_connection_id")
    if not secret or not connection_id:
        raise SystemExit("this copy has no connector_ss_to_product_secret / connector_connection_id configured")
    method, template = _route(tool)
    path = template
    for key in list(params):
        if "{" + key + "}" in path:
            path = path.replace("{" + key + "}", params.pop(key))
    query = urlencode(params)
    raw = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
    now = int(time.time())
    token = jwt.encode(
        {
            "typ": "connector_call", "iss": "foundryx-ss", "aud": "sorento-connector-api",
            "connection_id": connection_id, "sub": sub, "grant_id": "local-client",
            "consented_at": now, "tool": tool, "req_hash": request_hash(method, path, query, raw),
            "iat": now, "exp": now + 60, "jti": uuid.uuid4().hex,
        },
        secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Connector {token}"}
    if raw:
        headers["Content-Type"] = "application/json"
    return method, f"{path}?{query}" if query else path, raw, headers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as", dest="email", required=True, help="the CRM user the call runs as")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--json", dest="body", help="JSON body (chat_query)")
    ap.add_argument("tool")
    ap.add_argument("params", nargs="*", help="key=value path or query parameters")
    args = ap.parse_args(argv)
    params = dict(p.split("=", 1) for p in args.params)
    body = json.loads(args.body) if args.body else None
    method, url, raw, headers = build_request(args.tool, params, body, _user_id(args.email))
    resp = httpx.request(method, args.base.rstrip("/") + url, content=raw or None, headers=headers, timeout=30)
    try:
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except ValueError:
        print(resp.text)
    print(f"HTTP {resp.status_code}", file=sys.stderr)
    return 0 if resp.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
