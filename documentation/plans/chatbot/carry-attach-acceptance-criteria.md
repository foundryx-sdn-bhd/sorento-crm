# UAC: CARRY-ATTACH

Plan: `PLAN-carry-attach-13oct.md`.

## Acceptance criteria

- AC-1 (T3, the prod bug): after "Srt8308 drawing" and "Srt8313" (bot asks for the type),
  "Drawing" sends SRT8313's Technical Specifications file. No "matched these" line, no
  escalation offer. The attachments call carries `product_ids` [SRT8313] and
  `attachment_type_ids` [Technical Specifications].
- AC-2: a document word alone, with the parser echoing the settled product back
  (`current_message: false`) and `domain_in_message: true`, keeps that product; every other
  carried kind the message did not name is still dropped (customer, brand, order).
- AC-3: a turn with no echo behaves as today (no `restated_kinds` on the verdict).
- AC-4 (T2, switch off, default): "Srt8313" after "Srt8308 drawing" asks for the attachment
  type, as today.
- AC-5 (T2, switch on, if the owner rules (a)): the same turn sends SRT8313's file under the
  carried type.

## Golden set for crew's live check

Each case is a fresh conversation on a test contact. Use real product codes that hold the named
file type (pick per case; SRT8308 and SRT8313 hold Technical Specifications). "Asks" = the bot's
"Please provide the attachment type for product X" question (T2 behaviour, switch off).

| # | Turn 1 | Turn 2 | Turn 3 (the answer) | Expected on turn 3 |
|---|--------|--------|---------------------|--------------------|
| 1 | Srt8308 drawing | Srt8313 (asks) | Drawing | SRT8313 Technical Specifications file |
| 2 | <code A> photo | <code B> (asks) | photo | code B Product Photos file(s), not code A |
| 3 | <code A> 3D | <code B> (asks) | 3D model | code B 3D file, or the truthful "no 3D model for code B" line naming code B, never a miss over no product |
| 4 | <code A> cert | <code B> (asks) | certificate | code B certificate(s) |
| 5 | <code A> 图纸 | <code B> (asks) | 图片 | code B photo(s) (Chinese type word) |
| 6 | <code A> lukisan teknikal | <code B> (asks) | gambar | code B photo(s) (Malay type word) |
| 7 | <code A> drawing | <code B> (asks) | sijil | code B certificate(s) (Malay) |
| 8 | <code A> photo | <code B> (asks) | 证书 | code B certificate(s) (Chinese) |
| 9 | <code A> drawing | <code B> (asks) | installation guide | code B installation guide, or the truthful "none on file for code B" line |
| 10 | <code A> photo | <code B> (asks) | drawing for <code C> | code C drawing (the message names its own product; code B is not kept) |

What to read on each turn 3: the attachments tool call's `product_ids` must be the product the
bot asked about (code B; code C on row 10), and the reply must never say "matched these" over a
document type alone or offer an escalation for a file that exists.

Row 10 is the guard the other way: a message that names a new product replaces the asked one.
