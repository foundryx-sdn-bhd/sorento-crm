# PLAN: v1 chatbot states the linked fit before the usual answer (CHAT-FIT-LINE)

Status: Plan (lane CHAT-FIT-LINE, branch `crew/chat-fit-line`; behaviour card pending crew answers).
Track: standard, size M (new reply line customers see; no migration, no FE change, no auth/RBAC
change, no parser prompt change unless the card proves one is required).

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

TBD after the code map and the card (section filled before any code).

## Tests

TBD (red first, from fresh parser-output paraphrases).

## Live check

Golden set >= 8 messages on dev parser v94 (mirrors prod v63).
