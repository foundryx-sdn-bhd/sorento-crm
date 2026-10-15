# PLAN: PV2 R5 "follow-ups" - the code acts on what the parser read (15 Oct 2026)

Status: Plan. Track M (LEAD pattern, red-first, kill-proof). Branch `crew/pv2-redesign-r5` from 380b6cdf4 (R4a head,
PR #1691 unmerged), worktree sorento_crm-pv2-fix2. PR stacked on #1691 (base `crew/pv2-redesign-r4a`) so the diff
shows R5 only; it retargets to crew/pv2 once #1691 merges. Owner TOP priority 15 Oct (brief
crew/state/briefs/PV2-FIX-R5.md). Restarted after a context overflow: nothing of the first attempt was committed.
UAC: `pv2-r5-followups-acceptance-criteria.md` (beside this file). Parent plan: `PLAN-pv2-redesign-14oct.md`.

## Why

Crew's analysis of the R4a 3x battery (TRAIN classes only, anti-overfit rule): in most failing turns the parser read
the message right and PV2's own code dropped it. R4a let the parser SEE the printed rows; the code does not ACT on them.

## Behaviours (each tested directly from a parser output, never from battery wording or case ids)

1. Number / ordinal pick from the bot's own list ('2', 'no 2', '第二') acts on that row; the same list is never re-shown.
2. 'this product' / 'this one' after a one-item order or DO resolves to that line's product; several lines = ask with
   the real product names.
3. Account pick after the bot's confirm or account menu (e.g. 'A/C I only') is applied; the same confirm never loops.
4. Promotion copy pick (dealer copy / end-user copy) sends that copy.
5. Question kind survives a pick: an arrival / delivery-date question stays one; no fallback to 'Sales orders or
   delivery orders?' or 'need at least one filter' when the kind is already known.
6. Reply language: the typed language wins; a language-neutral reply keeps the conversation language.
7. Reply-build guard: a reply never prints a UUID or an internal id.

Owner product rules (15 Oct) still apply: a named customer carries when a product is named next; the clarify is only
product -> customer; staff get stock numbers, dealers got / no stock; promo replies send the list, never echo the code;
size ask -> size or 'none on record'; one exact option is used, a guess is a yes/no confirm with the real name.

## Code map (at 380b6cdf4)

(filled from the mapping pass; file:line at 380b6cdf4)

## Work split

- Red tests: one TESTER subagent per behaviour group, from the UAC only, each with >= 1 fresh paraphrase not in the
  battery; commit `test(red): ...` alone, run, post red-proof.
- Fix: one CODER subagent (Opus: state-machine round) for the whole round, continued not respawned.
- Lead: kill-proof on each committed fix, touched-set run, reviewer + security-reviewer once per round.
- Commit after every behaviour (red commit, then fix commit).

## Touched-set (pv2 apply / engine changes)

All tests/chatbot/pv2/test_redesign_r*_*.py + test_rgaps_* + test_r4a_* + the three replay files + test_carry_safety.py
+ the DB-backed test_fix_r2*.py + tests/chatbot/test_promo_*.py + the new test_r5_*.py, pytest -n 2.

## Gates

Touched tests green, reviewer READY, security CLEAN. Prompt changes only if a behaviour needs one: then ONE dev-only
unlabelled version via pv2/index.publish_v2(label=None) on sorento_cagent_stack, id + md5 posted. Then
`crew report --done`; crew runs the 3x battery.
