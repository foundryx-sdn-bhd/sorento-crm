# CHAT-STATE: the parser prompt section that teaches `state_changes`, and the one function
# that appends it to a base prompt. This constant is the single source of the text; the
# literal `state_changes` in it is what opts a prompt version into the state path
# (`parser.resolve_config`), so the production label never opts in.
from __future__ import annotations

STATE_SECTION = """## Conversation state and state_changes

The user block may carry a `State:` block. It is the whole conversation state and it is never cut:
- `subject`: what the conversation is about now (domain, customer, products, brand, tier, channel, period, status,
  document, sort, report); a domain marked `(answered)` is the domain of the last answer.
- `open question`: the question the bot asked last, with its numbered options, or none.
- `last list`: the numbered items the bot showed last, or none.
- `pending task`: the stock quantity task still owed, or none.
Where the rules above mention "Current subject", "Open question", "Open question options", "Pending" or "Open task",
read the matching `State` line.

Fill every other field exactly as the rules above say, with values from THIS message only. An entity is what THIS
message names, even when the State already holds it; a State item the message does not name is not copied, it is not
an entity.

Then fill `state_changes`: the changes this message makes to the State, in order. Each item is
`{op, axis, position}`. An op never carries a value: the value of a `set` is the field you already filled for that
axis (entities with that hint; `sales_channel`; `date_mode` / `date_filter_start` / `date_filter_end`; `status` /
`order_status` / `document`; `sort_by` / `sort_dir`; `demand_qty` or the entity's quantity).

| op | when | axis | position |
|---|---|---|---|
| `keep` | the message continues the subject as it is | null | null |
| `keep` / `skip` / `clear` / `unclear` | the State holds a customer or a period: say what this message does with it (rules below) | `customer` or `period` | null |
| `new_subject` | the message is a full ask with its own subject and measure; the held customer, the held period (see the held-slot rules), the access tier and the brand carry, nothing else | null | null |
| `set` | the message adds or replaces ONE part of the subject; one `set` per axis the message states (several items of one axis are one `set`); a stated value applies as stated | the entity's own kind (its hint), or one of: customer, warehouse, brand, tier, channel, period, status, document, sort, quantity | null |
| `clear` | the message removes one part of the subject | one of the same names | null |
| `pick` | the message points at one of the options of the `open question` or at an item of the `last list` (its number, its position, or its name as listed); one `pick` per picked position ("1 and 3" is two picks); only product rows of the `last list` can be picked | null | the item's number |
| `answer` | the message answers the `open question` with yes or no, or with the value it asked for | null | null |
| `switch_domain` | the message asks a different kind of fact about the same subject; the new domain is the `domain_hint` you fill as usual | null | null |
| `close_question` | the `open question` is still owed (it asked for something) and the message leaves it unanswered; never for a question that was already answered | null | null |

Business rules:
- Several items of one axis (several products, several brands) are ONE `set` for that axis.
- A message naming only another item (another code, another product) asks the same fact as the last answer: `set product` and keep the domain marked `(answered)`.
- A message that refers to the held subject as a whole is `keep`; such a reference is never output as an entity.
- "and <another item>?" replaces only that item (`set product` or `set customer`); the ask, quantity, period and
  filters stay.
- A quantity said for the held item is `set quantity`.
- A customer named first stays for later asks; do not clear it when a product follows.
- A new ask that states its own measure or basis (a ranking, a report) is `new_subject`: nothing of the old measure,
  basis or channel carries; the held period carries unless you say `skip period` or `clear period`.
- When the `open question` is open and the message is not an answer to it, add `close_question`.
- Small talk, thanks and greetings change nothing: `[{"op": "keep", "axis": null, "position": null}]`.
- When you cannot tell whether the message continues the subject or starts a new one, output `[]`.
- When the State block is absent or every line is none, output `[]` (nothing to change); do not output `new_subject`.

Held-slot rules (customer and period):
- When the State holds a customer or a period, state for each one, per message: `keep` (it applies to this ask),
  `skip` (it does not apply to this ask and stays held), `clear` (drop it), or `unclear` (you cannot tell). A slot you
  say nothing about applies as before.
- Period: a follow-up keeps the held period (no op needed, or `keep period`); a period this message states replaces it
  (`set period`); the held period also carries across a change of domain, unless you say `skip period` or `clear period`; for a brand-new topic the held period may not belong to, judge it: `skip period` or `clear period`.
- Held customer: a delivery or "when will it arrive" ask with a held customer is delivery to that customer
  (`keep customer`, delivery/order domain); a clearly ETA or incoming stock ask is product-level (`skip customer`). An
  ETA ask is about incoming stock by meaning, not by a word. Say `unclear customer` only when you cannot tell; the
  system then settles it from the customer record.
"""

HEADING = "## Conversation state and state_changes"


def build_lane_prompt(base_template: str) -> str:
    """The base prompt unchanged, then the state section. Idempotent: a template that
    already carries the section is returned as it is."""
    if HEADING in base_template:
        return base_template
    return base_template + "\n\n" + STATE_SECTION
