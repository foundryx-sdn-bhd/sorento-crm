# UAC - PROMPT-DYNAMIC (plan `PLAN-prompt-dynamic-30sep.md`)

AC-PD-1 A template holding `{{domains}}` renders the current `chatbot_domains` names; adding a
        row and committing makes the NEXT render carry it with no publish and no label move.
AC-PD-2 Every variable in D2 renders from its registry; an empty registry renders an empty value
        (teams/agents: the code fallback), never the literal `{{token}}`.
AC-PD-3 Publishing a version saves the text exactly as submitted; a template without
        `{{domains}}` stays without it after any later publish or migration.
AC-PD-4 `chatbot_status_words` exists, is seeded with outstanding / delivered / so_outstanding /
        do_outstanding / outstanding_both / sales_report / sales_analysis / top_selling, and the
        sales rows carry "sales", "sales report", "top selling", "sales analysis",
        "best selling".
AC-PD-5 Admin can list, create, edit and delete status words (deferred delete, no confirm);
        each write reaches the next render.
AC-PD-6 The wording migration adds one UNLABELLED version built from the production text with
        tokens substituted; the `production` label still points at the old version.
AC-PD-7 Drift test fails when a rendered list diverges from its registry or a literal registry
        list reappears in the wording layer.
AC-PD-8 Rendered before/after diff on production data: equivalent except the registry rows the
        old hand lists lacked (sales words; any domain the hand list had missed), listed in the PR.
AC-PD-9 "my sales this month" routes `domain=sales`; outstanding / delivery / DO asks stay
        `order`; sales asks without the reveal key are refused as today.
AC-PD-10 Search in the prompt editor: Enter / Shift+Enter / next / prev scroll to the match and
        select it; typing at a caret while search is open edits only at the caret. (DONE)
AC-PD-11 Editor: each variable is a chip labelled "<Label> - N rows, from <Page>" with a link,
        expandable to its rendered text, at view AND edit time; deleting a chip removes the
        token; a picker re-inserts one; a "Wired to this agent" panel lists every source with
        count and last-changed; "Preview rendered prompt" shows the final text.
AC-PD-12 Diff: next / previous change buttons, "change N of M", keyboard shortcuts, and a
        changes-only mode with context lines.
