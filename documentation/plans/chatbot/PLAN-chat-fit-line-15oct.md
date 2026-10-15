# PLAN: v1 chatbot states the linked fit before the usual answer (CHAT-FIT-LINE)

Status: Build (lane CHAT-FIT-LINE, branch `crew/chat-fit-line`; card answered by crew 15 Oct:
Q1 a, Q2 a, Q3 a, no split). Track: standard, size M+ (new reply line customers see + mirror /
shelf fit resolution; no migration, no FE change, no auth/RBAC change, no prompt republish: the
live `{{find_filters}}` text and the strict parse schema change in code).
Card `CARD-chat-fit-line-15oct.md`; UAC `chat-fit-line-15oct-acceptance-criteria.md`.
Crew scope addition 15 Oct: mirror / shelf answering (#1690 "Part 2") is in this lane.

Owner-directed 15 Oct ~18:45; the v1 chatbot is otherwise frozen, so the diff touches only what the
line needs.

## Owner story (prod console, parser v63 = v52 + `{{find_filters}}`, PR #1613)

- "what is the seat cover for SRTWC286-SH-NEW-150" -> "I have SRTSCBD407. What would you like me to know?"
- "please recommend seat cover for SRTWC287" -> "I have SRTSCBD315. What would you like me to know?"
- "seat cover recommendation for SRTWC8383-RL-MBL" -> stock summary of SRTSCBD823-MBL, no word that
  it is the seat cover.

Owner: "we just need to have the line 'seat cover for xxx is xxx' or 'mirror for xx is xxx' (need
to see the linkage), then followed by the usual answer (unchanged)."

## Rule (owner, final)

1. When the turn resolved a product through a `product_compatibility` link (#1613 seat cover <-> WC,
   #1690 mirror / shelf <-> cabinet), the reply STARTS with one line naming the link, its wording
   read from the link's kind.
2. Then the usual answer, unchanged.
3. Truthful: only links on record; none on record keeps the #1613 no-link line.
4. The line comes from the resolution path (the product was reached via a link), never from words
   in the message. No keyword / regex rules in code.
5. No internal ids; product codes as today; audience rules unchanged.

## Design

Paths under `sorento_crm_backend/`.

Slice 1, every link kind resolves:
- Parse schema: the find filter item gains `kind` (`["string","null"]`, enum = `list(KINDS)` +
  null, required; strict mode) at `app/services/chatbot/head/parser.py:543-569`. The reader
  (`lanes/business/find.py:43-56`) passes it on.
- Vocabulary (`app/services/product_find.py:184-275`): the `fits` line's first sentences list the
  `KINDS` pairs generated from the registry and ask for `kind`; the rest of today's seat cover text
  stays. `FIND_RULE`'s seat-cover-only asides read "the other side of a listed relation".
- Resolution (`lanes/business/find.py:120-304`): the seat-cover-only reads
  (`:94,103,224-225`, `_linked_codes_holding`) take the parsed kind and use the kind-aware
  `accessories_for` / `hosts_for` (`product_compatibility_service.py:99-110`); the data decides
  the direction as today. A carried fit ask keeps its kind (focus relation stores it; a stored
  relation without one reads as seat cover). No-link lines per kind from `KINDS` nouns, the seat
  cover lines unchanged (`product_find.py:126-130`).

Slice 2, the fit line:
- Linked entities already carry `linked_from` / `linked_as` (`find.py:271-276`); they also carry
  the kind. `_run_answer` (`app/services/chatbot/engine.py:7664`) prepends the line before the
  answer, outside the `_carried_line` prepend (`:7697-7701`), with a blank line between.
- Line text per kind and direction from `KINDS` nouns; catalogue entries with ms / zh
  (`label_catalog.py`, templated match at :811) so `_localize_result` (`engine.py:3045`) says it in
  the turn's language.

## Tests

Tester first (red), from the UAC and fresh parser-output paraphrases; new file
`tests/chatbot/test_chat_fit_line.py` reusing the `_turn` / `_parity` / `_seed` harness of
`tests/chatbot/test_seat_as_product.py`; that file's parity asserts become line + blank + control.
Parser contract (`tests/chatbot/test_find_parser_contract.py`) for the schema and vocabulary.
Live golden set in `tests/chatbot/test_find_live_parser.py` (opt-in).

## Live check

Golden set >= 8 messages on dev parser v94 (mirrors prod v63), DB `sorento_cagent_stack`.

## Known limits and triggers (reviewer rounds 2 to 7)

- A code that is BOTH a host and an accessory (none in the data on 15 Oct): AC-24 reads hosts of
  every kind it holds as accessory, so a forward ask on it would also answer its own hosts.
  Trigger: the first such product_compatibility row; then read hosts of every kind only for a code
  with no accessory link of the parsed kind.
- kind null and nothing linked on a cabinet answers today's seat cover no-link line (AC-3b).
- A turn with several domains where one is denied drops the fit line for the whole reply
  (conservative: no line beside any refusal).
- zh / ms other-side nouns joined to the code (`马桶盖多少钱`, `cermin untuk ...`) are sometimes
  read as a category entity, on main too: a parser-side follow-up, not a code guard.
