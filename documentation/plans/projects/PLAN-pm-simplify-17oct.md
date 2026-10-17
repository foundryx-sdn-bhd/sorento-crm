# PLAN: simplify the project PO and delivery schedule parts (PM-SIMPLIFY)

Status: discovery (17 Oct 2026). No code until the owner OKs the mock. Track: not chosen yet
(decided by the diff once the mock is approved).

Owner 15 Oct: "the PO part and the delivery schedule part are badly designed, too complicated ->
simplify." This is a new round on top of the shipped 24 Sep redesign
(`PLAN-po-schedule-redesign-24sep.md`, slices S1-S7, #1219 #1237 #1264 #1265, later #1618 #1681).

## Steps

1. Discovery: walk create PO from a project, plan and change delivery dates, partial deliveries,
   see what is outstanding. Count screens, clicks and fields per job, screenshot every step.
2. Proposal: fewer steps and screens, existing components only. Mock v1 on the real case with an
   element -> component map (repo paths), at most 5 questions, one report to the owner.
3. After the owner OKs the mock: behaviour card if rules change, red-first tests, reviewer,
   security review, browser pass, hand-test script.

## Discovery findings

(in progress)
