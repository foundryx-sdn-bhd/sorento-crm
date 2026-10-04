# PLAN: LLM-TEST-COST (cut test LLM spend: prompt size, cache, record/replay)

Status: implemented on branch `claude/llm-test-cost-kbwfi3` (PR #1476), awaiting review. Track:
small fix track (backend/test infra only, no migration, no auth/RBAC change, no owner-visible
behaviour). UAC: `llm-test-cost-4oct-acceptance-criteria.md`.

Lane: crew-lane LLM-TEST-COST, 4 Oct 2026.

## Goal

Cut OpenAI spend from testing without changing prod accuracy. No local model. Owner hand tests
stay on real gpt-5.4-mini. Prompt WORDING is not changed in this lane.

## 1. Prompt size: why a parse is ~33k tokens

Token counts below are OpenAI's own (`POST /v1/responses/input_tokens`, model `gpt-5.4-mini`,
free endpoint), not an estimate.

### Measured now

| Part | Tokens | Source |
| --- | --- | --- |
| System prompt as published (fallback + rendered policy blocks) | 32,419 | `ai_prompt_registry.render("chatbot_semantic_parser")` on a bootstrapped DB |
| of which the vendored fallback text | 31,644 | `app/services/chatbot_parser_prompt.py` `SEMANTIC_PARSER_PROMPT` |
| Strict output schema (`PARSE_OUTPUT_JSON_SCHEMA`) | ~1,850 | `app/services/chatbot/head/parser.py` |
| One real parse, end to end (short message) | **33,540 prompt tokens** | live call, see section 2 |

A real dealer turn adds its user block (memory layers, recent exchanges) on top, so 33-35k matches
the ~33k/parse seen on `chatbot.turns.trace` at prompt_version 56.

### Why it grew from ~10k (8-18 Sep) to ~33k

Two separate causes:

1. **The slim body was retired (22 Sep, #952, turn engine re-architecture).** From 8 to 21 Sep
   production ran the S1b *slim* body, 40% shorter than the live n8n body and proven against the
   replay corpus. With its addenda it was **10,821 tokens** (`alembic/_legacy_prompt_bodies.py`
   `SEMANTIC_PARSER_PROMPT_SLIM_V1`), which is the ~10k/parse in `ai_assistant_usage_logs`. The
   re-architecture went back to the FULL live body as the base (`chatbot_parser_prompt.py:694`,
   "RETIRED ... the slim body is no longer published from here"). That accounts for roughly
   +10k at once.
2. **Every feature since then added a vocabulary addendum** to the full body
   (`chatbot_parser_prompt.py:98-654`, appended at `:648-654`). Nothing has been removed since.

The vendored fallback's size at each commit that touched it:

| Date | PR | Change | Tokens | Delta |
| --- | --- | --- | ---: | ---: |
| 5 Sep | #659 | S0+S1: live n8n body vendored | 11,106 | |
| 7 Sep | #711 | warehouse-arrival cue | 11,357 | +251 |
| 8 Sep | #735 | growth r1 (`GROWTH_R1_ADDENDUM`) | 13,266 | +1,909 |
| 12 Sep | #844 | last purchase cost | 13,600 | +334 |
| 14 Sep | #862 | outstanding report | 14,916 | +1,316 |
| 14 Sep | #909 | low stock report | 15,194 | +278 |
| 19 Sep | #1034 | sales report | 16,929 | +1,735 |
| 22 Sep | #952 | re-architecture (stock task, specification, sales analysis blocks; slim body retired) | 20,802 | +3,873 |
| 27 Sep | #1247 | stock ask v2 | 22,745 | +1,943 |
| 27 Sep | #1269 | sales reports S1 | 23,350 | +605 |
| 28 Sep | #1324 | escalation confirmation | 24,618 | +1,268 |
| 28 Sep | #1321 | billing documents | 24,660 | +42 |
| 28 Sep | #1301 | the Samantha case | 25,340 | +680 |
| 28 Sep | #1273 | top X hot selling | 28,158 | +2,818 |
| 29 Sep | #833 | attribute-first asks | 28,991 | +833 |
| 29 Sep | #1353 | pick never overrides domain | 29,422 | +431 |
| 29 Sep | #1304 | contact memory | 29,665 | +243 |
| 29 Sep | #1373 | SPO / warehouse | 30,861 | +1,196 |
| 30 Sep | #1365 | self reference | 31,187 | +326 |
| 2 Oct | #1432 | account ledger | 31,644 | +457 |

Largest sections of today's fallback (tokens): preamble, the live n8n body plus woven edits 15,108;
SO / PO / SPO vocabulary (growth r1) 3,507; TOP SELLING 2,807; THE OPEN TASK (stock task)
2,137; SALES REPORT 1,724; ESCALATION CONFIRMATION 1,247; PO, SPO AND THE WAREHOUSE 1,152;
SPECIFICATIONS 813; CUSTOMER ACCOUNT NUMBER 714; SALES ANALYSIS 635; the rest under 400 each.

### Cache friendliness

- The parser call is `[system: prompt, user: user_block]` plus the strict schema
  (`head/parser.py` `parse`). The system prompt and schema are byte-identical across calls; the
  only variable inside the system prompt is `{{current_date}}`, which sits at character 125,341
  of 129,174 (97%), near the tail. The variable data (contact, history, message) is all in the
  last message. **Nothing needed reordering.**
- `tests/test_llm_cassette.py::TestStablePrefix` now pins both facts: the variable block is
  last and identical-prefix across calls, and two days' prompts share at least 95% as a common
  prefix.
- `cached_tokens` (`usage.prompt_tokens_details.cached_tokens`) now reaches the `understood`
  trace facts (`engine.py`, `facts.cached_tokens`) via `ChatResult.cached_tokens`
  (`llm_provider.py`) and the parser's usage dict (`head/parser.py`).

### Proposed trims (a separate ask, NOT done here: wording is the owner's)

1. Re-apply the S1b slimming method to the full body's 15k preamble (it took the live body down
   40% with replay-corpus parity once already).
2. Consolidate the worked examples in GROWTH_R1 (3.5k) and TOP SELLING (2.8k).
3. Move `CURRENT DATE` into the user block so the system prompt is fully static (only ~1k
   tokens after it today, so a small win; needs a parity run because position can change
   output).
4. Send `prompt_cache_key` (one per prompt version) so OpenAI routes parses to the same cache.

## 2. Measured numbers

Three live parses, real published prompt, gpt-5.4-mini, short fake messages (`record` mode, so
the same three then replayed):

| Run | prompt_tokens | cached_tokens | hit rate | latency |
| --- | ---: | ---: | ---: | ---: |
| live 1 | 33,540 | 30,976 | 92.4% | 2.8 s |
| live 2 | 33,540 | 33,024 | 98.5% | 1.7 s |
| live 3 | 33,539 | 33,024 | 98.5% | 1.9 s |
| replay 1-3 | same values | same values | n/a | 0.0-0.4 s, no network call |

- **Tokens per parse, before and after this lane: unchanged at ~33.5k** (no prompt edits, by
  ruling). What changes is what a test run PAYS: a replayed parse is 0 tokens.
- OpenAI's automatic prompt cache already serves 92-98% of every warm parse, so a live parse is
  billed mostly at the cached-input rate. Prod's own hit rate is now visible per turn in
  `understood.facts.cached_tokens`.

## 3. Record/replay (built)

`app/services/llm_cassette.py`, used by `OpenAIProvider.chat` / `.embed`
(`app/services/llm_provider.py`). `LLM_CASSETTE_MODE=off|record|replay`, default off, read per
call. Key = sha256 of (model, messages, response_format incl. schema, tools). A replay miss raises
`CassetteMiss` naming the key; `chatbot/llm_call.chat` raises it at once (it is not a rate limit,
timeout or transient). Anthropic and Gemini refuse in replay. Storage is `LLM_CASSETTE_DIR`, default
`~/.cache/sorento-llm-cassettes`. A directory inside the git checkout is refused. Files are 0600 in
0700 dirs, hold only the response, and a response containing the API key is not written. Nothing
is committed.

Known limit: the system prompt carries the date, so a cassette is valid for the day (and prompt
version) it was recorded on.

## 4. Runners (built)

- Harness launchers `documentation/plans/chatbot/evidence/lowstock-filter-ask/harness/run_backend_live.py`
  and `run_worker_local.py` start in replay; `--record` / `--live` to spend
  (`llm_cassette.configure_runner`).
- `scripts/chatbot_console_check.py` reads `understood.facts.llm_cassette` off each turn and stops
  after the first live parse unless `--live`.
- Documented in `documentation/agents/cloud-lanes.md`.

## 5. Optional override (built, set nowhere)

`LLM_BASE_URL` reaches the OpenAI client constructor, and `LLM_MODEL_OVERRIDE` replaces the model on
the wire. Each applies only when set.

## Direct-HTTP calls outside the provider seam

Raw-httpx embeddings (`embedding_worker._embed_text_chunks`, `api/v1/external/rag.py`), Whisper
(`media_extract/transcribe.py`) and the SCM market-research Anthropic call do not go through
`OpenAIProvider`, so nothing records them. In replay each one refuses (`refuse_uncovered`)
instead of spending (security review round 1, S3). The cost of that: in a replay run the
entity resolver's Tier-3 fallback (`entity_resolver.py`, `_memo_embedding`) skips (it already
catches and returns no match), and background indexing of new rows fails. Run `--live` when a
pass depends on either.

## Security review round 1 (applied)

- B1: the console check read an inline trace the `/turn` body never carries; it now reads the
  stored row (`_row_trace`).
- S1: a cassette dir inside ANY git checkout (another clone, a worktree's primary) is refused.
- S2: `LLM_CASSETTE_MODE` other than `off` raises when `ENVIRONMENT=production`.
- S3: the direct-HTTP calls above refuse in replay.
- S4: `LLM_BASE_URL` must be https (http only on localhost), since the key travels there.
- N1: an existing cassette dir must be owned by this user with mode 0700.
- N2 (retention): a cassette is good for one day, so clear old ones with
  `find "${LLM_CASSETTE_DIR:-$HOME/.cache/sorento-llm-cassettes}" -name '*.json' -mtime +7 -delete`.
