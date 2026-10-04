# PLAN - Top selling: a named N is no longer capped at 100 (TOP-N-UNCAP)

Status: in review on PR #1407 (small fix track: no migration, no auth/RBAC change, no UI).

Owner, 30 Sep 2026: "top 100 selling ... remove the cap, there is a use case of 100, 200".
Supersedes the 26 Sep ruling "a named N is 1 to 100" in
`PLAN-chatbot-top-x-hot-selling-24sep.md`.

## Change

1. One technical safety ceiling, `TOP_SELLING_N_CEILING = 1000`, replaces the three 100 caps:
   - backend engine (`lanes/business/fetch.py`): a named N above the ceiling is sent as the
     ceiling and flagged (`n_capped_from`) so the reply says so; never a silent clamp.
   - API (`orders.py` `_TOP_SELLING_N_MAX`): 422 only above the ceiling.
   - MCP presenter (`presenters.py`): renders every row up to the ceiling; when the named N
     was above it, the reply states the ceiling.
   The MCP package cannot import the backend, so it carries its own copy of the constant with
   a comment naming the backend one; a test pins the two equal.
2. Long lists: the presenter splits its text into WhatsApp-safe messages (each <= 3900 chars,
   never breaking a row, "(1/3)" markers) so delivery does not depend on n8n chunking.
3. Wording: the MCP tool description and the route docstring say "1 to 1000".

## UAC

- AC-1: N=200 returns 200 rows end to end (fetch -> API -> presenter), split into ordered
  chunks each <= 3900 chars, markers "(k/m)", no row split across chunks.
- AC-2: N=5 reply is unchanged (one message, no marker).
- AC-3: N above 1000 answers with 1000 rows under the line "I can list at most the top 1,000 in one reply."
- AC-4: API 422 `invalid_n` only for n < 1 or n > 1000.
- AC-5 (grill Q6): the detail reply (one code's customers and months) longer than one message is
  split the same way: parts <= 3900 chars, "(k/m)" markers, no line broken, every customer once.

## Grill (feature skill Step 2, run late on 30 Sep 2026 after the owner's process audit)

The grill ran AFTER the code, which was built to the recommendation on each question below.
Asked as one `crew-ask` on PR #1407. Owner answer, 30 Sep 2026 (verbatim): "all as recommended
(over 1000 -> top 1000 with a note; buttons on last part only; split detail reply here; API >1000
-> error; sales analysis tool stays 100)".

| # | Decision | Options | Recommendation | Owner answer |
|---|----------|---------|----------------|--------------|
| Q1 | Safety ceiling | (a) 1000 (b) 500 (c) none | (a): 1000 rows is 11 to 12 WhatsApp messages (measured, see premise check); no ceiling lets "top 50000" flood the chat | (a) as recommended |
| Q2 | Named N above the ceiling | (a) answer the top 1000 under "I can list at most the top 1,000 in one reply." (b) ask for a number 1 to 1000 | (a): answers at once, never a silent cut | (a) as recommended |
| Q3 | Long list delivery | (a) CRM splits into messages <= 3900 chars, never breaking a row (b) one message, rely on n8n chunking | (a): the owner said n8n chunks (premise check), this removes the dependency | (a) as recommended |
| Q4 | Part marker | (a) "(1/3)" own line at the top (b) "Part 1 of 3" (c) marker at the end | (a): short, reads first; a reply that fits one message has none | (a) as recommended |
| Q5 | Quick replies and the result set | (a) last part only (b) every part | (a): the question is asked at the end; a rank typed after any part still picks from the whole list. CORRECTED premise: a ranking has NO quick-reply buttons, see premise check | (a) as recommended |
| Q6 | Detail reply (customers and months) splitting | (a) backlog (b) split the same way in this PR | (b): same WhatsApp limit, ~3 lines plus a test | (b) as recommended: split here |
| Q7 | Direct API/MCP n above 1000 | (a) 422 "n must be between 1 and 1000" (b) silent clamp | (a): the lane clamps and says so; a raw caller gets a clear error | (a) as recommended |
| Q8 | "How many?" offered range | (a) 1 to min(count, 1000) (b) 1 to count | (a): consistent with Q1 | (a) as recommended |
| Q9 | `crm_sales_analysis` n (1 to 100) | (a) leave, a different tool (b) uncap too | (a): not in the ruling; a new ask if wanted | (a) as recommended |

### Premise check (30 Sep 2026, after the owner's "get your facts right" feedback)

Every grill premise re-read against the code. Two were wrong or overstated (Q3, Q5), one was a
guess now measured (Q1); the owner's answers stand, since none of them changes what was built.

| # | Premise as asked | What the code says | Verdict |
|---|------------------|--------------------|---------|
| Q1 | "1000 rows is about 12 WhatsApp messages" | Measured with the presenter and 9-character codes (the fixture's `SRTWT7445` shape): 1000 rows = 41,264 characters / 11 messages (qty 9,000, RM 122,456.78) to 46,273 / 12 (qty 250,000, RM 12,345,678.90). `top_n` has no maximum in the parser schema (`head/parser.py:236`, `{"type": ["integer", "null"]}`). | Holds; was a guess, now measured. The same guess ("about 45 kB") in the `sales_report_service.py` comment is replaced by the measured figures. |
| Q2 | Above the ceiling the lane asks for the ceiling and says so | `lanes/business/fetch.py:903` clamps; `lanes/business/__init__.py:480` builds the note, `:1767` adds it to the notes printed above the reply (`fetch.py` `_top_selling_output`, `top_selling_notes`). | Holds. |
| Q3 | "n8n chunking is unverified" | On main the presenter's docstring records the OWNER saying "n8n already chunks a long WhatsApp message (owner, PR #1258 05:32Z)" (`presenters.py:2773` at b8cdbebe). The n8n workflows are not in this repo, so whether n8n does chunk is UNVERIFIED from the code. The 4096 limit is cited in a code comment (`product_discontinued_notify_service.py:159`); Meta's limit itself is UNVERIFIED from the repo. | OVERSTATED: it was the owner's own statement, not an unknown. (a) still holds: the CRM split removes the dependency either way. |
| Q4 | Marker "(k/m)" on its own line; a reply that fits one message has none | `presenters.whatsapp_parts`: returns `[text]` unmarked when `len(text) <= limit`, else prefixes `(i/total)`. | Holds. |
| Q5 | "Tap buttons and the rank pick go on the last part" | Measured on a real engine turn (top 200, `_run_turn` harness): `quick_replies` is `None` on BOTH parts and on `reply`; a ranking prints no menu (`turn/compose.py:701` `MAX_MENU_OPTIONS = 5`, `:748` a ranking's re-print is a short question, not buttons). What rides the last part is `result_set` (200 rows; the first part carries 0). A rank typed after any part picks from the whole list because the stored roster is built from the lane's `outstanding_ask.last_result_set` (`fetch.py:2499`) by `turn/compose.py:175-181`, not from the action's `result_set`. | WRONG premise on "buttons": a ranking has none. The rule (quick replies and result set on the last part only, `engine.split_send_actions`) still applies to any reply that has them. |
| Q6 | "An item with hundreds of customers could exceed 4096" | The detail's customer list has no limit: `sales_report_service.py:714-723` (`_ranked_by(...).all()`, no `.limit`). Whether a real item has hundreds of customers: UNVERIFIED (no production data in the sandbox). | Holds as a possibility. |
| Q7 | "422, as the 100 cap did"; MCP callers | Main: `orders.py:2025` `_TOP_SELLING_N_MAX = 100`, `:2182-2184` 422 `invalid_n` (b8cdbebe). The MCP server wraps backend GETs as tools for n8n (`CLAUDE.md:11`). | Holds. |
| Q8 | "It offered 1 to min(count, 100)" | Main: `presenters.py:2786` `min(total, _TOP_SELLING_MAX_ROWS)` with `_TOP_SELLING_MAX_ROWS = 100`. | Holds. |
| Q9 | `crm_sales_analysis` has its own n of 1 to 100 | `app/api/v1/sales/analysis.py:76` `_MAX_N = 100`, `:99` 422 `n_out_of_range`; `sorento_crm_mcp/catalog.py:842` "`n` (1 to 100)". | Holds. |

Other statements made on PR #1407, re-checked: "n8n executes `actions` and nothing else" is the
code comment at `app/api/v1/external/chat.py:535-537` ("the executor ruling, 5 Sep"); the n8n
side is UNVERIFIED from this repo.
