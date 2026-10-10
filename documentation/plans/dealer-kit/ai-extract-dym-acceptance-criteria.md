# UAC: AI Extract did-you-mean (AI-EXTRACT-DYM)

Plan: `PLAN-ai-extract-dym.md`.

## Backend

- AC-B1 A line whose code has no exact match but has trigram neighbours in scope carries
  `suggestions`, best first: SRTK1634SS-BL lists SRTKT1634SS-BL first.
- AC-B2 A suggestion is never applied: the line keeps `match = null`, `product_id = null`, and the
  raw extracted `product_code`.
- AC-B3 An exactly matched line carries `suggestions = []`.
- AC-B4 At most 3 suggestions per line.
- AC-B5 A product of another company is never suggested.
- AC-B6 An inactive product is never suggested.
- AC-B7 A miss with no neighbour above the floor carries `suggestions = []`.
- AC-B8 A resolver failure still answers every line as "no match", with `suggestions = []`.

## Frontend (price tag request portal, AI Extract dialog)

- AC-F1 A not-found row with suggestions shows "Not found" and a "Did you mean?" picker listing
  `CODE - name`; nothing is selected.
- AC-F2 Apply without picking: the row is not added and is named in the "Not found" toast.
- AC-F3 Pick a suggestion, then Apply: a line for the picked product is added (code and name of
  the picked product), and the code is not in the "Not found" toast.
- AC-F4 Picking shows the row as matched before Apply; clearing returns it to "Not found".
- AC-F5 A not-found row with no suggestions shows plain "Not found" and no picker.
