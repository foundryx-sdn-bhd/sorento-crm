# UAC: RESOLVER-TOKEN-CAP (small fix track)

Plan: `PLAN-resolver-token-cap-06oct.md`. Resolver ACs are pinned by `sorento_crm_backend/tests/test_resolver_token_cap.py`, reply ACs by `sorento_crm_backend/tests/chatbot/test_resolver_token_cap_reply.py`.

- AC-RTC-1 [resolver] 19 pre-extracted product tokens sent the way `resolve_gate.resolve_entity_body` sends them (OR mode, one `product` hint per token) come back as 19 resolutions in order, each matched to its own code, with `unresolved_tokens` and `truncated_tokens` both empty.
- AC-RTC-2 [resolver] 60 such tokens come back as the first 50 resolutions in order plus `truncated_tokens` equal to the last 10 in order; no cut token is in `unresolved_tokens`.
- AC-RTC-3 [resolver] `PRE_EXTRACTED_TOKEN_CEILING` is 50, the stock balance page's default `limit` and the chatbot's `_LOOKUP_LIMIT`.
- AC-RTC-4 [resolver] An explicit `max_candidates` stays the caller's: 60 tokens with `max_candidates=60` are all answered with nothing truncated; 5 tokens with `max_candidates=3` answer 3 and report 2.
- AC-RTC-5 [control] 1, 3 and 8 token lists answer exactly as before with nothing truncated; a free-text query still extracts at most 8 tokens (default and explicit cap) and never reports a cut; the route's alpha-token path (`attachment_type` hint, no `tokens`) still probes at most 8 synthesized tokens.
- AC-RTC-6 [reply] The resolver's cut tokens are joined to the words the customer typed with separators folded (token "ZZTCAP51GY" names raw "ZZTCAP51-GY"); no truncation means nothing is not checked; `ResolveOutcome.not_checked` carries the map beside an empty `unplaced_tokens`.
- AC-RTC-7 [reply] `envelope_of` carries the cut codes on `not_checked` after the task's own dropped slots, each once; an envelope without a cut is unchanged (`[]`).
- AC-RTC-8 [reply] A stock reply over 19 answered codes prints every one of them and neither "Not checked" nor "I could not find"; a reply over 50 answered codes plus 10 cut ends the section with "Not checked: <the 10, in order>." said once, and never "I could not find" for them; a two-section reply names the same cut codes once.
- AC-RTC-9 [journey] Through `engine.run_turn` with the real resolver on 60 seeded ZZT codes, the reply contains "Not checked: <codes 51 to 60>." and no "I could not find" or "Couldn't find"; on 19 seeded codes the tool call carries 19 uuids (`_diagnostics.total_uuids_passed`, 8 before the fix) and the reply has no "Not checked".
- AC-RTC-10 [rule] No new typo, word or fuzzy matching: the fix only lifts a cap and reports what was cut; the parser prompt and the media extraction prompt are untouched.
