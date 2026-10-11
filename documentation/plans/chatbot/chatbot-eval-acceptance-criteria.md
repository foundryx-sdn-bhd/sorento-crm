# UAC: CHATBOT-EVAL

1. No case data in git: the runner refuses a data dir inside the repo; tests use generated fake cases.
2. A case loads only if it has id, split, message, expect.outcome; invalid cases are listed, not skipped silently.
3. Each turn runs as a dry run (`is_test` true, a `test_run_id` per run); a response without the dry-run
   stamp aborts the run.
4. Multi-turn cases replay history user turns in order, carrying `session_patch`; only the final turn is graded.
5. Deterministic grade: a required code/number/date missing -> wrong; a listed must_not hit -> wrong;
   tool error in trace -> error; reply equal to the previous bot reply -> stuck; clarify listing the
   required options -> good-clarify; alternates honoured.
6. Judge runs only when deterministic checks pass and a judge_note exists; judge scores are ignored
   unless calibration agreement >= 90%.
7. Report: verdict counts per topic and language; underfit (originals pass rate); overfit gaps
   dev-minus-hidden and originals-minus-variants; tokens, est. USD, p50/p95 latency, wall time.
8. Budget: the run stops before the next turn once estimated spend passes `--budget-usd`; the report
   says "stopped: budget".
9. Engine pluggable: `--engine module:Class` runs the same cases through another engine.
10. Baseline delta: with `--baseline`, the report shows per-category and total delta.
11. Hidden output: a hidden-split run's printable summary carries aggregates only (no message text).
