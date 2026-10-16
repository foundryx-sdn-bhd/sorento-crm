"""Read tokens and tool errors from a turn's stored trace."""

from __future__ import annotations

import json
import threading
from typing import Any

ERROR_MARKER = "Error executing tool"


def parse_trace(trace: list | None) -> tuple[dict, list[str], list[str]]:
    """Returns (tokens, tool_errors, lane_notes); a tool-less envelope error is the lane's own verdict."""
    tokens = {"prompt": 0, "cached": 0, "output": 0}
    errors: list[str] = []
    notes: list[str] = []
    for entry in trace or []:
        if not isinstance(entry, dict):
            continue
        stage = entry.get("stage")
        facts = entry.get("facts") or {}
        if stage == "understood":
            tokens["prompt"] += int(facts.get("prompt_tokens") or 0)
            tokens["cached"] += int(facts.get("cached_tokens") or 0)
            tokens["output"] += int(facts.get("completion_tokens") or 0)
        if entry.get("status") == "failed" and entry.get("error"):
            errors.append(f"{stage}: {entry['error']}")
        if stage == "looked_up":
            for env in (entry.get("raw") or {}).get("envelopes") or []:
                if not isinstance(env, dict):
                    continue
                tool = env.get("tool")
                err = env.get("error")
                if err and (tool or "returned HTTP" in str(err)):
                    errors.append(f"{tool}: {err}")
                elif err:
                    notes.append(str(err))
                lane = env.get("lane_text")
                if isinstance(lane, str) and lane.startswith(ERROR_MARKER):
                    errors.append(f"{tool}: {lane}")
    return tokens, errors, notes


def build_digest(trace: list | None, reply_text: str, branch_kind: str | None) -> dict:
    """Stage summary kept per turn, so a failure can be traced after the turn row is reset."""
    parser: dict = {}
    resolved: list[dict] = []
    tools: list[dict] = []
    for entry in trace or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("stage") == "understood" and not parser:
            facts = entry.get("facts") or {}
            parser = {k: facts.get(k) for k in ("domain", "intent", "entities", "prompt_version", "prompt_sha256", "model", "provider", "reasoning_effort", "code_sha")}
        if entry.get("stage") == "looked_up":
            for env in (entry.get("raw") or {}).get("envelopes") or []:
                if isinstance(env, dict):
                    resolved.append({k: env.get(k) for k in ("tool", "entities", "miss", "unresolved", "error")})
                    if env.get("tool") and env.get("tool_ran") is True:
                        args: dict = {}
                        for part in ((env.get("lane_ask") or {}).get("filters"), env.get("sales_ranking_frame"), env.get("low_stock_frame")):
                            if isinstance(part, dict):
                                args.update(part)
                        tools.append({"tool": env["tool"], "args": args})
        if entry.get("tool"):
            tools.append({"tool": entry["tool"], "args": entry.get("args")})
    return {"parser": parser, "resolved": resolved, "tools": tools, "branch_kind": branch_kind, "reply_text": reply_text}


class DbTraceReader:
    """Reads chatbot.turns.trace over a read-only psycopg2 connection, opened on first use."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._local = threading.local()

    def _connect(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            import psycopg2

            conn = psycopg2.connect(self.dsn)
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor() as cur:
                cur.execute("SET default_transaction_read_only = on")
            self._local.conn = conn
            return conn
        return conn

    def fetch(self, turn_id: str) -> list | None:
        with self._connect().cursor() as cur:
            cur.execute("SELECT trace FROM chatbot.turns WHERE id = %s", (turn_id,))
            row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]


class DbContactReset:
    """Deletes a contact's is_test turns and frames. Only ever opens a DB named sorento_eval_*."""

    PREFIX = "sorento_eval_"

    def __init__(self, dsn: str):
        from urllib.parse import urlparse

        name = urlparse(dsn).path.lstrip("/")
        if not name.startswith(self.PREFIX):
            raise ValueError(f"contact reset refuses database {name!r}: name must start with {self.PREFIX}")
        self.dsn = dsn
        self._local = threading.local()

    def reset(self, respond_id: str) -> None:
        import psycopg2

        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            conn = self._local.conn = psycopg2.connect(self.dsn)
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chatbot.turns WHERE is_test = true AND contact_respond_id = %s", (respond_id,)
                )
                cur.execute(
                    "UPDATE scm.reorder_run SET status = 'failed' "
                    "WHERE requested_via = 'chat' AND status IN ('queued', 'running')"
                )
                cur.execute(
                    "DELETE FROM conversation_frames WHERE is_test = true AND contact_respond_id = %s",
                    (respond_id,),
                )
        except Exception:
            conn.close()
            self._local.conn = None
            raise
