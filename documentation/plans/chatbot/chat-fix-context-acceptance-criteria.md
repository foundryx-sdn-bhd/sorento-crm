# UAC: chat fix PR-C, follow-up context

Plan: `PLAN-chat-fix-context-16oct.md`.

- AC-C1 On a follow-up, the parser's input keeps the current subject (the product, customer or document the
  conversation is about) even when the brand / product type / product detail / promotion lists are all present; the
  subject is shortened only when the subject, task, open question and pending lines alone exceed the layer's cap.
- AC-C2 After the user picks from a list, the parser sees that list as answered (nothing owed, the picked options
  named) and no "waiting for a reply" line; the list and its options stay, so a second pick from it still picks number 2.
- AC-C3 A list that was not answered yet is shown exactly as today (owed, waiting line).
- AC-C4 A first message's parser input is byte-identical to main.
- AC-C5 Parser gold TRAIN follow-ups: at least 3 more right than main on both runs per slice, and no item right in
  both main runs is wrong in both new runs; first asks unchanged.
- AC-C6 No regression on the PR-B report chains and the walkthrough rows.
- AC-C7 No new prompt line, no word rule, no state change (the sticky roster contract 36 tests stay green).
