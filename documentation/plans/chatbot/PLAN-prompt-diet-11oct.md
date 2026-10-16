# PLAN: PROMPT-DIET (step 1 of ARCH-scalable-chat option D = option E)

Status: stopped at DP1 (11 Oct 2026, crew per owner exit ramps); PR #1568 is a draft measurement record, switch stays off. Track: M, lead pattern. Additive migration (one new table, two prompt keys).
UAC: `prompt-diet-acceptance-criteria.md`. Architecture: `ARCH-scalable-chat-11oct.html` (PR #1566).

## Goal

Replace the one static parser prompt (v50, 37,916 tokens, 87 sections) with: stable core + cards for the
candidate areas only + 3 to 5 retrieved worked examples. Today's pipeline, no tool change, output schema
(54 keys) unchanged. Behind `CHATBOT_PROMPT_DIET` (env, default off). Static prompt stays the fallback.

## Rulings (owner 11 Oct; crew 11 Oct on the plan card)

- Way 2: embedding retrieval picks candidate areas; areas pinned by state are always added.
- Missing area: read off the parser's own output (domain_hint, asks[].domain, sales order_status); one
  re-call with the missing card(s). No new output key.
- Examples: ~20 synthetic seeds in the repo; DEV-derived examples loaded at run time from the crew dir by a
  script (no case text in the repo). Never hidden cases.
- Lane-only env switch this step; DB setting at promote.
- Move-only cut (no rewording) now; a trim round after DP1.
- Cut from v50 (baseline pin). The PR notes v49 (prod label) vs v50 differences.
- Rules: no regex/keyword rules on user text; no code fuzzy matching; MCP untouched; never stuck.

## Lead decision (technical, crew decides)

Prompt text lives in the prompt registry, not in `chatbot_domains` columns: two new versioned keys,
`chatbot_parser_core` and `chatbot_parser_cards`. The cards document holds every card under a marker line
`<<card:NAME>>`. Reason: prompt text is already versioned, labelled and owner-edited there (Prompts
screen); a revert is a label move; and `chatbot_domains` rows drive routing policy, so pseudo-areas
(open_question, escalation, account) must not become rows there. `chatbot_domains` is unchanged.

## Cut (move only, from v50; measured)

| Goes to | Sections (v50 start line) | Tokens |
| --- | --- | --- |
| core | 0, 9, 14, 24, 58, 76, 196, 299, 309, 324, 368, 400, 415, 421, 453, 511, 514, 517, 541, 567, 583, 589, 719, 735, 742, 963, 1089, 1145, 1358, 1380, 1927, 2072 + area list | ~10.8k |
| replaced by the area list in core | 98 DECISIVE DOMAIN TERMS | 1,680 |
| card open_question (state) | 848, 1158, 1179, 1196 | 2,794 |
| card escalation (state) | 602 | 767 |
| card account (companion of order and sales) | 1573, 1742 | 3,069 |
| card master_products | 238, 456, 942, 978, 1308 | 3,147 |
| card sales | 1033, 1059, 1106, 1262, 1280, 1286, 1292, 1410, 1428, 1432, 1439, 1445, 1456, 1489, 1499, 1510, 1551, 1820 | 7,020 |
| card order | 636, 793, 804, 812, 822, 898, 975, 1697, 1726 | 2,761 |
| card promotion | 339, 346, 675, 1970, 1978 | 2,663 |
| cards resource_attachment / purchase_order / spo_allocation / purchase_cost / inventory / ideate / product_attachment | 688 / 915, 954, 1686 / 932, 1676 / 986 / 1007 / 219 / 327 | 2,640 |

The DECISIVE DOMAIN TERMS section is NOT deleted: it is kept verbatim at the end of core in this step
(move only), and the one-line area list is added before it. Its removal is part of the trim round.

## Contract (pinned for tester and coder)

Module `app/services/chatbot/head/prompt_diet.py`; data files `app/services/chatbot/head/prompt_diet_data/
core.txt` and `cards.txt` (the fallback texts and the migration seed; produced by `scripts/prompt_diet_cut.py`
from a v50 text file, run once, output committed).

- `enabled() -> bool`: `settings.chatbot_prompt_diet` (env `CHATBOT_PROMPT_DIET`, default False).
- `parse_cards(text: str) -> dict[str, str]`: splits on lines `<<card:NAME>>`; text before the first marker
  is ignored; names unique.
- `COMPANIONS = {"order": ("account",), "sales": ("account", "order")}`; `STATE_CARDS = {"open_question",
  "escalation"}`.
- `pinned_areas(*, focus_domains, pending_kind, open_question, tasks) -> set[str]`: focus domains; the
  `open_question` card when an open question, pending pick/offer or task exists; `escalation` when the
  pending kind is an escalation offer (kinds per `turn/pending.py:13`).
- `nearest_examples(db, vector, k=5) -> list[Example]`; `Example(area, message, context, expected_output)`.
  pgvector cosine order; `enabled = true` rows only.
- `select_cards(*, retrieved_areas, pinned, available) -> list[str]`: union + companions, filtered to
  names present in `available`, ordered by a fixed `CARD_ORDER` (state cards first, then areas by
  chatbot_domains sort order) so the prompt prefix is stable.
- `build_system_prompt(core, cards: dict[str, str], selected: list[str], examples) -> str`: core, then
  each selected card in order, then an examples block; deterministic.
- `needed_areas(parsed: dict) -> set[str]`: `domain_hint`, every `asks[].domain`, plus `sales` when
  `order_status` is one of sales_report, sales_analysis, top_selling, sales_ranking.
- `missing(parsed, loaded) -> set[str]`: `needed_areas(parsed) & set(cards) - loaded`.
- Engine (`engine.py` around :4371 / :4438): when `enabled()` and not a harness-bypassed parse: embed
  (message + previous reply) with the configured embedding provider, retrieve, build, parse; if
  `missing` is non-empty, rebuild with those cards and parse ONCE more; use the second emission. Any
  exception in retrieval / build -> the static prompt (today's path), recorded on the trace. Tokens of both
  calls are summed into `parser_usage`.
- Trace (`understood` facts): `prompt_diet: {on, cards, pinned, examples: [ids], missing: [..], recalled:
  bool, fallback: reason|None}`; `prompt_tokens` / `cached_tokens` as today (summed).
- `prompt_cache_key`: `chatbot_parser_core:v{core_version}:{'+'.join(selected)}`.
- Table `chatbot_examples(id uuid pk, area text, message text, context text null, expected_output jsonb,
  embedding vector(1536) null, source text ('synthetic'|'dev'), enabled bool default true, created_at)`.
  Migration seeds ~20 synthetic rows (fake names and codes) without embeddings; rows with a null embedding
  are skipped by retrieval until `scripts/prompt_diet_seed_examples.py --embed` fills them.
- `scripts/prompt_diet_seed_examples.py`: reads `--data-dir` (crew dev dir, refuses a path inside the
  repo, refuses `hidden`), masks customer names, writes a JSONL + idempotent SQL for crew to load, with
  embeddings; `expected_output` = the v50 parser emission on that case when it is supplied.

## Slices

S1 cut script + data files + registry keys + migration (table, seeds) + module + engine hook + trace.
S2 examples script; DEV runs (crew runs them on the eval DB if lane turn rows are not allowed there).
S3 report for DP1: per topic pass / strict vs 78.0 / 34.6, prompt tokens per turn, p95, missing-area rate,
cache hit rate, cost per turn.

## Not doing

No wording change to any section, no schema change, no tool change, no UI, no n8n, no DB setting.
