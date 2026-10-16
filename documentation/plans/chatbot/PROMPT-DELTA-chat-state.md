# CHAT-STATE parser prompt delta (S5)

One self-contained section APPENDED to the base parser prompt (`chatbot_semantic_parser`, the version the
`production` label points at on the prod copy). It edits nothing above it, so it applies to any base version. The
literal `state_changes` in it is what turns the state path on (`resolve_config`), so the prod label never opts in.

Publish: an UNLABELLED version on the dev copy only (`ai_prompt_service.LABELS` allows only production/staging,
`app/services/ai_prompt_service.py:23`); runs pin it per turn through the dry-run `prompt_overrides` envelope key
(`engine.py:1308`, EVAL-BENCH `scripts/chatbot_eval/engine.py:76-77`). The `production` label is never moved by this
lane. The single source of the text below is the repo constant the S5 publish script reads (coder moves it there and
this file then points at it).

Rules taught are business rules (what carries), not language: no word lists, no example phrases from any eval set.

---

## Conversation state and state_changes

The user block may carry a `State:` block. It is the whole conversation state and it is never cut:
- `subject`: what the conversation is about now (domain, customer, products, brand, tier, channel, period, status,
  document, sort, report).
- `open question`: the question the bot asked last, with its numbered options, or none.
- `last list`: the numbered items the bot showed last, or none.
- `pending task`: the stock quantity task still owed, or none.
Where the rules above mention "Current subject", "Open question", "Open question options", "Pending" or "Open task",
read the matching `State` line.

Fill every other field exactly as the rules above say, with values from THIS message only. Never restate an item of
the State as an entity: only what this message itself names is an entity.

Then fill `state_changes`: the changes this message makes to the State, in order. Each item is
`{op, axis, position}`. An op never carries a value: the value of a `set` is the field you already filled for that
axis (entities with that hint; `sales_channel`; `date_mode` / `date_filter_start` / `date_filter_end`; `status` /
`order_status` / `document`; `sort_by` / `sort_dir`; `demand_qty` or the entity's quantity).

| op | when | axis | position |
|---|---|---|---|
| `keep` | the message continues the subject as it is | null | null |
| `new_subject` | the message is a full ask with its own subject and measure; only the customer of the old subject still applies | null | null |
| `set` | the message adds or replaces ONE part of the subject (brand, filter, period, channel, quantity, product, customer); one `set` per part it states | the part | null |
| `clear` | the message removes one part of the subject | the part | null |
| `pick` | the message points at an item of the `last list` (its number, its position, or its name as listed) | null | the item's number |
| `answer` | the message answers the `open question` (one of its options, yes or no, or the value it asked for) | null | null |
| `switch_domain` | the message asks a different kind of fact about the same subject | the domain name | null |
| `close_question` | the message leaves the `open question` unanswered and asks something else | null | null |

Business rules:
- "and <another item>?" replaces only that item (`set product` or `set customer`); the ask, quantity, period and
  filters stay.
- A quantity said for the held item is `set quantity`.
- A customer named first stays for later asks; do not clear it when a product follows.
- A new ask that states its own measure or basis (a ranking, a report) is `new_subject`: nothing of the old measure,
  basis, channel or period carries.
- When the `open question` is open and the message is not an answer to it, add `close_question`.
- Small talk, thanks and greetings change nothing: `[{"op": "keep", "axis": null, "position": null}]`.
- When you cannot tell whether the message continues the subject or starts a new one, output `[]`.
- When the State block is absent or every line is none, output `[{"op": "new_subject", "axis": null, "position": null}]`
  for a business ask and `[]` otherwise.
