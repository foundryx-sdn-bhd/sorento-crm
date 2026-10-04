# UAC: release blocker test_sec_1a

- AC1: `tests/test_ingest_parity_security_fixes.py` passes on the branch (was 1 failed / 4 passed on 60c57c7a3).
- AC2: after a first-push supersede of a ref-less xlsx row holding received 5, the live AutoCount
  line holds received 5, and the live rows' total received is exactly 5.
- AC3: the retired row is closed, received 0, and `stated_received >= 5` (receipt recorded, not erased).
- AC4: kill tests: removing the carry-forward, or skipping the `stated_received` freeze, turns the test red.
- AC5: ingest-area and tests/scm suites run locally with no new failures versus main.
