# PLAN: the resolver answers every pre-extracted product code, and names any it cut

Status: small fix track, implemented, reviewer VERDICT: PASS on head 2b54841f (round 2) and on the wording head, PR #1507 (crew lane RESOLVER-TOKEN-CAP)
UAC: `resolver-token-cap-06oct-acceptance-criteria.md`
Owner rulings (6 Oct 2026, via crew): accepted as built. No cap on the "Not checked:" line (it names every cut code, up to 50 on one line, no "+N more"). No rate limit on the API-key `/resolve` route (up to 50 pre-extracted lookups per call, so up to 50 embedding calls per request with the fallback on).
Owner report (6 Oct 2026 18:57 MYT, production, staff contact): a catalogue photo read 19 product codes, the parser carried all 19, and "Stock summary for the requested products." listed only the first 8. Owner: "why only 8?"

## Journey

A staff contact sends a photo of a catalogue page. `media_intake` reads 19 codes, the parser carries all 19 as product entities (inventory / check_stock). Today the stock reply lists the first 8 in media order and says nothing about the other 11. After: every one of the 19 is looked up and answered (a stock row, "No stock found for X", or the family's did-you-mean), and if a message ever names more codes than the resolver looks up in one go, the reply says which ones it did not check.

## Defect (`sorento_crm_backend/`)

`app/services/entity_resolver.py::resolve_references` cut a pre-extracted token list to `max_candidates` (default 8, the FREE-TEXT extraction count) at `tokens = tokens[:max_candidates]`, and the cut tokens appeared in neither `resolutions` nor `unresolved_tokens`. The chatbot's `turn_runtime.unplaced_tokens` reads `unresolved_tokens`, so nothing downstream could name them: silent loss, the same class as #1501 and #1502. The route (`api/v1/system/references.py::_resolve_input`) never overrode the default. Dev replay: 19 tokens gave exactly 8 resolutions.

Not the cause: `_LOOKUP_LIMIT=50` is per token (matches per token); the stock balance page is 50 rows; zero-stock named products are not dropped by the compact renderer; `media_max_entities` (System Settings, default 10, max 100) already names its own cut ("There was more than I can handle in one go, so I have taken the first N.", `media_extract/wording.truncated_note`, carried on `result.truncated` and prepended by `media_intake.reply_prefix`), so the 19 reached the parser because the setting is above 10 on production.

## Fix

1. `entity_resolver.py`: `PRE_EXTRACTED_TOKEN_CEILING = 50`. The ceiling is the stock balance page's own default `limit` (`api/v1/inventory/stock.py::get_stock_balance`) and the chatbot's `_LOOKUP_LIMIT`, so every code the resolver answers has a row the renderer can print. `max_candidates` becomes `Optional[int] = None`: unset, free text extracts 8 as before and a pre-extracted list is looked up to the ceiling; an explicit cap is honoured either way (`ai_extract.extract_service` passes `len(chunk)`, up to 100). The remainder is `ResolutionResult.truncated_tokens` and `as_dict()["truncated_tokens"]`, never dropped in silence.
2. `turn_runtime.py`: `not_checked_tokens(entities, resolved)` joins `truncated_tokens` to the words the customer typed exactly as `unplaced_tokens` joins `unresolved_tokens` (separator-folded `token_key`); `ResolveOutcome.not_checked`; `make_tool_runner(not_checked=)` to `envelope_of(not_checked=)`, merged onto the task's own `spec.filters["not_checked"]`. `engine.py` threads it. The reply uses the existing "Not checked: X, Y." line (`turn/compose.py`), said once per turn: a cut token is not unplaced (it may well exist), so it is never "I could not find".
3. Route: the one free-text path that synthesizes a token list (`_synthesize_alpha_tokens`, alpha-only types such as `attachment_type` with no `tokens` sent) keeps the free-text count of 8 (reviewer S1): nobody chose those words.

No new typo, word or fuzzy matching; the parser decides what is a code; fake ZZT fixture ids only.

## Tests (written first)

`tests/test_resolver_token_cap.py` (Postgres, 60 seeded ZZT codes): 19 tokens answered in order; 60 give 50 answers plus the 10 cut in `truncated_tokens`, none mislabelled unresolved; explicit cap raised (60) and lowered (3) honoured; 1, 3 and 8 token controls; free text keeps 8 (default and explicit); the alpha-token route path keeps 8. `tests/chatbot/test_resolver_token_cap_reply.py`: the join, `ResolveOutcome.not_checked`, the envelope merge, compose (19 codes each print; the cut named once; once per turn across two sections, the task's own dropped slot included), and two end-to-end engine turns with the real resolver (60 codes name the 10 not checked; 19 codes all reach the tool, `total_uuids_passed: 19`, which read 8 before the fix).

## Known follow-ups

- A pinned token past the ceiling fails the whole call (`_apply_entity_pins` raises `EntityPinMismatch`) rather than being reported as not checked; pre-existing at 8, now needs more than 50 pinned codes.
- Duplicate tokens are not folded before the cut; a code repeated past position 50 would be listed as not checked although its first copy was answered.
- Cut codes are named only through compose. A turn that exits before any fetch (a resolver clarify or pick ask, or a turn with no envelopes) does not mention them; at the old cap of 8 those codes were mentioned on no turn at all. The same join limit `unplaced_tokens` has applies: a variant code produced by `narrow_code_tokens` and then cut matches no typed raw word, so `not_checked_tokens` cannot name it.
- The entities-only arm (`engine.py::_run_entities_only_arm`, bare codes with no domain) keys each typed raw against the resolver's `resolutions` and does not read `truncated_tokens`: above 50 raws the cut ones print under "Couldn't find" (wrong sentence, not silent) instead of "Not checked". Owner evidence 20:06 MYT 6 Oct (15 codes, "Couldn't find" = exactly the raws past position 8) was this arm on the old cap; the ceiling fixes it, the sentence above 50 is the follow-up.
- The route's synthesized alpha-word path (`_synthesize_alpha_tokens`) sends a list capped at 8 and so reports the words past 8 in `truncated_tokens`; no caller reads it on that path today.

## Out of scope

Parser prompt, media extraction prompt, frontend (none). Browser pass not needed (no screen changes). The `ci` label is the crew's.
