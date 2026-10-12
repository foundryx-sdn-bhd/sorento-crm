"""Resolve the parser prompt a run uses: a version number, a row id, or the production label."""

from __future__ import annotations

PROMPT_NAME = "chatbot_semantic_parser"

_COLUMNS = "v.id::text, v.version, md5(v.template)"
_BY_LABEL = (
    f"SELECT {_COLUMNS} FROM ai_prompt_labels l JOIN ai_prompt_versions v ON v.id = l.version_id "
    "WHERE l.name = %s AND l.label = 'production'"
)
_BY_VALUE = f"SELECT {_COLUMNS} FROM ai_prompt_versions v WHERE v.name = %s AND (v.id::text = %s OR v.version = %s)"


def query_prompt(dsn: str, value: str | None) -> dict | None:
    """The only DB touch: read-only, parameterised. value None means the production label."""
    import psycopg2

    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            if value is None:
                cur.execute(_BY_LABEL, (PROMPT_NAME,))
            else:
                number = int(value) if value.isdigit() else -1
                cur.execute(_BY_VALUE, (PROMPT_NAME, value, number))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"id": row[0], "version": row[1], "template_md5": row[2]}


def resolve_prompt(dsn: str, value: str | None) -> dict:
    found = query_prompt(dsn, value)
    if found is None:
        raise LookupError(f"no {PROMPT_NAME} prompt matches {value!r}" if value else "no production label found")
    return {**found, "source": "pin" if value is not None else "label"}
