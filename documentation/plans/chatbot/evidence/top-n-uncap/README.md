# TOP-N-UNCAP browser evidence (agent-browser 0.27.0, 30 Sep 2026)

Chatbot Console, reached by sidebar clicks (System > Messaging > Chatbot Console), at 1280x900
(`1280-*`) and 375x812 (`375-*`), on a copy of the stack built inside the cloud sandbox
(branch `claude/top-n-uncap-moxk53`): real frontend (`npm run dev`), real backend, real MCP
server, real `GET /order-management/top-selling` route and real presenter, on a throwaway
Postgres seeded with 1,205 products (`ZZT00001`..`ZZT01205`, distinct delivered quantities).

Stubbed, the same two seams the console tests stub (`tests/chatbot/test_top_selling_round6.py`
`console` fixture): the parser LLM (`parser_mod.parse` reads `top (\d+)` and returns the
top-selling parse, quantity, delivered; `resolve_config` a dummy config) and the Respond.io
access check (`engine_mod.check_access` allowed with the sales report grant,
`default_space_id`). No LLM key exists in the sandbox.

| File | Ask | What it shows |
|------|-----|---------------|
| `*-top5.png` | top 5 selling items by quantity | one bubble, "Top 5 selling items", ranks 1 to 5, no "(1/" marker |
| `*-top200-first.png` | top 200 selling items by quantity | bubble "(1/3)", "Top 200 selling items", ranks from 1 |
| `*-top200-last.png` | same | end of "(2/3)", then "(3/3)" ranks 196 to 200 and the rank-number offer |
| `*-top1500-first.png` | top 1500 selling items by quantity | "I can list at most the top 1,000 in one reply." then "(1/11)", "Top 1000 selling items" |
| `*-top1500-last.png` | same | "(11/11)" ranks 988 to 1000, rank 1000 is the last row |

Bubble counts observed in the page: top 200 = 3 bubbles, top 1500 = 11 bubbles, one per part
(`console_service._customer_texts`).
