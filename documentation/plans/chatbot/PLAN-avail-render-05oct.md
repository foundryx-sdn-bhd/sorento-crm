# PLAN: availability-mode wording and discontinued marker (AVAIL-RENDER)

Status: Review (PR #1493). Track: small fix (rendering only, no migration, no auth/RBAC change, no new
ingest surface, no prompt change, no UI).

Owner ask (5 Oct 2026, live traces, contact on AVAILABILITY stock access, prompt v42):

A. An availability-mode stock line and a dealer ETA line carry the discontinued marker
   compact/detailed already print. Today "check stock SRTWT2634-RG x 5" prints
   "SRTWT2634-RG x 5: 🚫 the quantity is more than what I can confirm here. Please refer to
   your salesman." with no hint the product is discontinued.
B. A tick line says stock is available and does not refer. Owner decision (5 Oct 23:40):
   the words are "Got stock". Lines that cannot confirm (🚫 too big, ❌ no stock and no
   incoming) keep the refer.

## Where the lines are printed (origin/main)

- Availability line: `sorento_crm_mcp/sorento_crm_mcp/presenters.py` `_AVAILABILITY_TAILS` /
  `_availability_tail`; `_stock_availability` passes flags `{needs_quantity, branch}` only.
- Availability entry: `sorento_crm_backend/app/services/inventory_service.py`
  (`_apply_stock_visibility`, entry dict) built from the ORM `Product`, without
  `is_discontinued` (`models/product.py` `is_discontinued`).
- Discontinued marker: `lanes/business/fetch.py::_item_line` prints
  `\n⚠️  *(PRODUCT DISCONTINUED)*` under any item whose `flags.discontinued` is true, for every
  tool and mode. The cross-domain renderer (`lanes/business/answer.py`) does the same.
- Dealer ETA: `app/services/eta_policy.py::dealer_view` rows `{product_code, etas}`, called
  from `app/api/v1/incoming_stock.py`; MCP `_incoming_dealer` flags `{dealer_view}` only.
- Customer asks: `_stamp_refers` (presenters) stamps `refers_to_salesman` = tail ends with the
  refer; `engine._record_customer_asks` logs stamped entries.
- i18n: `app/services/chatbot/label_catalog.py` (one catalog, ms + zh per sentence;
  `Localizer.tail` translates the sentence after the status mark).

## Availability outcome -> wording (after)

| Outcome | Branch | Line |
| --- | --- | --- |
| Q above the category max (or none set) | too_big | `🚫 the quantity is more than what I can confirm here. Please refer to your salesman.` (unchanged) |
| available >= Q | in_stock | `✅ Got stock.` (was `✅ Please refer to your salesman.`) |
| 1 <= available < Q, Q within the max | in_stock | `✅ Got stock, N available.` (was `✅ N available. Please refer to your salesman.`) |
| available < 1, shipment due | incoming | `❌ No stock, ETA dd/mm/yyyy.` (was `❌ ETA dd/mm/yyyy.`; owner hand test 6 Oct 2026) |
| available < 1, nothing incoming | no_incoming | `❌ No stock and no incoming. Please refer to your salesman.` (unchanged) |

Any of the five, for a discontinued product, gets the marker `fetch.py::_item_line` already
prints for compact/detailed: a second line `⚠️  *(PRODUCT DISCONTINUED)*` under the verdict.
A dealer ETA line (`CODE: ✅ ETA dd/mm/yyyy`, `CODE: No ETA`) gets the same.

## Change list

1. `inventory_service.py`: the availability entry carries `is_discontinued` (bool).
2. `presenters.py::_stock_availability`: flags gain `discontinued`.
3. `incoming_stock.py` + `eta_policy.dealer_view`: dealer rows carry `is_discontinued`;
   `presenters.py::_incoming_dealer` flags `discontinued`.
4. `presenters.py::_AVAILABILITY_TAILS` / `_availability_tail`: the two in-stock tails.
5. `label_catalog.py`: ms + zh rows for the two new tails; the retired `{available} available. REFER` row goes (the bare REFER row stays, other lines print it).
6. Tests red-first (MCP + backend), the chat-language doc table, the handtest script.

Side effect, stated: an in-stock line no longer ends with the refer, so `_stamp_refers`
stamps it `refers_to_salesman = False` and it is no longer logged as a Customer ask
(consistent with the 1 Oct refer-only ruling: Customer asks logs the replies that referred).
