# UAC: PROMPT-DIET

Plan: `PLAN-prompt-diet-11oct.md`. Tests under `sorento_crm_backend/tests/chatbot/prompt_diet/`.
No live LLM or embedding calls in pytest (fake provider / fake embed / cassettes).

## Switch and fallback

- AC-1 `CHATBOT_PROMPT_DIET` unset: the parser gets today's static prompt, byte for byte; no embedding call.
- AC-2 Switch on: the system prompt starts with the core text and contains no card that was not selected.
- AC-3 Switch on, retrieval or card loading raises: the turn parses on the static prompt and answers; the
  trace records `prompt_diet.fallback`.
- AC-4 Switch on, harness `mock_reformulator_output`: no parser call, no embedding call (as today).

## Cut integrity

- AC-5 Every one of v50's 87 sections appears exactly once across core + cards (move only): the
  multiset of section texts is unchanged.
- AC-6 `parse_cards` returns one entry per `<<card:NAME>>` marker; duplicate names raise.
- AC-7 The fallback texts in `prompt_diet_data/` equal what the migration seeds.

## Selection

- AC-8 Cards = retrieved areas + pinned areas + companions; `order` brings `account`; `sales` brings
  `account` and `order`.
- AC-9 An open question, pending pick/offer or open task pins `open_question`; a pending escalation offer
  pins `escalation`.
- AC-10 Focus domains are pinned even when retrieval returns nothing (short replies like "2").
- AC-11 Card order is fixed (state cards, then areas in CARD_ORDER) whatever the input order, so two turns
  with the same card set build identical prompts.
- AC-12 Retrieval returns at most k enabled examples with a non-null embedding, nearest first; examples
  appear after all cards.
- AC-13 No regex or keyword rule reads the user's text anywhere in the module (test greps the module for
  `re.` use on message text; selection takes only vectors and state).

## Missing area

- AC-14 Parsed `domain_hint` or an `asks[].domain` naming a card that was not loaded -> exactly one re-call
  with that card added; the second emission is used.
- AC-15 `order_status` sales_report / sales_analysis / top_selling / sales_ranking without the sales card ->
  re-call with `sales` (plus its companions).
- AC-16 Never more than 2 parser calls per turn; tokens of both are summed in the usage and trace.
- AC-17 No re-call when every needed card was loaded.

## Trace

- AC-18 `understood` facts carry `prompt_diet` {on, cards, pinned, examples, missing, recalled, fallback}.
- AC-19 `prompt_cache_key` includes the core version and the selected card set.

## Data and scripts

- AC-20 `chatbot_examples` exists after `alembic upgrade head` with ~20 synthetic seeds (no real customer
  names; codes are fake); single alembic head.
- AC-21 The seed script refuses a data dir inside the repo and any path containing `hidden`.
- AC-22 The seed script masks customer names from the case contact/labels in message and context.
