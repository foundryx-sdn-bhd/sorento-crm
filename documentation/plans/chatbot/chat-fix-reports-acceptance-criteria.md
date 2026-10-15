# UAC: chat fix PR-B, report engine

Plan: `PLAN-chat-fix-reports-16oct.md`. "Report ask" = a sales ranking / sales total / top selling ask answered by
the report engine. Chain numbers (#n) refer to the 25 real console chains (3-14 Oct) of the report slice.

- AC-B1 A report ask missing more than one detail gets ONE reply listing every missing detail, numbered, each with its
  choices; no detail gets a default (4 Oct ruling). One missing detail is asked alone, as today.
- AC-B2 One answer can settle several details: "2026, top 10, by quantity delivered" settles period, count, metric
  and basis and runs the report (#8). Every detail is read from the parser's fields, never from the message words.
- AC-B3 A partial answer is not a miss: the reply re-asks only the details still missing, and a later answer
  completes it.
- AC-B4 An answer that says "delivered" (or "ordered") fills the basis and runs the same report; it never switches to
  the delivery-order list (#23).
- AC-B5 With a report on screen, a follow-up the parser marks as a refine changes only what the parser changed (e.g.
  the channel) and re-runs that report, for every report type, including a report that came from a multi-ask
  message (#23).
- AC-B6 A follow-up naming another axis ("the top customer") is still asked (8 Oct rule), but the held period and
  product stay and are not asked again (#22).
- AC-B7 A sales total that names a brand runs filtered to that brand, or says it cannot, never an unfiltered total
  presented as the brand's (#15).
- AC-B8 A ranking reply says how many rows exist ("Top 10 of 37"), names the sort measure first, shows each name once,
  shows dates as readable dates, categories by name, and month rows in date order (#14).
- AC-B9 When the bot asks which company ("Sorento or Mocha?"), the next reply naming one answers that question and
  runs the held analysis for it; it is never read as a new or casual message (#24, second half).
- AC-B10 (config, no code) The report limit is the owner's setting; no client-sent flag can skip it (#13).
- AC-B11 A customer picker whose accounts all carry the same answer (e.g. all "no DO") gives that answer directly
  instead of asking which account (W02).
- AC-B15 A reply that fills the open question's missing detail is its answer even when the parser tags it with another
  report type ("delivered" under the top selling basis question runs the top selling list); a reply that fills no
  open detail and reads as a new report ask is a new ask, never "I don't know ... as a basis".
- AC-B16 A sales report ask with no product and no customer never ends in "could not fetch": for staff it answers the
  company's sales for the period; a customer-scoped contact keeps its own customers as today.
- AC-B12 Parser input unchanged: the rendered parser input for the slice's asks is byte-identical to main (same
  prompt version).
- AC-B13 No regression: no chain right on main goes wrong on PR-B; walkthrough rows not written for are the same or
  better; existing report suites stay green.
- AC-B14 No word rule over the user's message is added anywhere (reviewer checks the diff); "ask all at once" is
  opt-in for the report ask only, other required-field asks keep one question per turn.
