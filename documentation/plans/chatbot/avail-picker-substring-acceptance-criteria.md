# UAC: availability picker for a substring code token (AVAIL-PICKER)

Plan: `PLAN-avail-picker-substring-05oct.md`. Contact: availability access (stock check
mode `availability`). Catalogue: the SRTWC286-SH family (10 codes), SRT5674, SRTWC287-S-150.

- AC-1 "wc286 check stock" replies with the family picker: a header naming the typed token
  and the count, then one numbered code per line (every SRTWC286-SH-* code), and leaves a
  `product_pick` question open. No "Couldn't find", no "But no inventory matched these".
- AC-2 "wc286 x 5": the same picker with "WC286 x 5: which one?"; a number reply answers
  that code at x 5 through the real stock decision.
- AC-3 A reply "2" to the AC-1 picker asks "How many units of <code 2>?" (the picked code
  still owes its quantity).
- AC-4 A substring of exactly ONE code ("tr5674" over SRT5674 alone) answers as that code
  with no picker; with a quantity ("tr5674 x 3") the quantity goes to that code.
- AC-5 An exact code stays exact: "SRTWC286-SH x 3" answers SRTWC286-SH alone, never its
  family (S-series regression, `_exact_codes_only` rule 1 unchanged).
- AC-6 A prefix token keeps its prefix reading: "srtwc286 x 10" still gives the S09 picker.
- AC-7 A code-like token with no prefix and no substring hit ("FOO99 x 1") still reaches
  the did-you-mean / not-found path (S11, S12 unchanged).
- AC-8 Full mode (compact / detailed staff contacts) is unchanged by this lane.
- AC-9 `pytest tests/chatbot/test_avail_mode_scenarios.py` and the engine unit tests are
  green; the new scenario is a row of `tests/chatbot/AVAIL-MODE-SCENARIOS.md`.
