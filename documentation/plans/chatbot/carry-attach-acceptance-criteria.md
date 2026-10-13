# UAC: CARRY-ATTACH

Plan: `PLAN-carry-attach-13oct.md`.

## Acceptance criteria

- AC-1 (T3, the prod bug): after "Srt8308 drawing" and "Srt8313", "Drawing" (the parser's
  echo of Srt8313 plus the document word, `domain_in_message: true`) sends SRT8313's Technical Specifications file. No "matched these" line, no
  escalation offer. The attachments call carries `product_ids` [SRT8313] and
  `attachment_type_ids` [Technical Specifications].
- AC-2: a document word alone, with the parser echoing the settled product back
  (`current_message: false`) and `domain_in_message: true`, keeps that product; every other
  carried kind the message did not name is still dropped (customer, brand, order).
- AC-3: a turn with no echo behaves as today (no `restated_kinds` on the verdict).
- AC-4 (T2, owner ruling 13 Oct): "Srt8313" after "Srt8308 drawing" sends SRT8313's Technical
  Specifications file in the same format as T1, with no ask and no extra wording.
- AC-5: "attachments for Srt8313" (a new ask naming no type) still asks which type.

## Golden set for crew's live check

Each row is a fresh conversation on a test contact. Use real product codes that hold the named
file types. On the dev copy SRT8308 and SRT8313 hold Technical Specifications but no photos;
SRT2308-HP and SRT2313-HP hold photos, so use those for every photo row (crew live check, 13 Oct). Code A and code B are different
products. On every checked turn read the attachments tool call: `product_ids` must be the product
in "Expected", and the reply must never say "matched these" over a document type alone, ask for a
type the conversation already gave, or offer an escalation for a file that exists.

| # | Turn 1 | Turn 2 | Expected on turn 2 | Turn 3 | Expected on turn 3 |
|---|--------|--------|--------------------|--------|--------------------|
| 1 | Srt8308 drawing | Srt8313 | SRT8313 drawing, T1 format, no ask | Drawing | SRT8313 drawing again |
| 2 | code A photo | code B | code B photo(s) | photo | code B photo(s), not code A |
| 3 | code A drawing | code B | code B drawing | 3D model | code B 3D file, or the truthful "none on file" line naming code B |
| 4 | code A drawing | code B | code B drawing | cert | code B certificate(s) |
| 5 | code A 图纸 | code B | code B drawing | 图片 | code B photo(s) (Chinese type word) |
| 6 | code A lukisan teknikal | code B | code B drawing | gambar | code B photo(s) (Malay type word) |
| 7 | code A photo | code B | code B photo(s) | sijil | code B certificate(s) (Malay) |
| 8 | code A cert | code B | code B certificate(s) | 证书 | code B certificate(s) (Chinese) |
| 9 | code A photo | code B | code B photo(s) | installation guide | code B installation guide, or the truthful "none on file" line |
| 10 | code A drawing | attachments for code B | asks which type (a new ask names no type) | Drawing | code B drawing |
| 11 | code A photo | code B | code B photo(s) | drawing for code C | code C drawing (the message names its own product) |

### Crew rule rows (13 Oct): narrow, short replies, topic switch and back

This lane touches two answer types: the product attachment answer (the files) and the bot's
attachment-type question. Period, channel and measure do not apply to an attachment answer, so
the narrowing rows use brand and document type.

| # | Turns, in order | Expected |
|---|-----------------|----------|
| 12 | which tap has technical drawing / sorento | turn 2 narrows the same set to Sorento taps, still technical drawings (`attachment_type_ids` unchanged) |
| 13 | Srt2308-HP photo / Srt2313-HP / drawing | turn 3 narrows the document type on the same product: SRT2313-HP drawing, or the truthful "none on file" line naming SRT2313-HP |
| 14 | attachments for Srt8313 (bot asks the type) / drawing | short reply to the ask: SRT8313 drawing |
| 15 | attachments for Srt8313 (bot asks, numbered if shown) / 2 | the second offered type for SRT8313, never a product pick |
| 16 | which tap has technical drawing (bot shows a brand breakdown) / sorento | short brand reply: the Sorento taps' drawings |
| 17 | code with no 3D file + "3D" (truthful miss, escalation offer) / yes | escalates once about that code and type, no second ask |
| 18 | photo for Srt8313 (bot asks something with a year, if it does) / 2026 | read as the answer to that question, never as a product code |
| 19 | Srt2313-HP photo / stock for Srt2313-HP / photo | topic switch to stock and back: turn 2 is the stock answer for SRT2313-HP, turn 3 is SRT2313-HP photo(s), with no ask for the product |
| 20 | Srt8308 drawing / any promotion for sorento? / drawing for Srt8313 | switch to promotions and back: turn 2 answers promotions only (no file); turn 3 is SRT8313's drawing. A bare "Srt8313" as turn 3 would correctly stay a promotion question (the prompt's bare-value rule keeps the last domain), so the back-switch names its document word |

Row 18 only applies if the bot asks a question whose short answer is a year. Attachment turns do
not ask one today, so crew can skip it when no such question appears.

## Live check notes (crew, 13 Oct, not fixed here)

- "Srt8313 3d" when the dev copy has no 3D attachment type sends the drawing; main does the
  same. Owner ruling pending; not part of this lane.
- A dealer's "Srt8313 attachment" was read by the parser as a photo ask (history bias). Parser
  reading, noted only.
