# PLAN: unified product-code resolution, canonical_code first, semantic fallback on a miss

Status: Plan, behaviour card put to the owner (crew-ask on the PR), code not started. Track: size M, standard pipeline (red-first tests, reviewer, security-reviewer because a new LLM call path and the API-key `/resolve` route are touched). Crew lane RESOLVE-SEMANTIC-FALLBACK.
UAC: `resolve-semantic-fallback-06oct-acceptance-criteria.md`
Owner report (6 Oct 2026 20:02 MYT, production, 15-line stock check): the line "TAP SRTWT5608 x 5" came back "I could not find TAP SRTWT5608" although SRTWT5608 exists. The parser emitted ONE product entity raw "TAP SRTWT5608" (qty 5, confident, canonical_code null) and every resolver tier matches a token only as exact code, prefix of a code or substring of a code.
Owner ruling: "I need this behaviour in any place that resolves, it should be a unified behaviour; agree with the semantic fallback." No fuzzy, typo or word-shape matching in code; the parser decides; a semantic fallback is fine.

## Journey

A dealer types a stock check with a type word beside the code ("TAP SRTWT5608 x 5", "WC SRTWC8066-SS", "basin tap MWT5727SS-CR"). Today the whole typed phrase is the resolver token and it misses. After: the parser puts the bare code in `canonical_code`, the shared resolver looks that up first, and the stock reply names SRTWT5608. When the parser did not split it, the resolver asks the parser model one narrow question about that token alone and retries with the code it names, or offers the code family it names as the existing picker, or says the existing not-found line. The same happens for the MCP resolve tool and for an API-key `POST /resolve`, because the fallback lives in the one shared function.

## Where resolution happens today (origin/main e3ea227e, verified)

- Shared resolver: `app/services/entity_resolver.py::resolve_references` (:4920). Tier 1 exact (:5050), Tier 2 prefix / substring (:5080), head-code retry on `raw_tokens` (:5147), Tier 3 embedding (:5260), trigram did-you-mean alternatives (:5322).
- Route: `app/api/v1/system/references.py::resolve_reference_post` (:2788) over `_resolve_input` (:2068). The chatbot calls the ROUTE function in process (`lanes/business/services.py::_resolve_entity` :140), the MCP server and n8n call it over HTTP.
- Token order, canonical first: `resolve_gate._token_of` (:508), `_names_a_typed_code` (:500), `turn/apply._code_of_entity` (:115).
- Token order, raw first (to unify): `lanes/business/__init__.py` :1095, :1250, :1261, :1722; `engine._exact_codes_only` (:554).
- Parser prompt: `app/services/chatbot_parser_prompt.py` entity shape says `canonical_code` is "usually null; REQUIRED for attachment_type; OPTIONAL for attachment; else null". `QUANTITY_ADDENDUM` (:241) strips the count out of raw; `KNOWN_BRANDS_ADDENDUM` (:256) fills canonical_code for brands. No rule for a type word in front of a code.
- Parser eval harness: `scripts/chatbot_console_check.py` with YAML cases under `tests/chatbot/console_cases/` (`expect.parser.entities: [{canonical_code, hint, current_message}]`, `live_only: true`). Live runs happen on the owner's Mac through crew; this sandbox has no parser key.
- Prompt promotion: an addendum is a new UNLABELLED `chatbot_semantic_parser` version published by a migration (`alembic/versions/chatbot_known_brands.py` pattern); the owner moves the `production` label by hand.

## Behaviour (to be confirmed by the owner's answers to the card)

1. Parser prompt addendum: a product entity whose typed text carries a type / colour / brand word beside a code gets `canonical_code` = the bare code, `raw` unchanged, the word kept as a hint. Proven by a parser eval case file (real dealer lines, expected canonical_code per line), red before the addendum, green after.
2. Shared resolver, canonical first: every chatbot site reads `canonical_code or raw` in that order.
3. Semantic fallback on a miss only, in `resolve_references`: a product token that resolved to nothing in every code tier gets ONE narrow structured question to the parser model (token, message line, nearest code families the resolver found, max N candidates) answering `code | family | none`. `code` retries the code tiers with that code; `family` is handed back for the existing picker; `none` keeps the existing not-found line. Bounded by a per-call timeout and a per-message cap; never called on a clean message; cassette-backed in tests.
4. Unified: the chatbot turn, the MCP tool and `POST /resolve` all get it through the shared function; caller-facing field names stay stable.

## Tests (written first)

To be listed once the card is answered.

## Out of scope

Frontend (none). Fuzzy / typo matching in code (owner rule).
