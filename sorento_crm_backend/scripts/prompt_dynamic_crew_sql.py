"""Generate the PROMPT-DYNAMIC crew-migration SQL (crew copy, 1 Oct 2026).

The crew copy's dev DB is migrated with plain SQL posted as ONE `crew-migration:` PR
comment, and a GitHub comment holds at most 65,536 characters. This writes the SQL twin of
`pdyn_0003_prod_identical` to `sorento_crm_backend/alembic/data/crew-migration-prompt-dynamic.sql`,
small enough for that comment:

- ONE unlabelled `chatbot_semantic_parser` version from the owner's production text (132 KB).
  Each hard-coded list becomes its `{{variable}}` only where the tables, when the SQL runs,
  render exactly that text; the SQL computes each rendering the way `chatbot_prompt_vars`
  does. Lists with no SQL renderer here stay literal. Skips when a version from the same
  snapshot (or with the same template) exists. Never sets a label.
- The text travels compressed, decoded in plain PL/pgSQL (no extension): its non-ASCII
  characters become `^` + an index into a code-point table, the result is LZ77-coded
  (`~<hex offset>,<hex length>;` back-references), and each pair of ASCII characters is
  packed into one code point from U+4E00. The SQL checks the decoded text's sha256 against
  the file before it writes anything.
- pdyn_0001's statements are not repeated: the crew copy already applied them.
  pdyn_0002 has no SQL twin (Python over the copy's own production text).

No dash character reaches the file: the table lists code points as numbers.

    venv/bin/python -m scripts.prompt_dynamic_crew_sql            # write the file
    venv/bin/python -m scripts.prompt_dynamic_crew_sql --check    # exit 1 if stale
    venv/bin/python -m scripts.prompt_dynamic_crew_sql --comment  # print the PR comment body
"""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = BACKEND / "alembic" / "data" / "chatbot_semantic_parser.prod-20261001.txt"
OUT = BACKEND / "alembic" / "data" / "crew-migration-prompt-dynamic.sql"
KEY = "chatbot_semantic_parser"
COMMENT_LIMIT = 65536
ESC = "^"
REF = "~"
PACK_BASE = 0x4E00
# Index characters for the non-ASCII table: printable ASCII minus the two markers.
INDEX_CHARS = "".join(chr(c) for c in range(33, 127) if chr(c) not in (ESC, REF))


def _load(name: str):
    path = BACKEND / "alembic" / "versions" / name
    spec = importlib.util.spec_from_file_location(f"_crew_sql_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _array(values) -> str:
    return "ARRAY[" + ", ".join(_q(v) for v in values) + "]::text[]"


# --------------------------------------------------------------------------- #
# Encoding (decoded by the SQL below; `decode` here is its Python mirror for tests)
# --------------------------------------------------------------------------- #


def _escape(text: str) -> tuple[str, list[int]]:
    table = sorted({ord(c) for c in text if ord(c) > 127})
    assert len(table) <= len(INDEX_CHARS) and ESC not in text and REF not in text
    index = {cp: INDEX_CHARS[i] for i, cp in enumerate(table)}
    return "".join(ESC + index[ord(c)] if ord(c) > 127 else c for c in text), table


def _lz(s: str, min_len: int = 8, gram: int = 6, chain: int = 64) -> str:
    """Greedy LZ77. A reference never overlaps its own output (length <= offset)."""
    heads: dict[str, list[int]] = {}
    out: list[str] = []
    i, n = 0, len(s)

    def remember(k: int) -> None:
        if k + gram <= n:
            heads.setdefault(s[k : k + gram], []).append(k)

    while i < n:
        best_len, best_off = 0, 0
        if i + gram <= n:
            for j in reversed(heads.get(s[i : i + gram], [])[-chain:]):
                off = i - j
                limit = min(n - i, off)
                length = 0
                while length < limit and s[j + length] == s[i + length]:
                    length += 1
                if length > best_len:
                    best_len, best_off = length, off
        if best_len >= min_len:
            out.append(f"{REF}{best_off:x},{best_len:x};")
            for k in range(i, i + best_len):
                remember(k)
            i += best_len
        else:
            out.append(s[i])
            remember(i)
            i += 1
    return "".join(out)


def _pack(s: str) -> str:
    assert all(0 < ord(c) < 128 for c in s)
    if len(s) % 2:
        s += " "
    return "".join(chr(PACK_BASE + ord(a) * 128 + ord(b)) for a, b in zip(s[::2], s[1::2]))


def encode(text: str) -> tuple[str, int, list[int]]:
    escaped, table = _escape(text)
    coded = _lz(escaped)
    return _pack(coded), len(coded), table


def decode(packed: str, length: int, table: list[int]) -> str:
    coded = "".join(chr((ord(c) - PACK_BASE) // 128) + chr((ord(c) - PACK_BASE) % 128) for c in packed)[:length]
    parts = coded.split(REF)
    out = parts[0]
    for part in parts[1:]:
        ref, rest = part.split(";", 1)
        off, ln = (int(x, 16) for x in ref.split(","))
        out += out[len(out) - off : len(out) - off + ln] + rest
    pieces = out.split(ESC)
    return pieces[0] + "".join(chr(table[INDEX_CHARS.index(p[0])]) + p[1:] for p in pieces[1:])


# --------------------------------------------------------------------------- #
# Renderers and candidate lists
# --------------------------------------------------------------------------- #


def _renderers() -> dict[str, str]:
    """SQL for each variable, matching `chatbot_prompt_vars.VARIABLES[...].render` on any
    rows whose rendering equals a clean literal (the only case that swaps)."""
    from app.modules.chatbot.lane_vocabulary import escalation_teams, suggested_agents

    known = list(suggested_agents())
    return {
        "teams": _q("|".join(escalation_teams())),
        "domains": "(SELECT string_agg(name, ' | ' ORDER BY sort_order, name) FROM chatbot_domains)",
        "status_values": (
            "(SELECT string_agg(value, '|' ORDER BY sort_order, value) FROM chatbot_status_words "
            "WHERE 'status_values' = ANY(prompt_lists))"
        ),
        "status_field_values": (
            "(SELECT string_agg(value, '|' ORDER BY sort_order, value) FROM chatbot_status_words "
            "WHERE 'status_field_values' = ANY(prompt_lists))"
        ),
        "order_status_values": (
            "(SELECT string_agg(value, '|' ORDER BY sort_order, value) FROM chatbot_status_words WHERE domain = 'order')"
        ),
        "entity_kinds": "(SELECT string_agg(kind, '|' ORDER BY sort_order, kind) FROM chatbot_entity_kinds)",
        "agents": (
            "COALESCE((SELECT string_agg(code, '|' ORDER BY COALESCE(array_position("
            f"{_array(known)}, code), {len(known)}), code COLLATE \"C\") FROM access_agents WHERE is_active AND in_parser_prompt), "
            f"{_q('|'.join(known))})"
        ),
        "access_levels": (
            "(SELECT CASE WHEN bool_and(position('\"' in name) = 0 AND position(chr(92) in name) = 0 "
            "AND name !~ '[[:cntrl:]]') THEN '[' || string_agg('\"' || name || '\"', ',' "
            "ORDER BY sort_order NULLS LAST, name) || ']' END FROM contact_access_types WHERE is_active)"
        ),
        "entity_kinds_detail": (
            "(SELECT string_agg(format('Entity kind %s: resolver %s. Did-you-mean %s. Default narrowing %s.', "
            "kind, resolver_source, CASE WHEN did_you_mean THEN 'on' ELSE 'off' END, default_narrowing), "
            "chr(10) ORDER BY kind) FROM chatbot_entity_kinds)"
        ),
    }


def _candidates(source: str) -> list[tuple[int, int, str]]:
    """(start, end, variable) of every list `identical_wording_layer` looks at, in the
    original text, sorted by position."""
    from app.services import chatbot_prompt_vars as pv
    from app.services.chatbot_parser_prompt import BLOCKS_BEGIN, BLOCKS_END

    spans: list[tuple[int, int, str]] = []
    taken: set[tuple[int, int]] = set()
    for variable, pattern in pv._IDENTICAL_CANDIDATES:
        for m in re.finditer(pattern, source, flags=re.S):
            span = (m.start("list"), m.end("list"))
            if span not in taken:
                taken.add(span)
                spans.append((span[0], span[1], variable))
                break
    if BLOCKS_BEGIN in source and BLOCKS_END in source:
        start = source.index(BLOCKS_BEGIN) + len(BLOCKS_BEGIN)
        block = source[start : source.index(BLOCKS_END)]
        offset = start + len(block) - len(block.lstrip("\n"))
        for variable, part in zip(["domains_detail", "entity_kinds_detail", "specs"], block.strip("\n").split("\n\n")):
            spans.append((offset, offset + len(part), variable))
            offset += len(part) + 2
    spans.sort()
    for (_, a_end, _), (b_start, _, _) in zip(spans, spans[1:]):
        assert a_end <= b_start, "overlapping candidate lists"
    return spans


# --------------------------------------------------------------------------- #
# The SQL
# --------------------------------------------------------------------------- #


def _pdyn_0004_sql() -> str:
    """The owner's prompt-list tags and curated domain words (migration `pdyn_0004_prompt_lists`),
    as idempotent statements the crew copy applies before the version is built."""
    mod = _load("pdyn_0004_prompt_lists.py")
    lines = [
        "ALTER TABLE chatbot_status_words ADD COLUMN IF NOT EXISTS prompt_lists text[] NOT NULL DEFAULT '{}';",
    ]
    for value, lists in mod.STATUS_PROMPT_LISTS.items():
        lines.append(
            f"UPDATE chatbot_status_words SET prompt_lists = {_array(lists)} "
            f"WHERE value = {_q(value)} AND prompt_lists = '{{}}';"
        )
    lines.append(
        "CREATE TABLE IF NOT EXISTS chatbot_domain_words (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "word text NOT NULL UNIQUE, sort_order integer NOT NULL DEFAULT 0, "
        "created_at timestamp without time zone NOT NULL DEFAULT now(), "
        "updated_at timestamp without time zone NOT NULL DEFAULT now());"
    )
    for i, word in enumerate(mod.DOMAIN_WORDS):
        lines.append(
            f"INSERT INTO chatbot_domain_words (id, word, sort_order) VALUES (gen_random_uuid(), {_q(word)}, {i}) "
            "ON CONFLICT (word) DO NOTHING;"
        )
    agents = _load("pdyn_0006_agents_in_prompt.py")
    lines.append("ALTER TABLE access_agents ADD COLUMN IF NOT EXISTS in_parser_prompt boolean NOT NULL DEFAULT false;")
    lines.append(
        f"UPDATE access_agents SET in_parser_prompt = true WHERE code = ANY({_array(agents.PROMPT_AGENTS)}) "
        "AND NOT EXISTS (SELECT 1 FROM access_agents WHERE in_parser_prompt);"
    )
    order = _load("pdyn_0005_access_level_order.py")
    for i, name in enumerate(order.ACCESS_LEVEL_ORDER, start=1):
        lines.append(
            f"UPDATE contact_access_types SET sort_order = {i} "
            f"WHERE is_active AND name = {_q(name)} AND sort_order IS NULL;"
        )
    return "\n".join(lines)


def build_sql(lookup: bool = False, seed: bool = True) -> str:
    """`lookup=True` carries no text: it takes the owner's text from a version already on
    the database whose text (CRLF folded, trailing newlines trimmed) has the file's sha256,
    and writes nothing when none matches. Small enough to post by hand."""
    mod3 = _load("pdyn_0003_prod_identical.py")
    raw = SNAPSHOT.read_bytes()
    source = raw.decode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    packed, length, table = encode(source)
    assert decode(packed, length, table) == source
    renderers = _renderers()
    spans = _candidates(source)
    lines = [source[:s].count("\n") + 1 for s, _, _ in spans]
    pick = "CASE var " + " ".join(f"WHEN {_q(n)} THEN r_{n}" for n in renderers) + " END"
    return "\n".join(
        [
            _pdyn_0004_sql(),
            "DO $crew$",
            "DECLARE",
            *[f"r_{name} text := {sql};" for name, sql in renderers.items()],
            f"starts int[] := ARRAY[{', '.join(str(s + 1) for s, _, _ in spans)}];",
            f"lens int[] := ARRAY[{', '.join(str(e - s) for s, e, _ in spans)}];",
            f"vars text[] := {_array([v for _, _, v in spans])};",
            f"lines int[] := ARRAY[{', '.join(str(n) for n in lines)}];",
            f"tbl int[] := ARRAY[{', '.join(str(cp) for cp in table)}];",
            f"idx text := {_q(INDEX_CHARS)};",
            "p text; l text; e text := ''; t text; tpl text; part text; var text; lit text; rendered text;",
            "k int; off int; ln int; v int; vars_json jsonb; rep jsonb := '[]'::jsonb; act text; i int; first boolean;",
            "BEGIN",
            f"IF EXISTS (SELECT 1 FROM ai_prompt_versions WHERE name = {_q(KEY)} "
            f"AND config_json->>'prod_snapshot_sha256' = {_q(sha)}) THEN",
            "RAISE NOTICE 'prod snapshot already published; nothing to do'; RETURN; END IF;",
            *(_lookup_lines(sha) if lookup else [
                "p := replace($pk$\n" + "\n".join(packed[i : i + 200] for i in range(0, len(packed), 200)) + "\n$pk$, chr(10), '');",
                "SELECT string_agg(chr((ascii(c) - 19968) / 128) || chr((ascii(c) - 19968) % 128), '' ORDER BY n) INTO l",
                "FROM unnest(string_to_array(p, NULL)) WITH ORDINALITY AS u(c, n);",
                f"l := left(l, {length});",
                "first := true;",
                "FOREACH part IN ARRAY string_to_array(l, '~') LOOP",
                "IF first THEN e := part; first := false; CONTINUE; END IF;",
                "k := strpos(part, ';');",
                "off := ('x' || lpad(split_part(left(part, k - 1), ',', 1), 8, '0'))::bit(32)::int;",
                "ln := ('x' || lpad(split_part(left(part, k - 1), ',', 2), 8, '0'))::bit(32)::int;",
                "e := e || substr(e, length(e) - off + 1, ln) || substr(part, k + 1);",
                "END LOOP;",
                "first := true;",
                "FOREACH part IN ARRAY string_to_array(e, '^') LOOP",
                "IF first THEN t := part; first := false; ELSE t := t || chr(tbl[strpos(idx, left(part, 1))]) || substr(part, 2); END IF;",
                "END LOOP;",
                f"IF encode(sha256(convert_to(t, 'UTF8')), 'hex') <> {_q(sha)} THEN",
                "RAISE EXCEPTION 'prod snapshot: decoded text does not match the owner file; nothing written'; END IF;",
            ]),
            *(_seed_lines() if seed and not lookup else []),
            "tpl := t;",
            "FOR i IN REVERSE array_length(starts, 1)..1 LOOP",
            "var := vars[i]; lit := substr(t, starts[i], lens[i]);",
            f"rendered := {pick};",
            "act := CASE WHEN rendered IS NOT NULL AND rendered = lit THEN 'replaced' ELSE 'kept literal' END;",
            "IF act = 'replaced' THEN tpl := overlay(tpl placing '{{' || var || '}}' from starts[i] for lens[i]); END IF;",
            "rep := jsonb_build_object('variable', var, 'line', lines[i], 'action', act, 'reason', CASE",
            "WHEN act = 'replaced' THEN NULL",
            "WHEN rendered IS NULL AND NOT var = ANY(" + _array(list(renderers)) + ") "
            "THEN 'no SQL renderer: the alembic migration decides this list from the tables'",
            "ELSE 'the registry renders different text' END) || rep;",
            "END LOOP;",
            f"IF EXISTS (SELECT 1 FROM ai_prompt_versions WHERE name = {_q(KEY)} AND template = tpl) THEN",
            "RAISE NOTICE 'an identical template already exists; nothing to do'; RETURN; END IF;",
            f"SELECT COALESCE(max(version), 0) + 1 INTO v FROM ai_prompt_versions WHERE name = {_q(KEY)};",
            "SELECT pv.variables INTO vars_json FROM ai_prompt_versions pv JOIN ai_prompt_labels lb ON lb.version_id = pv.id",
            f"WHERE lb.name = {_q(KEY)} AND lb.label = 'production';",
            "INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, config_json, commit_message, created_at)",
            f"VALUES (gen_random_uuid(), {_q(KEY)}, v, 'text', tpl, COALESCE(vars_json, '[\"current_date\"]'::jsonb),",
            f"jsonb_build_object('prod_snapshot', {_q(SNAPSHOT.name)}, 'prod_snapshot_sha256', {_q(sha)},",
            "'rendered_identical', true, 'identical_report', rep, 'source', 'crew-migration SQL'),",
            f"{_q(mod3.MESSAGE)}, now());",
            "RAISE NOTICE 'prod snapshot v%: published unlabelled', v;",
            "FOR i IN 1..jsonb_array_length(rep) LOOP",
            "RAISE NOTICE 'prod snapshot v%: {{%}} line % %', v, rep->(i-1)->>'variable', rep->(i-1)->>'line', rep->(i-1)->>'action';",
            "END LOOP;",
            "END",
            "$crew$;",
            "",
        ]
    )


SEED_MESSAGE = "prod snapshot 1 Oct (dev seed)"


def _seed_lines() -> list[str]:
    """Dev only (crew, 1 Oct 2026): the crew copy has no version holding the owner's text,
    so put it there verbatim and unlabelled first; the variable version is then built from
    it. Prod needs no seed: alembic pdyn_0003 reads the version `production` points at."""
    return [
        f"SELECT version INTO v FROM ai_prompt_versions WHERE name = {_q(KEY)} AND template = t ORDER BY version LIMIT 1;",
        "IF v IS NULL THEN",
        f"SELECT COALESCE(max(version), 0) + 1 INTO v FROM ai_prompt_versions WHERE name = {_q(KEY)};",
        "INSERT INTO ai_prompt_versions (id, name, version, type, template, variables, config_json, commit_message, created_at)",
        f"VALUES (gen_random_uuid(), {_q(KEY)}, v, 'text', t, '[\"current_date\"]'::jsonb, "
        "jsonb_build_object('dev_seed', true, 'prod_snapshot', 'chatbot_semantic_parser.prod-20261001.txt'), "
        f"{_q(SEED_MESSAGE)}, now());",
        "RAISE NOTICE 'dev seed v%: owner prod text inserted verbatim, unlabelled', v;",
        "ELSE",
        "RAISE NOTICE 'dev seed: owner prod text already present as v%', v;",
        "END IF;",
        "v := NULL;",
    ]


def _lookup_lines(sha: str) -> list[str]:
    return [
        f"FOR t IN SELECT rtrim(replace(template, chr(13) || chr(10), chr(10)), chr(10)) FROM ai_prompt_versions "
        f"WHERE name = {_q(KEY)} ORDER BY version DESC LOOP",
        f"EXIT WHEN encode(sha256(convert_to(t, 'UTF8')), 'hex') = {_q(sha)};",
        "END LOOP;",
        f"IF t IS NULL OR encode(sha256(convert_to(t, 'UTF8')), 'hex') <> {_q(sha)} THEN",
        f"RAISE NOTICE 'prod snapshot: no {KEY} version on this database holds the owner text; nothing written'; "
        "RETURN; END IF;",
    ]


# The pdyn_0003 section on its own (the whole file now).
pdyn_0003_sql = build_sql


def comment_body(sql: str | None = None) -> str:
    """Exactly what crew's applier expects: the prefix line and ONE sql fence, nothing else."""
    sql = build_sql() if sql is None else sql
    return "crew-migration:\n```sql\n" + sql.rstrip("\n") + "\n```"


def main() -> int:
    sql = build_sql()
    if "--comment" in sys.argv[1:]:
        sys.stdout.write(comment_body(build_sql(lookup=True) if "--lookup" in sys.argv[1:] else sql))
        return 0
    if "--check" in sys.argv[1:]:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != sql:
            print(f"stale: regenerate {OUT}")
            return 1
        print("up to date")
        return 0
    OUT.write_text(sql, encoding="utf-8")
    print(f"wrote {OUT} ({len(sql)} chars; comment body {len(comment_body(sql))} of {COMMENT_LIMIT})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
