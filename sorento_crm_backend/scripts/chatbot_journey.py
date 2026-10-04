#!/usr/bin/env python3
"""Drive the in-app chatbot console turn endpoint through a JSON chain and grade each
reply against RULINGS, not a recording.

    venv/bin/python scripts/chatbot_journey.py \
        --base http://localhost:8081 --contact 900000008 \
        --chain tests/chatbot/journeys/promo-tier.json --sleep 8

Unlike `chatbot_console_check.py` (which posts a borrowed WEBHOOK envelope at
`/api/v1/external/chat/turn` over `X-API-Key`), this script drives the CONSOLE route,
`POST /api/v1/system/chatbot/console/turn`. That route sits behind `require_permission`,
which reads `get_current_user` only - an `X-API-Key` header is silently ignored there (no
`get_current_user_or_api_key` fallback on this route), so this script logs in for real via
`POST /api/v1/auth/login` and sends the session token it gets back as
`Authorization: Bearer <token>` (measured against `app/dependencies.py`, 17 Sep 2026 - see
the module docstring in `test_parser_schema_guard.py`'s sibling files for the same class of
"measured, not assumed" note). Credentials: `E2E_EMAIL`/`E2E_PASSWORD` from the environment,
or read straight out of `sorento_crm_frontend/.env.local` (gitignored, never printed) the
same pair `documentation/agents/browser-verification.md` names for browser login.

Every turn is a DRY RUN by construction - `console_service.run_console_turn` always sets
`is_test=True`, `ingress="console"` on the envelope it borrows (D14) - so nothing outside
`chatbot.turns` is written and no WhatsApp message can leave, the same guarantee the console
check script documents at length for its own route.

**A chain file is a JSON document, not a recording.** One file names one or more
independent `cases`, each a `turns:` list; every step's `expect` block is graded against
what the RULING says should happen, not against what a prior run captured - a case is
expected to be red until the coder's fix lands, and that is reported as such, not hidden.

    {
      "name": "promo-tier",
      "cases": [
        {
          "name": "promo-tier-srtwc286",
          "cold": true,
          "turns": [
            {"text": "Promo for srtwc286", "expect": {"branch_kind": "check_promotion"}},
            {"text": "1", "expect": {"reply_not_contains": ["No matching results found"]}}
          ]
        }
      ]
    }

`cold` (default `true`) sends `session_vars: {}` on the case's own first turn - the console's
own Reset semantics (`ConsoleTurnRequest.session_vars` is read by MEMBERSHIP: `null` means
"use this contact's stored session", `{}` means "this contact remembers nothing"). Every
step after the first carries forward the PREVIOUS step's own `session_vars` response, which
is how a picker/roster sequence is checked without touching the database.

A case may set its own `contact` (overriding `--contact` for that case only) when its
precondition needs a specific grant profile the default contact does not have. A case that
sets `skip_unless_contact: true` and leaves `contact` unset is SKIPPED (not scored pass or
fail, not printed in the table) rather than silently run against the wrong contact; its
`_note` (free text) explains what contact it needs, printed once at skip time.

Matchers, all optional, combined with AND inside one step's `expect`:

* `branch_kind` - the reply's `branch_kind` must equal this string.
* `reply_contains` - every substring (case-insensitive) must appear somewhere in
  `reply_text` + every `send_messages` entry, joined - what the customer would actually be
  told, silence-checked the same way `chatbot_console_check.py` silence-checks it.
* `reply_not_contains` - none of these substrings may appear anywhere in that same text.
* `reply_contains_any` - AT LEAST ONE of these substrings must appear somewhere in that
  same text - for an "either phrasing is fine" ruling (e.g. a did-you-mean line OR a plain
  not-found line, never a silent broad match).
* `header_names_once` - a substring must appear AT MOST ONCE in the reply's own first
  line (catches the historical "JIMMY - I, JIMMY - I" family-header duplication class).
* `roster_min_options` - the reply must carry at least this many numbered lines
  (`^\\d+\\.\\s`), i.e. an actual picker/roster, not a bare answer.
* `roster_stamped` (bool) - every numbered option line must end in a "has <word>" / "no
  <word>" stamp (has PO / no PO, has incoming / no incoming, has DO / no DO, has promo / no
  promo - the same probe seam per the 17 Sep rulings).
* `max_occurrences` - a `{substring: max_count}` mapping; each substring (case-insensitive)
  must appear no more than `max_count` times in the same text - catches a section/line that
  repeats itself (e.g. a fan-out reply that prints "0 products have incoming stock." once
  per internal branch instead of once per rendered section).
* `db_add_comment_contains` - a list of substrings (hand pass 6, item 2). The console
  response carries no `actions[]` (`ConsoleTurnResponse` never declares the field - measured
  17 Sep 2026), so this reads `response->'actions'` for this turn's own `turn_id` straight
  out of `chatbot.turns` (requires `--db-url`) and requires an `add_comment` action whose
  `text` contains EVERY substring here (case-insensitive) - e.g. `["Team: customer_service"]`
  to pin which team an escalation actually named. Fails loudly (not silently) if `--db-url`
  was not given, or if the turn's `response->'actions'` carries no `add_comment` action.
* `db_no_assign_conversation` (bool) - same `response->'actions'` read; fails if any action
  has `kind == "assign_conversation"` - proves a turn did NOT escalate, catching the case
  where an open escalate offer eats a plain data ask.

Exits non-zero if any step fails. `--record <dir>` additionally writes one text file per
case with every step's turn id, branch, elapsed seconds and full reply, for a human to read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

CONSOLE_TURN_PATH = "/api/v1/system/chatbot/console/turn"
LOGIN_PATH = "/api/v1/auth/login"

ROSTER_LINE_RE = re.compile(r"^[ \t]*(\d+)\.\s+(.*)$", re.MULTILINE)
STAMP_RE = re.compile(r"\b(?:has|no)\s+\S+[ \t]*$", re.IGNORECASE)


def _read_e2e_credentials(backend_root: Path) -> tuple[str | None, str | None]:
    """`E2E_EMAIL`/`E2E_PASSWORD` straight out of the frontend's `.env.local` (never
    printed), the same pair `documentation/agents/browser-verification.md` names for
    browser login. Env vars of the same name, if already exported, win over the file."""
    email = os.getenv("E2E_EMAIL")
    password = os.getenv("E2E_PASSWORD")
    if email and password:
        return email, password
    env_path = backend_root.parent / "sorento_crm_frontend" / ".env.local"
    if not env_path.exists():
        return email, password
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    email = email or values.get("E2E_EMAIL")
    password = password or values.get("E2E_PASSWORD")
    return email, password


def _login(session: Any, base: str, email: str, password: str, timeout: float) -> str:
    url = base.rstrip("/") + LOGIN_PATH
    resp = session.post(url, json={"email": email, "password": password}, timeout=timeout)
    if resp.status_code != 200:
        raise SystemExit(f"login failed: HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    token = body.get("token")
    if not token:
        raise SystemExit("login response carried no token")
    return str(token)


def _post_turn(
    session: Any,
    base: str,
    token: str,
    *,
    contact: str,
    text: str,
    run_id: str,
    session_vars: Any,
    prompt_version_id: str | None,
    timeout: float,
) -> dict[str, Any]:
    url = base.rstrip("/") + CONSOLE_TURN_PATH
    payload = {
        "contact_respond_id": contact,
        "text": text,
        "session_vars": session_vars,
        "prompt_version_id": prompt_version_id,
        "run_id": run_id,
        "media": None,
    }
    resp = session.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return {"_http_error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
    return resp.json()


def _customer_words(body: dict[str, Any]) -> str:
    """Everything this turn would say to the customer: `reply_text` plus every
    `send_messages` entry, joined - the console route's own flatter shape (a list of
    strings, not the external turn endpoint's action dicts)."""
    parts = [body.get("reply_text") or ""]
    parts += [m for m in (body.get("send_messages") or []) if isinstance(m, str)]
    return "\n".join(p for p in parts if p)


def _roster_lines(text: str) -> list[tuple[str, str]]:
    return ROSTER_LINE_RE.findall(text)


def _fetch_actions(db_conn: Any, turn_id: str | None) -> list[dict[str, Any]] | None:
    """`response->'actions'` for this turn_id out of `chatbot.turns`, or None when there
    is no db connection, no turn_id, or the row/column carries nothing - the caller tells
    those apart in its own failure message rather than this helper guessing which one."""
    if db_conn is None or not turn_id:
        return None
    with db_conn.cursor() as cur:
        cur.execute(
            "select response -> 'actions' from chatbot.turns where id = %s",
            (turn_id,),
        )
        row = cur.fetchone()
    if not row or row[0] is None:
        return None
    actions = row[0]
    return actions if isinstance(actions, list) else None


def _grade(
    expect: dict[str, Any], body: dict[str, Any], *, db_conn: Any = None
) -> list[str]:
    """Every expectation that did NOT hold, as sentences. Empty list is a pass."""
    if "_http_error" in body:
        return [body["_http_error"]]
    failures: list[str] = []
    words = _customer_words(body)
    branch = body.get("branch_kind")

    if not words.strip():
        failures.append("the turn would say nothing to the customer")

    wanted_branch = expect.get("branch_kind")
    if wanted_branch and branch != wanted_branch:
        failures.append(f"branch_kind is {branch!r}, expected {wanted_branch!r}")

    for needle in expect.get("reply_contains") or []:
        if str(needle).lower() not in words.lower():
            failures.append(f"reply does not contain {needle!r}")

    for needle in expect.get("reply_not_contains") or []:
        if str(needle).lower() in words.lower():
            failures.append(f"reply contains {needle!r} and must not")

    any_needles = expect.get("reply_contains_any") or []
    if any_needles and not any(str(n).lower() in words.lower() for n in any_needles):
        failures.append(f"reply contains none of {any_needles!r}, expected at least one")

    header_needle = expect.get("header_names_once")
    if header_needle:
        first_line = words.splitlines()[0] if words.splitlines() else ""
        count = first_line.lower().count(str(header_needle).lower())
        if count > 1:
            failures.append(
                f"first line names {header_needle!r} {count} times, expected at most once "
                f"({first_line!r})"
            )

    lines = _roster_lines(words)
    min_opts = expect.get("roster_min_options")
    if min_opts is not None and len(lines) < int(min_opts):
        failures.append(
            f"roster has {len(lines)} numbered options, expected at least {min_opts}"
        )

    if expect.get("roster_stamped"):
        if not lines:
            failures.append("roster_stamped expected but no numbered options were found")
        else:
            unstamped = [num for num, rest in lines if not STAMP_RE.search(rest)]
            if unstamped:
                failures.append(
                    f"options not stamped has/no at line end: {unstamped} "
                    f"(of {len(lines)} total)"
                )

    for needle, max_count in (expect.get("max_occurrences") or {}).items():
        count = words.lower().count(str(needle).lower())
        if count > int(max_count):
            failures.append(
                f"{needle!r} appears {count} times, expected at most {max_count}"
            )

    add_comment_needles = expect.get("db_add_comment_contains")
    if add_comment_needles:
        turn_id = body.get("turn_id")
        if db_conn is None:
            failures.append(
                "db_add_comment_contains was expected but no --db-url was given"
            )
        else:
            actions = _fetch_actions(db_conn, turn_id)
            if actions is None:
                failures.append(
                    f"no response->'actions' found in chatbot.turns for turn_id="
                    f"{turn_id!r}"
                )
            else:
                comments = [
                    str(a.get("text") or "")
                    for a in actions
                    if isinstance(a, dict) and a.get("kind") == "add_comment"
                ]
                if not comments:
                    failures.append(
                        f"no add_comment action in actions for turn_id={turn_id!r}: "
                        f"{actions!r}"
                    )
                else:
                    joined = "\n".join(comments)
                    for needle in add_comment_needles:
                        if str(needle).lower() not in joined.lower():
                            failures.append(
                                f"add_comment action text does not contain {needle!r} "
                                f"(got {joined!r})"
                            )

    if expect.get("db_no_assign_conversation"):
        turn_id = body.get("turn_id")
        if db_conn is None:
            failures.append(
                "db_no_assign_conversation was expected but no --db-url was given"
            )
        else:
            actions = _fetch_actions(db_conn, turn_id) or []
            assign_kinds = [
                a
                for a in actions
                if isinstance(a, dict) and a.get("kind") == "assign_conversation"
            ]
            if assign_kinds:
                failures.append(
                    f"turn_id={turn_id!r} carries an assign_conversation action, "
                    f"expected none: {assign_kinds!r}"
                )

    return failures


def _run_case(
    case: dict[str, Any],
    session: Any,
    base: str,
    token: str,
    *,
    default_contact: str,
    run_id: str,
    sleep_seconds: float,
    prompt_version_id: str | None,
    timeout: float,
    record_dir: str | None,
    db_conn: Any = None,
) -> tuple[list[dict[str, Any]], int]:
    """One case's steps, run in order. Returns (step rows, failed step count)."""
    name = str(case.get("name") or "case")
    if case.get("skip_unless_contact") and not case.get("contact"):
        # This case needs a SPECIFIC contact this chain does not carry (e.g. one holding
        # a grant the default contact lacks) - falling back to `default_contact` would
        # silently test the wrong precondition rather than the one the case names. Skipped
        # cases score neither pass nor fail; they print once and are absent from the table.
        note = case.get("_note") or "needs an explicit 'contact' this chain does not set"
        print(f"    SKIP {name}: {note}")
        return [], 0
    contact = str(case.get("contact") or default_contact)
    cold = case.get("cold", True)
    session_vars: Any = {} if cold else None
    turns = [t for t in (case.get("turns") or []) if isinstance(t, dict)]
    rows: list[dict[str, Any]] = []
    failed = 0
    record_lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        started = time.perf_counter()
        body = _post_turn(
            session,
            base,
            token,
            contact=contact,
            text=str(turn.get("text") or ""),
            run_id=run_id,
            session_vars=session_vars,
            prompt_version_id=prompt_version_id,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - started
        failures = _grade(turn.get("expect") or {}, body, db_conn=db_conn)
        is_fail = bool(failures)
        failed += 1 if is_fail else 0
        words = _customer_words(body)
        rows.append(
            {
                "case": name,
                "step": index,
                "text": turn.get("text"),
                "verdict": "FAIL" if is_fail else "PASS",
                "turn_id": body.get("turn_id"),
                "elapsed": elapsed,
                "branch_kind": body.get("branch_kind"),
                "reply": words.replace("\n", " ")[:100],
                "failures": failures,
            }
        )
        record_lines.append(
            f"step {index}: > {turn.get('text')!r}\n"
            f"  turn_id={body.get('turn_id')} branch={body.get('branch_kind')} "
            f"elapsed={elapsed:.1f}s\n"
            f"  {words}\n"
        )
        if "_http_error" in body:
            # A transport failure poisons every later step's session_vars too - stop this
            # case rather than feed None forward and misreport every remaining step.
            break
        session_vars = body.get("session_vars")

    if record_dir:
        os.makedirs(record_dir, exist_ok=True)
        out_path = Path(record_dir) / f"{name}.txt"
        out_path.write_text("\n".join(record_lines), encoding="utf-8")

    return rows, failed


def _print_table(rows: list[dict[str, Any]]) -> None:
    print(
        f"{'VERDICT':<6} {'case':<40} {'step':<4} {'turn_id':<38} {'elapsed':>8}  "
        f"{'branch':<20} reply"
    )
    for row in rows:
        print(
            f"{row['verdict']:<6} {row['case']:<40} {row['step']:<4} "
            f"{str(row['turn_id'] or ''):<38} {row['elapsed']:>7.1f}s  "
            f"{str(row['branch_kind'] or ''):<20} {row['reply']!r}"
        )
        for failure in row["failures"]:
            print(f"       - {failure}")


def main(argv: list[str] | None = None) -> int:
    import requests

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8081")
    parser.add_argument("--contact", required=True, help="default respond.io contact id")
    parser.add_argument(
        "--chain",
        action="append",
        required=True,
        metavar="FILE",
        help="a journey JSON file (repeatable)",
    )
    parser.add_argument(
        "--sleep", type=float, default=8.0, help="seconds to sleep before every turn"
    )
    parser.add_argument("--email", default=None, help="overrides E2E_EMAIL")
    parser.add_argument("--password", default=None, help="overrides E2E_PASSWORD")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--prompt-version",
        default=None,
        help="pin one ai_prompt_versions.id of chatbot_semantic_parser for this run",
    )
    parser.add_argument(
        "--record", default=None, metavar="DIR", help="write one .txt per case here"
    )
    parser.add_argument(
        "--db-url",
        default=None,
        metavar="URL",
        help=(
            "postgres URL for the SAME database :--base talks to - only needed by "
            "db_add_comment_contains / db_no_assign_conversation matchers (a plain "
            "psycopg2 DSN, e.g. postgresql://user:pass@host:5432/dbname)"
        ),
    )
    args = parser.parse_args(argv)

    backend_root = Path(__file__).resolve().parents[1]
    email, password = _read_e2e_credentials(backend_root)
    email = args.email or email
    password = args.password or password
    if not email or not password:
        print(
            "no E2E_EMAIL/E2E_PASSWORD found (env, --email/--password, or "
            "sorento_crm_frontend/.env.local)",
            file=sys.stderr,
        )
        return 2

    session = requests.Session()
    session.trust_env = False
    token = _login(session, args.base, email, password, args.timeout)
    print(f"logged in as {email} against {args.base}, auth = Authorization: Bearer <token>")

    db_conn = None
    if args.db_url:
        import psycopg2

        db_conn = psycopg2.connect(args.db_url)
        db_conn.autocommit = True
        print(f"db_add_comment_contains / db_no_assign_conversation reads: {args.db_url}")

    run_id = f"journey-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    all_rows: list[dict[str, Any]] = []
    total_failed = 0
    for chain_path in args.chain:
        with open(chain_path, encoding="utf-8") as handle:
            doc = json.load(handle)
        chain_name = doc.get("name") or Path(chain_path).stem
        cases = [c for c in (doc.get("cases") or []) if isinstance(c, dict)]
        if not cases:
            print(f"{chain_path} has no cases", file=sys.stderr)
            total_failed += 1
            continue
        print(f"\n=== {chain_name} ({chain_path}) - {len(cases)} case(s) ===")
        for case in cases:
            rows, failed = _run_case(
                case,
                session,
                args.base,
                token,
                default_contact=args.contact,
                run_id=run_id,
                sleep_seconds=args.sleep,
                prompt_version_id=args.prompt_version,
                timeout=args.timeout,
                record_dir=args.record,
                db_conn=db_conn,
            )
            all_rows.extend(rows)
            total_failed += failed

    if db_conn is not None:
        db_conn.close()

    print()
    _print_table(all_rows)
    passed = len(all_rows) - total_failed
    print(f"\n{passed} passed, {total_failed} failed  (run {run_id})")
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
