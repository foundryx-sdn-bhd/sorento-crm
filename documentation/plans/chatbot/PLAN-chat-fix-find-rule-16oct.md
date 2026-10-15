# PLAN: chat fix, FIND rule text follows ruling R9 (lane CHAT-ADVISOR-SPIKE, chat-fix LEAD)

Status: Proposed, waiting for a crew/owner ruling (16 Oct). Track: small fix (text only, under 50 lines, no
migration, no auth change). Branch `crew/chat-fix-find` off origin/main ccb1ce9820, worktree
`sorento_crm-chat-fix-find`. Ships as its own PR before PR-A2 (rec) or folds into PR-A2 (crew to say).

## Problem

Parser-gold TRAIN item G005 (a bare fit ask) is read as a file ask (product_attachment) by some prompt versions:
v94 on main 2 of 2 right, the PR-A vocabulary version v104 0 of 5. Read from the parser's own outputs (no new LLM
calls, 16 Oct): the FIND rule text tells the parser the opposite of the gold ruling.

- Ruling R9 (crew/state/eval/parser-gold/rulings.md:27-30): a seat cover / mirror / shelf reached through a fit link
  is a FIND on the named product; "a bare 'what seat cover for X' is product information".
- `app/services/product_find.py:342-345` (FIND_RULE, from #1613, 14 Oct): with nothing else asked, BARE ENTITY
  CONTINUATION decides the domain (the previous turn's, else null) and "the relation itself never sets
  master_products, and never product_attachment". On a first message the domain is left unanchored.
- `app/services/product_find.py:299-301` (fits filter vocabulary, from #1613, pinned by #1710's
  `tests/chatbot/test_chat_fit_line.py:136`): "The other-side word never IMPLIES an attachment type (no photo,
  drawing, installation guide, certificate or spec file)": a negated list that names every file word next to the
  fit ask.
- The text reaches EVERY prompt version through `{{find_filters}}` (prod v63 included): a deploy changes the prod
  parser's input with no prompt publish.

## Change (text only, two sentences)

1. :342-345: with nothing else asked, a fit ask is product information (domain master_products, intent
   check_product), per R9; a bare code right after a fit ask still repeats that fit ask (continuation unchanged); a
   file domain still needs a file word in THIS message.
2. :299-301: replace the negated file-word list with the positive rule: attachment_type and a file domain come only
   from `file_word`, the word of THIS message asking for a file. Update `test_chat_fit_line.py:136` with the ruling.

## Expected (upper bound, no LLM: scratchpad g005tools/sim.py over the recorded v94/v104-v107 outputs)

TRAIN wrong to right: G009 8/8, G014 8/8, G001 7/8, G006 5/8, G005 4/4, G007 2/8; right to wrong: none predicted.
v94 TRAIN 101/137 -> about 104-106.

## Gates

- Parser-gold TRAIN x2 on v94 with the new text vs the v94 baseline runs (101/137 both): whole-parse must not
  drop; no item right in both baseline runs goes wrong in both new runs. About 274 parser calls (~$1.8): needs a
  cap raise or the next day.
- Walkthrough fit rows (seat cover / mirror asks) staff + dealer, no regression.
- Crew runs the held-out set. reviewer. Owner hand test (a deploy changes prod's parser input).

## Decision needed

Two earlier decisions disagree: #1613's FIND text (relation never sets master_products) and crew's parser-gold
ruling R9 (bare fit ask = product information). This plan follows R9. If #1613's choice stands instead, the gold
label of G005 and its siblings changes rather than the text.
