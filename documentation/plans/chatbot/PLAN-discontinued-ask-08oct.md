# PLAN: DISCONTINUED-ASK - chatbot answers "is X discontinued?" directly

Status: Card posted (8 Oct), awaiting owner answers. Track: small fix (see CARD-discontinued-ask-08oct.md).

## Owner's words (8 Oct 2026)

Users ask whether a product is discontinued; the bot dumps the master product card. A discontinued
product carries "(PRODUCT DISCONTINUED)" but a live one carries nothing, so the reply does not answer
the yes/no question.

## Steps

1. Research: parser intent/fields, product-info reply builder, discontinued stamp (file:line).
2. Behaviour card (owner decides).
3. Red-first tests, then fix.
4. Reviewer, PR.
