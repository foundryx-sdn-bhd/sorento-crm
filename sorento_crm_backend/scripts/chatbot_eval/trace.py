"""Read tokens and tool errors from a turn's stored trace."""

from __future__ import annotations

import json
from typing import Any

ERROR_MARKER = "Error executing tool"


def parse_trace(trace: list | None) -> tuple[dict, list[str]]:
    tokens = {"prompt": 0, "cached": 0, "output": 0}
    errors: list[str] = []
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
                if env.get("error"):
                    errors.append(f"{tool}: {env['error']}")
                lane = env.get("lane_text")
                if isinstance(lane, str) and lane.startswith(ERROR_MARKER):
                    errors.append(f"{tool}: {lane}")
    return tokens, errors


class DbTraceReader:
    """Reads chatbot.turns.trace over a read-only psycopg2 connection, opened on first use."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn: Any = None

    def _connect(self) -> Any:
        if self._conn is None or self._conn.closed:
            import psycopg2

            conn = psycopg2.connect(self.dsn)
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor() as cur:
                cur.execute("SET default_transaction_read_only = on")
            self._conn = conn
        return self._conn

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
        self._conn: Any = None

    def reset(self, respond_id: str) -> None:
        import psycopg2

        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn)
        try:
            with self._conn, self._conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chatbot.turns WHERE is_test = true AND contact_respond_id = %s", (respond_id,)
                )
                cur.execute(
                    "DELETE FROM conversation_frames WHERE is_test = true AND contact_respond_id = %s",
                    (respond_id,),
                )
        except Exception:
            self._conn.close()
            self._conn = None
            raise
