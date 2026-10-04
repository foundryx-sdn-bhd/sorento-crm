# Availability-mode stock asks: scenario catalogue

Every way a contact asks about stock, and what availability mode (a dealer: "got stock / no
stock", never our quantities) replies, beside what full mode (staff: compact / detailed)
does. Each `S` id is a test in `test_avail_mode_scenarios.py` (same folder), which asserts
the reply word for word, and a row on the owner-approved page
`documentation/mockups/avail-mode-scenarios/index.html` (v2, approved 2 Oct 2026).

Plan and rulings: `documentation/plans/chatbot/PLAN-avail-mode-replies-02oct.md`.

`R` = "Please refer to your salesman." X = the product's max quantity, else its own
category's, else 0. Available = on hand in the contact's allowed locations minus open SO.

## Rules

1. One line per product, "CODE x Q:", then ✅ (got stock) or ❌ (no stock). Never the words.
2. 1 <= available < Q and Q <= X: `✅ N available.` Q > X: `🚫 the quantity is more than what I can confirm here.` with no count.
3. ETA ask: `CODE: ✅ ETA dd/mm/yyyy`, or `CODE: No ETA`, one line per product (owner hand test, 3 Oct 2026).
4. A picker never offers "all" and refuses a bare "all" / "all of them" / "semua" (the list stays open). Typing every number is a pick.
5. Several codes: answered lines (asked order), then `Couldn't find: X, Y.`, then at most one question. Every vague code's list is in that one question, numbered on from the list before it. A code named twice adds up.
6. Exact codes only (owner hand test, 3 Oct 2026), on every turn of availability access: an exact code is the product and never its family; a code-like token with no exact code gets the which-one picker of its prefix family, else the did-you-mean; a word or a bare number is never a product; a bare number on a list (did-you-mean included) is that option.

## One code

| Id | Dealer | Bot (availability) | Full mode |
| --- | --- | --- | --- |
| S01 | SRT5674 x 50 (100 on hand) | `SRT5674 x 50: ✅ R` | Total on hand per product |
| S02 | SRT5674 x 50 (30 on hand, X 100) | `SRT5674 x 50: ✅ 30 available. R` | same |
| S03 | SRTW2000 x 150 (0, shipment 19/10) | `SRTW2000 x 150: ❌ ETA 19/10/2026.` | same |
| S04 | SRT5674 x 5 (0, nothing incoming) | `SRT5674 x 5: ❌ No stock and no incoming. R` | same |
| S05 | CWCX604 x 300 (X 200) | `CWCX604 x 300: 🚫 the quantity is more than what I can confirm here. R` | no cap in full mode |
| S06 | CWCX604 x 5 (X not set) | same 🚫 line | no cap |
| S07 | SRT5674 got stock? / 50 | `How many units of SRT5674?` / the S01 line | no quantity asked |
| S08 | SRTWC287-S x 3 (prefix of one code) | `SRTWC287-S-150 x 3: ✅ R` | that code's total |
| S09 | srtwc286 x 10 / 2 | `SRTWC286 x 10: which one?` + 1-10 / `SRTWC286-SH-150 x 10: ✅ R` | every match listed |
| S10 | check stock srtwc286 / 2 | `SRTWC286 matches 10 products. Which one?` + 1-10 / `How many units of SRTWC286-SH-150?` | every match listed |
| S11 | ELP3753 x 10 / yes | `Couldn't find ELP3753. Did you mean ELP3754?` / `ELP3754 x 10: ✅ R` | did-you-mean |
| S12 | FOO99 x 1 | `Couldn't find: "FOO99" (product).` + R | same sentence |

## ETA asks

| Id | Dealer | Bot | Full mode |
| --- | --- | --- | --- |
| S14 | ETA SRTW2000? | `SRTW2000: ✅ ETA 19/10/2026` + R | shipments with container and quantity |
| S15 | ETA SRTW2000 and MWT5727SS-CR | one line each, `MWT5727SS-CR: No ETA`, + R | same |
| S16 | ETA SRTW2000 and FOO99 | ETA line, `Couldn't find: FOO99.`, R | "I could not find FOO99." |

## Several codes

| Id | Dealer | Bot |
| --- | --- | --- |
| S17 | SRT5674 x 50, CWCX604 x 40, SRTW2000 x 10 | three lines, asked order |
| S18 | SRT5674 x 5, FOO99 x 1 | S01-style line, `Couldn't find: FOO99.` |
| S19 | SRT5674 x 5, SRTWC287-S x 3 | two answered lines |
| S20 | SRT5674 x 5, srtwc286 x 10 / 2 | answered line + picker / the picked line |
| S21 | srtwc286 x 10, srtwc6022 x 4 / 2 and 11 | both lists in one message (1-10, 11-12) / both answered |
| S21b | same / 2 / 12 | answered line + the SRTWC6022 list again (11-12) / answered |
| S22 | srtwc286 x 10, FOO99 x 1 | `Couldn't find: FOO99.` + picker |
| S22b | SRT5674 x 5, srtwc286 x 10, srtwc6022 x 4, FOO99 x 1 | answered, missing, both lists |
| S23 | FOO99 x 1, BAR12 x 2 | `Couldn't find: "FOO99" (product), "BAR12" (product).` + R |
| S24 | SRT5674 x 2, SRT5674 x 3 | `SRT5674 x 5: ✅ R` |
| S25 | SRT5674, CWCX604 / 1. 10, 2. 5 | point form `How many units for each?` / two lines |
| S26 | SRT5674, CWCX604, FOO99 | `Couldn't find: FOO99.` + point form |
| S27 | SRT5674 x 5 and CWCX604 / 7 | answered line + `How many units of CWCX604?` / `CWCX604 x 7: ✅ R` |
| S28 | SRT5674, CWCX604 / 10 | point form / both x 10 |
| S29 | SRT5674 x 50, SRTW2000 x 150 | `✅ 30 available` line + `❌ ETA` line |

Full mode answers every code with its quantities at once: no quantity question, no picker
for a family (every match is listed), and the shared miss sentence for codes not found.

## Picker replies (over `srtwc286 x 10`)

| Id | Dealer | Bot | Full mode |
| --- | --- | --- | --- |
| S30, S31, S34 | all / all of them / semua / all pls | `Please reply with the number of the code you need.` (list stays open) | "all" picks every option |
| S32 | 1,2,...,10 | ten answered lines | same |
| S33 | all, then 2 | refusal, then the picked line | - |
| S35 | 1 and 2 | two answered lines | same |
| S36, S37 | 2 of 3 / i want 2 of the third one / 2 of 3rd product | `SRTWC286-SH-200 x 2: ...` | - |
| S38 | 2 of 1 and 5 of 3 | two lines, x 2 and x 5 | - |
| S39 | 2 of 2 (after "x 10") | `SRTWC286-SH-150 x 2: ...` (the pick's quantity wins) | - |

## The live parser's own readings (tester-local pass on 7fa5d654)

| Id | Dealer | Parser read it as | Bot |
| --- | --- | --- | --- |
| S43, S43b | 2 of 3 / 2 of the third one / i want 2 of 3rd product / 2 of no 3 / 2 pcs of 3 | option 2, qty 3 | `SRTWC286-SH-200 x 2: ...` (the engine reads "N of M" itself) |
| S43c | 2 of 1 and 5 of 3 | options 2 and 5 | `SRTWC286-SH x 2` and `SRTWC286-SH-200 x 5` lines |
| S44 | SRT5674 x 2, SRT5674 x 3 | one entity, qty 3 (`replace_combine`) | `SRT5674 x 5: ✅ R` |
| S44b | SRT5674 x 2 for site 3 | qty 2 | `SRT5674 x 2: ✅ R` (a code named once is not summed) |
| S45 | ETA SRTW2000 and FOO99 (no shipment) | both codes | `SRTW2000: No ETA`, `Couldn't find: FOO99.`, R |

The dealer ETA view lists every product asked (no shipment: "No ETA") and
tells dates as dd/mm/yyyy (`eta_policy.dealer_view`, `tests/test_avail_mode_dealer_eta.py`).
| S46, S46b | CWCX604 x 300 / SRT5674 x 50, read by the parser as `check_incoming` (intermittent, tester re-run on 2eb2a00ef) | stock ask by the message's own words: the 🚫 / ✅ line, never "ETA not confirmed yet" |
| S46c | SRTW2000 x 10 when arrive? / ETA SRTW2000 x 10 / ... bila sampai | an ETA word keeps the ETA route: `SRTW2000: ✅ ETA 19/10/2026` + R |

## Owner hand test, 3 Oct 2026 (availability access)

| Id | Dealer | Bot |
| --- | --- | --- |
| S47 | srt5764 xx 10 (the resolver also matched catalogue rows for "xx" / "10") | `Couldn't find SRT5764. Did you mean:` SRT57-CR, SRT5713, SRT5732 numbered 1-3 (tied scores, so their order is the database's collation); never a quantity list of the catalogue |
| S48 | srt5764 10 / 2 (parsed as a product "2", a pick, or a quantity) | did-you-mean / SRT5713's number gives `SRT5713 x 10: ✅ R` |
| S48b | srtwc286 / 88 | `SRTWC286 x 88: which one?` (a number past the list is the quantity) |
| S49 | ETA SRTW2000 and MWT5727SS-CR | `SRTW2000: ✅ ETA 19/10/2026`, `MWT5727SS-CR: No ETA`, R |
| S50, S50c | srtw2000 20 / eta | the stock line / `SRTW2000: ✅ ETA 19/10/2026` + R: SRTW2000 only, never -SS-CR / -A / -NL |
| S50b | eta SRTWC286-SH / eta | `SRTWC286-SH: No ETA` + R, both turns, never the SRTWC286-SH-* variants |
| S51 | srt5764 xx 10, read by the parser as one unsure capture (tester re-run on a5ba9dc9f) | the S47 did-you-mean, every run; never `I captured ... couldn't tell which part is which` |
| S52 | srtw2000 (parser routed it to orders / outstanding) / eta | `SRTW2000: ✅ ETA 19/10/2026` + R: the exact code on EVERY availability turn, so nothing of its family is carried |
| S52b | SRTWC286-SH (routed to orders / outstanding) / eta | `SRTWC286-SH: No ETA` + R |
| S53 | eta SRTWC286 / 2 | which-one picker with no ETA or stock of any variant / `SRTWC286-SH-150: ✅ ETA 19/10/2026` + R |

Full mode keeps its own resolution (prefix families, described products) and its own ETA rows.
