# UAC: "all products" after a delivery miss stays an order enquiry (ORDER-BROADEN)

Plan: `PLAN-order-broaden-05oct.md`. Card: `CARD-order-broaden-05oct.md`. Tests in
`sorento_crm_backend/tests/chatbot/` unless said otherwise.

AC-1  Prompt rule (patch, no migration): the BROADEN AXIS block gains, inside its KEEP
      bullet after prod text line 507, the rule that a MISS reply inviting other dates never
      moves the domain, that "all products" is never a stock / incoming / ETA question, and
      one worked example set over a delivery MISS: "all products" -> domain_hint order,
      intent_hint check_order, broaden_axis product, broaden_to all, entities [], entity_op
      reuse, no dates emitted; "all customers" -> broaden_axis customer; "any date" ->
      broaden_axis date. Delivered as `alembic/data/chatbot_semantic_parser.order-broaden
      .addendum.txt` + `.patch`. `test_order_broaden_prompt_patch.py` (static, always runs).
AC-2  The production text file `chatbot_semantic_parser.prod-20261001.txt` is untouched
      (pdyn_0003 requires it byte-identical). Static test.
AC-3  The committed .patch equals the diff derived from the addendum and applies cleanly
      with GNU patch. Static test.
AC-4  Live parser (opt-in, `CHATBOT_LIVE_LLM=1`, run by crew on a machine with OpenAI
      access): over the owner's journey (previous response = the HANLIM / SRTWC286-SH miss,
      focus order + window 28/09 to 04/10), the PATCHED text answers "all products" and
      "all customers" per AC-1; the v42 text is xfail (non-strict). "any date" widens the
      date axis only; "15 Sep to 10 Oct" names its own window; "any incoming for
      srtwc286-sh?" still moves to incoming. `test_order_broaden_live_parser.py`.
AC-5  Presentation: a compose block whose figures carry `flags.dealer_view` never prints
      `date_line`; an order block still prints it under its header (control).
      `test_order_broaden_presentation.py` (compose unit tests).
AC-6  Engine: a turn whose `focus.domains` changes to a different set clears the carried
      `date_window` (`domain_change_drops_date_window`); the incoming tool call carries no
      `eta_from` / `eta_to` from the delivery window, and the dealer reply has no "Order
      date" line. Engine journey test through the dealer console, v42 verdict. Exception:
      a carried top selling ranking keeps its period for the report drilled out of it
      (existing `test_top_selling_round5.py` / `round7.py` tests stay green).
AC-7  Engine: the same domain keeps the window. With the patched verdict (order), "all
      products" after the miss re-runs the orders tool with the SAME
      `actual_delivery_date_from/to` and no `product_ids`, never an incoming call, never an
      ETA line, and `domain_change_drops_date_window` does not fire (owner Q1 (a)).
AC-8  Red first: every test in AC-5 to AC-7 except the control fails with the two fixes
      reverted and passes with them; the kill tests (each fix reverted alone, the addendum
      emptied) are posted on the PR.
AC-9  No drift: `pytest tests/chatbot -q` and `tests/test_ai_semantic_parser_*.py` green on
      the branch.
AC-10 Hand test `laneboard/scripts/1495.md` steps 1 to 5 pass on the crew test copy with the
      patched prompt; steps 4 and 5 pass with the presentation fix alone.
