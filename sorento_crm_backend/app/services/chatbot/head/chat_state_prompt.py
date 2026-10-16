# CHAT-STATE: the parser prompt section that teaches `state_changes`, and the one function
# that appends it to a base prompt. This constant is the single source of the text; the
# literal `state_changes` in it is what opts a prompt version into the state path
# (`parser.resolve_config`), so the production label never opts in.
from __future__ import annotations

STATE_SECTION = """## Conversation state and state_changes

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
"""

HEADING = "## Conversation state and state_changes"


def build_lane_prompt(base_template: str) -> str:
    """The base prompt unchanged, then the state section. Idempotent: a template that
    already carries the section is returned as it is."""
    if HEADING in base_template:
        return base_template
    return base_template + "\n\n" + STATE_SECTION
