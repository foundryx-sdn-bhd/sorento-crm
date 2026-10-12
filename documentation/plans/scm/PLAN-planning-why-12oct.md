# PLAN: Planning "why" trail - why was this cancelled, why can I not confirm

Status: Plan (behaviour card + Lavish mock pending owner). Track: standard (Size M, LEAD pattern).
Lane: PLANNING-WHY, branch `crew/planning-why`.
UAC: `planning-why-12oct-acceptance-criteria.md` (written after the card is answered).

## Owner ask (12 Oct 2026)

"I also cannot check, I cannot see the log in a nice way from prod; think about the user
experience to ease our load and the user load in checking."

Trigger: SO422492 Confirm showed only "0 of 1 orders confirmed / SO422492: Nothing on this
planning change could be confirmed." and purchasing got CANCEL emails nobody could explain.

Goal: a CS user or the owner answers "why was this cancelled?" and "why can I not confirm?" in
under a minute, without crew.

Out of scope: the wrong CANCEL ALL / under-order bugs themselves (lane OI-CANCEL-BUGS).

## Measured facts

(filled in from code research; every claim cites file:line)
