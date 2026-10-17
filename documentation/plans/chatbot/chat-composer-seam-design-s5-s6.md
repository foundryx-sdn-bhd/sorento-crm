# CHAT-COMPOSER seam design: S5 order / document status and S6 product info + price (coder-facing)

Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md`, card `chat-composer-behaviour-card.md` section 4 "Order / document
status", "Product info / spec", "Price", rules R1-R13, crew answer Q3 (section 9); UAC S5 and S6 lines. Builds on
`chat-composer-seam-design.md` ("Lead decisions 17 Oct" binds), `-s2.md` section 10a and `-s3.md` section 7a. Read-only
design. Line numbers on HEAD 0eb8361c1f (S0-S3a landed). Open PR hunks read with `gh pr diff 1744|1747|1755 --repo
foundryx-sdn-bhd/sorento-crm` on 17 Oct (heads 3f3bf8101e, f3b0c5dc21, ef8f12f320).
Paths: B = `sorento_crm_backend/app/services/chatbot`, A = `B/lanes/business/answer.py`, F = `B/lanes/business/fetch.py`,
BI = `B/lanes/business/__init__.py`, SB = `B/tail/scope_block.py`, SO = `B/so_status.py`, DA = `B/do_ask.py`,
SA = `B/turn/sub_asks.py`, LC = `B/label_catalog.py`, K = `B/composers/`, LF = `app/services/ledger_family.py`,
P = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`, E = `B/engine.py`.

**Binding rules applied:** gate pattern `if composers.enabled(): <new>` else today's code untouched; helpers land in the
slice that first prints them (S6 brings `format.money`, `format.present`, `format.missing_line`); the MCP presenter
stays the single printer (no MCP change in S5 or S6); R3 group name only, row-level `Customer:` fields stay; R4
truthful; R8 fit line unchanged; R13 money `MYR 1,234.00`; Q3 (a) un-asked empty field left out, asked one says
`No <attr> on record for <product>.`; spec-set count lines are #1744's (out); every reworded sentence gets ms/zh in the
same slice, and no sentence that also prints gate off is catalogued; no hunk in a function an open PR changes (S3 7a
precedent: `not_found_error_message` hunks far from #1744's 2-line comment hunk may land).

## Lead decisions 17 Oct (override anything below that disagrees)

- Build S5 (H1, H2, F1 gated, F2 fold into `composers/status.py`) and S6 (P1, P2, M1, M2 + helpers `money`,
  `money_field`, `present`, `missing_line`, `noun`) as designed; nothing waits on a PR merge (reviewer confirms H2 merges
  cleanly against #1744's comment hunk in `not_found_error_message`).
- Card corrections from this design: BI:346 and the sales report header already print the group name
  (`_customer_echo` -> `customer_header_words`); the A:3862-3885 header copy has drifted, so it is a GATED fold (F1),
  not a byte-identical one.
- OI-1 (a) land F1 now. OI-2 (a) leave the SO list's `family_words`. OI-3 (a) an order's own customer named in a
  sentence is a header: group name (R3).
- OI-4 (a): a price answer drops EMPTY un-asked fields only (Q3); a filled Dimensions stays (owner D12 ruling). This
  amends the card's "price answer shows Product Code, Description, List Price".
- OI-5 (a): the empty-field rule outside product envelopes waits for the flip PR.
- Added to S5 (lead, card R3; found in red-test review): the customer found bullet `• customer: <name> (+N more)`
  (answer.py ~3730; TRAIN cp0-620ea78a75 "BATHPRO ENTERPRISES [A/C I] (+5 more)") prints the group name only, gate
  on; other bullets keep their "(+N more)". Tests in test_composers_s5_status.py.
- The generic `No {noun} on record for {product}.` key must not translate a gate-off fit none line: pinned by a test
  (same guard as S4 OI-1).

## 0. Key findings that shape the design

| # | Finding | Effect |
|---|---|---|
| K1 | **Two of the card's three "raw-name writers" already print the group name.** BI:346 prints what it is handed, and its one caller (BI:401-405) hands `outstanding_customer_echo` (`lanes/business/services.py:459`) -> `outstanding_report_service._customer_echo` (:123) -> `LF.customer_header_words` over the DB `CustomerGroup.name`. The sales report header (P:2811 today, card's P:2927) prints the route's `customer_name`, which is the same `_customer_echo` (`sales_report_service.py:517`, `:763`; `sales_report_delivered.py:274`); it is also a report (passthrough tool, F:2941-2958), out of lane. | Only the order LABEL `CODE (ledger name)` prints a raw ledger name: A:3455-3458 (found bullets) and A:4370-4372 (status-filtered order miss). S5 seams H1, H2. |
| K2 | **A:3811-3915 is no longer a byte copy of `SB.search_scope_header`** (SB:286-331). Drift: `_AXES` (A:2504 one `Order` axis over `customer_order`/`order`/`order_number`; SB:37-83 a separate `Delivery order` axis for `customer_order`, REF-PROBES); SB `_printable` drops uuid words (SB:95), A does not; token raw lookup by `token_key` (SB:114) vs `_type_norm` + `by_raw_stripped` (A:3796); last-resort title fallback `title if title is not None` (A:3871) vs `display_name or title or code` (SB:189). Inputs are the same objects (`q`, `g`, `r`: A:3405, A:3202, A:3434). | A no-gate fold would turn `Order: DO1` into `Delivery order: DO1` on a miss. The fold becomes gated seam F1 (OI-1). |
| K3 | **`SO.period_reply` (SO:312-351) and the date tail of `DA.range_reply` (DA:121-154) give identical bytes** for the same (start, end, today, subject) apart from the noun (`sales orders` / `delivery orders`). Both callers parse dates with `DA._parse` (BI:1585-1586, DA:120-121). Neither sentence is catalogued (LC grep 0). | Card "become one" = byte-identical fold F2, no gate, 0 replay diffs, no ms/zh change. |
| K4 | **Two customer-word rules exist.** DO period subject (DA:81-88), scope headers (SB, A, `turn/compose.py:1102`) use `customer_header_words` (explicit group only, ungrouped ledger by its FULL name; owner ruling (b) 2 Oct). The SO list header (SO:376) and SO period subject (BI:1588) use `LF.family_words` (name-rule family, ledger marker stripped, LF:232-256). | Not a card S5 change; both print no count. Inventory row 8, OI-2. |
| K5 | **The turn's customer groups are a ContextVar for the whole turn** (`E._run_stages` E:4185-4195 -> `LF.customer_groups`), so `LF.customer_group_of(name)` inside `not_found_error_message` reads the DB group with no new fetch. | H1/H2 facts are already in hand. Tests wrap fixtures in `LF.customer_groups({...})`. |
| K6 | **`disp_by_uuid` is a printed label AND a grouping key.** A:3565 prints it (found bullets, A:3765 `• Order: ...`); A:3684-3688 uses it, through `_type_norm`, to decide whether one token named several distinct things. | H1 must not change `disp_by_uuid`: it fills a separate `shown_by_uuid`, read only at A:3565. |
| K7 | **Money has no key.** `_money` P:168-174 prints `MYR {float:.2f}` (fallback `MYR {v}`) on unkeyed fields `List Price` (P:1082, P:1116) and `Selling Price` (P:1081); `_line_money` P:620-626 prints `{currency} {v:.2f}` on KEYED fields `unit_cost`, `discount_per_unit`, `unit_cost_after_discount` (P:635-640). `SA:748` prints `{currency} {cost:,.2f}` with `SA._currency` (SA:873-875) mapping MYR to `RM`. Reports print `RM 1,234.50` (`_rm_money` P:2700, P:3284, P:3334): out of lane. | Decision (section B4): re-print at print time by field key / label from the presenter's exact shape (the inverse of `_money`), no MCP change, so recorded envelopes show the gate-on diff. Rejected: MCP additive keys (recorded envelopes lack them, gate-on replay proves nothing; second deploy unit); changing `_money` (ungated, n8n sees it, breaks AC-G1). |
| K8 | **The field set is decided in `F._project_product_specs` (F:1897-2145)**, called only for `result_type == "products"` (F:3028-3031). D12 (owner, 8 Sep, F:1785-1795) keeps every base field on every ask (`kept_base` F:2080). The presenter's only empty marker is `Not defined` on List Price / Dimensions (P:1116-1117); the builder drops every other empty value. Description is dropped when equal to the name and Product Name when equal to the code (P:204, P:1114-1115), so their absence is NOT "no value on record". | P1 (empty un-asked base field left out) keys on `Not defined` / empty only. P2 base-field lines exist for `list_price` and `dimensions` only (policy map `turn/policy_rows.py:397-411`); `description` / `name` / `cost` / `brand` print no line. Price field set beyond Q3 contradicts D12: OI-4. |
| K9 | **Translation of product replies is the final pass only** (products are not in `_LOCALIZED_TOOLS` F:2766-2778). A whole-line LABELS template is matched by `Localizer.lines` (LC:891-905, `_text` LC:874-889, "most fixed text wins"). The fit none lines `No {accessory} is on record for {code}.` (`product_find.py:148-155`) each carry their own key (`_kind_labels` product_find.py:183-197; `_NOUNS_I18N` covers seat cover, WC, mirror, shelf, cabinet = every `KINDS` noun, `product_compatibility_service.py:59-63`) with more fixed text than `No {noun} on record for {product}.` | The new generic key cannot take a fit none line today; a KIND added without nouns would (risk R2, pinned by a test). |
| K10 | **Overlap.** None of #1744 / #1747 / #1755 touches SO, DA, SA, LC, LF, SB, K or P. #1744 in A: `near_miss_reply`, `unknown_values_reply`, `build_set_header`, and inside `not_found_error_message` one 2-line comment (now A:4460-4461). #1744 in F: `entity_ids_transformer` (~F:1229) and `output_structurer` from `set_withheld` (now F:3551) to `attachments` (~F:3670). | S5 and S6 seams sit outside every PR function except `not_found_error_message` (H1 ~1000 lines, F1 ~540 lines, H2 ~90 lines from the comment hunk; S3 7a precedent). Nothing waits on a merge. |

## Part A. S5 order / document status

### A1. Helper that lands in S5 (`K/status.py`)

| Helper | Output | Note |
|---|---|---|
| `status.period_question(start: date \| None, end: date \| None, *, today: date, subject: str \| None, noun: str, max_days: int) -> str \| None` | the bytes `DA.range_reply` prints from its `today = ...` line on (DA:120-154), with `delivery orders` replaced by `{noun}` | Pure: `today` and `max_days` are passed in. `_month`, `_ddmmyyyy`, `_span` move into `K/status.py` byte for byte; DA keeps them as aliases (the S0 F-a pattern). `K/status.py` imports nothing from engine / head / tail / dialogue / turn.compose / lanes. |

No customer-word helper: H1/H2 call `customer_header_words([(c, customer_group_of(c))])`, already imported at A:37.

### A2. Inventory: every customer header / order-status writer

Verdicts: **C** canonical already; **G** gate-on seam; **F** byte-identical fold; **S** stays; **W** waits.

| # | Writer (file:line) | Today | Verdict |
|---|---|---|---|
| 1 | BI:346 `_outstanding_scope_filter_lines` `Customer:` | handed the group-folded echo (K1); report scope question | C (card claim refuted) |
| 2 | P:2811 `_sales_report_header` `*Customer:*` | route `_customer_echo` (K1) | C; report, out of lane |
| 3 | P:2382 outstanding header, P:3015 top selling `Customer:` | route `_customer_echo` (`outstanding_report_service.py:220`, `sales_report_service.py:763`) | C; reports |
| 4 | SB:130-191 `_axis_words`, SB:247-272 `_focus_words`, SB:286-331 `search_scope_header` (hit path, `answer_bridge.apply_scope_block` :119-166) | `customer_header_words` | C (the one header writer) |
| 5 | A:3811-3881 `axis_words` + A:3891-3915 head in `build_breakdown_msg` (miss path) | drifted copy of #4 (K2) | G (F1), OI-1 |
| 6 | DA:81-88 `_subject` (DO period question) | `customer_header_words` | C |
| 7 | `turn/compose.py:1102` `_subject_line` | `customer_header_words` | C |
| 8 | SO:376 SO list header, BI:1588 SO period subject, SO:417-427 per-row group | `family_words` / `group_names` (K4) | S (OI-2) |
| 9 | A:3452-3460 order arm of the `disp_by_uuid` loop -> found bullet A:3765 | `• Order: DO123 (HANLIM TRADING SDN BHD [A/C I])` | G (H1), OI-3 |
| 10 | A:4366-4392 status-filtered order miss | `Order DO123 (HANLIM TRADING SDN BHD [A/C I]) hasn't been delivered yet.` / `... has no outstanding items - it looks already delivered or closed.` | G (H2), OI-3 |
| 11 | SO:77 `_card` `*SO421624* - NAME`, P:432 DO rows `*Customer:*` (`debtor_name`) | row level | S (brief: row-level fields stay) |
| 12 | SO:312-351 `period_reply`, DA:108-154 `range_reply` | two copies (K3) | F (F2) |
| 13 | `ref_status.py:62` `and N more lines` | trailer with no pager | W Sets slice (S3 K1; owner 17 Oct "more" pages on) |
| 14 | SO:308 `_day` `5 Sep 2026`, `ref_status.py` `Issued 5 Sep 2026` | sentence dates, not field lines | S (S2 OI-1 scope = field lines; not a card S5 change) |
| 15 | P:788 orders summary title with the middle-dot separator | a summary item prints its fields, never its title (F:3303-3314; `_item_line` uses the title only with no fields, F:3346) | S. UNVERIFIED that no other reader prints the title. |
| 16 | `gate.py:338-346` `_cust_name`, `gate.py:1112-1123` `_rep_label` | customer picker labels | S (picker, not a header; W1755 if ever touched) |
| 17 | SO:272-305 misses | S3 M5 | done |

### A3. Gate-on seams

| Seam | Where | Facts read | Gate-on text | Downstream readers, how they keep working | ms/zh |
|---|---|---|---|---|---|
| **H1** | A:3452-3460 (order arm of the display loop): gate on, also fill `shown_by_uuid[uuid] = f"{code} ({words})"` (or `words` when no code) with `words = customer_header_words([(customer, customer_group_of(customer))])`; A:3565: gate on, `base = shown_by_uuid.get(uuid) or disp_by_uuid.get(uuid)`. `disp_by_uuid` itself is untouched. | `display.customer_name`, `canonical_code`, the turn's group map (K5) | `• Order: DO123 (HANLIM TRADING SDN BHD)`; an ungrouped ledger prints as today (its full name, owner ruling (b)) | A:3684-3688 grouping key reads `disp_by_uuid`, unchanged (K6). `bare_label` / representative pick (A:3598+) compares typed tokens to labels: UNVERIFIED that it ignores the bracket; tester pins a typed DO number still picks its row. No reader keys on `• ` lines (grep). | none: the sentence frame is uncatalogued today (existing gap); the name is a value |
| **H2** | A:4370-4372 `label` | `display.customer_name`, group map | `Order DO123 (HANLIM TRADING SDN BHD) hasn't been delivered yet.`; `Order DO123 (HANLIM TRADING SDN BHD) has no outstanding items - it looks already delivered or closed.` | The escalate offer is still appended to the LAST line with a space (AC-1863, A:4396-4398), so `tail/compose.MARKERS`, `dealer_stock._ESCALATION`, `reply_ladder._ESCALATE_TO_TEAM_RE` see the same suffix. No reader of "hasn't been delivered" (grep: A only). | none (as H1) |
| **F1** | A:3891-3915, the `if is_order_scope:` head block of `build_breakdown_msg`: gate on, `head = scope_block.search_scope_header(domain=domain_hint, qf=q, gate_json=g, resolver_json=r)` and `parts.append(head)` when not None; the gate-off block stays. A already imports from SB (A:39). | `q`, `g`, `r`, `domain_hint` (same objects the hit path reads; UNVERIFIED that `entities_list` A:3426 equals `q["entities"]`, which SB reads, tester checks) | the hit path's header bytes: `Delivery order: DO123` where today prints `Order: DO123`; a uuid word is dropped; other K2 drifts as they occur | Nothing reads the miss header lines (`answer_bridge._ORDER_DATE_LINE_RE` runs on the hit path only). The miss sentence, offer and widen invite after the header are unchanged. | `Customer` / `Product` labels catalogued (LC:124, LC:210); `Order`, `Delivery order`, `Dates` are not, today and gate on: no change |

### A4. Byte-identical fold, no gate (L1 replay 0 diffs)

| Fold | Copies | Into | Note |
|---|---|---|---|
| **F2** | DA:120-154 (date tail of `range_reply`) and SO:320-351 (`period_reply` body) | `K/status.period_question`; `range_reply` keeps its guards (DA:111-119) and subject (DA:126), then returns `period_question(..., noun="delivery orders", today=today_myt(), max_days=MAX_DAYS)`; `period_reply` returns `period_question(..., noun="sales orders", ...)` | Same `today_myt()`, same `MAX_DAYS`; uncatalogued sentences, so ms/zh unchanged. The subject rule difference (K4) stays with the callers. |

Rejected fold: A `axis_words` into SB (K2, not byte-identical; it is F1, gated).

### A5. Overlap per seam

| Seam | File / function | Open PR touching it | Lands |
|---|---|---|---|
| H1 | A `not_found_error_message` (A:3452-3460, A:3565) | #1744 (comment hunk A:4460-4461, ~1000 lines away) | now (S3 7a precedent) |
| H2 | A `not_found_error_message` (A:4370-4372) | #1744 (same hunk, ~90 lines away) | now; reviewer confirms the merge is clean (context lines do not overlap) |
| F1 | A `build_breakdown_msg` (nested, A:3891-3915) | #1744 (same hunk, ~540 lines away) | now, if OI-1 (a) |
| F2 | DA, SO, new `K/status.py` | none | now |

### A6. Red tests (S5; `tests/chatbot/test_composers_s5_status.py`; gate on = `monkeypatch.setattr(settings, "chat_composer_enabled", True, raising=False)`; today 2026-10-17 frozen via `do_ask.today_myt`)

| Test | Assertion |
|---|---|
| `test_period_question_do_bytes` | `DA.range_reply` over a grid equals today's literals: (None, None, subject `HANLIM TRADING SDN BHD`) -> `"Which period for HANLIM TRADING SDN BHD?\n- This month (Oct 2026)\n- Last month (Sep 2026)\nOr type a month (e.g. August) or dates (e.g. 15 Sep to 10 Oct)."`; (2026-04-01, 2026-09-30) -> `"That is 6 months (01/04/2026 to 30/09/2026). I can show up to 31 days of delivery orders at a time:\n- Sep 2026\n- Apr 2026\nOr type a month or dates."`; (2026-09-01, None) -> `"That is 47 days (01/09/2026 to 17/10/2026). I can show up to 31 days of delivery orders at a time:\n- Oct 2026\n- Sep 2026\nOr type a month or dates."`; (2026-09-01, 2026-09-20) -> None; swapped ends; future range |
| `test_period_question_so_bytes` | same grid through `SO.period_reply`, `sales orders` in place of `delivery orders`, literals as above |
| `test_period_fold_has_no_gate` | both functions return the same bytes in both gate states |
| `test_order_miss_group_name_gate_on` | status-filtered miss (delivered) under `customer_groups({"HANLIM TRADING SDN BHD [A/C I]": "HANLIM TRADING SDN BHD"})`: text contains `Order DO123 (HANLIM TRADING SDN BHD) hasn't been delivered yet.` |
| `test_order_miss_gate_off_unchanged` | same input gate off: `Order DO123 (HANLIM TRADING SDN BHD [A/C I]) hasn't been delivered yet.` |
| `test_order_miss_outstanding_group_name_gate_on` | `Order DO123 (HANLIM TRADING SDN BHD) has no outstanding items - it looks already delivered or closed.`; escalate offer still on the same line after one space |
| `test_order_miss_ungrouped_unchanged_gate_on` | no group map: gate-on text == gate-off text |
| `test_found_bullet_order_group_name_gate_on` | found bullets: `• Order: DO123 (HANLIM TRADING SDN BHD)`; gate off `• Order: DO123 (HANLIM TRADING SDN BHD [A/C I])` |
| `test_order_token_grouping_unchanged_gate_on` | two DO matches for one token, two ledgers of one group: the several-things verdict (A:3684-3700) and the reply equal gate off apart from the bullet name |
| `test_miss_header_is_scope_block_gate_on` (F1) | for fixtures (customer only; DO number placed as `customer_order`; brand + window): the head of `build_breakdown_msg` == `search_scope_header(domain, q, g, r)`; DO fixture prints `Delivery order: DO123` |
| `test_miss_header_gate_off_unchanged` (F1) | gate off DO fixture prints `Order: DO123`; customer fixture `Customer: HANLIM TRADING SDN BHD\nProduct: all products\nDates: all dates` |
| `test_s5_output_clean` (property) | no `accounts)`, no `and N more` after a customer name, no middle-dot, no UUID in any S5 gate-on fixture (AC-G3) |
| `test_composers_import_boundary` (extend) | `K/status.py` imports nothing from engine / head / tail / dialogue / turn.compose / lanes |

Existing tests that pin gate-off text (stay green, tester lists exact asserts): `test_do_ask_range.py`, `test_do_ask_range_engine.py`, `test_do_period_skip_do_list.py`, `test_do_period_skip_so_list.py`, `test_so_list.py`, `test_so_number_ask.py`, `test_do_reveal_bypasses.py` (period text); `test_do_field_reveals.py`, `test_s6c_answer_lane.py`, `divergences.py` ("hasn't been delivered yet"); `test_customer_scope_lane.py`, `test_rearch_r5_production_decides.py`, `test_rearch_r12_handpass12_d.py` (scope header lines, hit or miss: tester checks which).

## Part B. S6 product info / spec + price

### B1. Helpers that land in S6 (`K/format.py`)

| Helper | Output | Note |
|---|---|---|
| `format.money(amount, currency="MYR") -> str` | `f"{currency} {Decimal(str(amount)):,.2f}"`: `MYR 1,234.00`, `MYR 275.00`, `CNY 12,345.60`, `MYR -1,234.50` | R13 |
| `format.money_field(f) -> Any` | for a field with key in `MONEY_KEYS = {"unit_cost", "discount_per_unit", "unit_cost_after_discount"}` or label in `MONEY_LABELS = {"List Price", "Selling Price"}` whose string value fullmatches `([A-Z]{2,3}) (-?\d+\.\d{2})` (the exact shape of `_money` / `_line_money`): `money(amount, currency)`; any other field or value (`Not defined`, `MYR abc`, a currency-less `_line_money` value) unchanged | Labels are used where the presenter sends no key, as `_PRODUCT_IDENTITY_LABELS` (F:1792) already does |
| `format.present(v) -> bool` | False for None, `""`, whitespace, `Not defined`; else True | the presenter's empty markers (K8) |
| `format.missing_line(noun: str, product: str) -> str` | `f"No {noun} on record for {product}."` | lead decision; `noun` built by the caller |
| `format.noun(label: str) -> str` | each Title-case word lower-cased, other words kept: `Weight` -> `weight`, `List Price` -> `list price`, `Seat cover material` -> `seat cover material`, `WELS rating` -> `WELS rating` | one rule, used for spec labels and base labels |

### B2. Inventory: every product info / spec / price formatter

| # | Producer (file:line) | Today | Verdict |
|---|---|---|---|
| 1 | P:1116 `_products` List Price | `MYR 1234.00` or `Not defined` | S at source; G downstream: money (M1), empty drop (P1), asked-and-empty line (P2) |
| 2 | P:1117 `_products` Dimensions | `L x W x H mm` or `Not defined` | S at source; G: P1, P2 |
| 3 | P:1081-1082 `_promotion_products` Selling / List Price | `MYR 1234.00` (empty dropped by builder) | G: M1 |
| 4 | P:635-640 `_po_last_cost` keyed cost fields | `CNY 1234.50` | G: M1 |
| 5 | P:168 `_money`, P:620 `_line_money` | single printers | S (MCP stays the printer) |
| 6 | F:2059-2078 no-ask branch (`fields_out = list(base)` + `Specs:`) | every base field, `Not defined` included | G: P1 on base; `Specs:` line S |
| 7 | F:2080-2120 ask branch (`kept_base` D12 + matched spec fields) | `*Dimensions:* Not defined` on a price ask (TRAIN cp0-245d689a0c) | G: P1, P2; field set beyond Q3 = OI-4 |
| 8 | F:2125-2136 spec miss entry + F:3460-3462 printer | `*Weight:* not recorded for A, B, C, D, E (+2 more)` | G: P2 (one line per product, in its block) |
| 9 | F:2137-2142 hidden spec | `*Weight:* not available` | S (card s.5 "I can't share the X." is not an S6 change) |
| 10 | F:3336-3346 `_item_line` field value printer | `_fmt_value(value)` | G: M1 + P2 print |
| 11 | F:1855-1890 `_discontinued_status_lines` | status lines | S |
| 12 | SA:748 + SA:873-875 last cost | `Last cost: RM 1,234.00 / unit (PO, date)` | G: M2 (`MYR`, R13) |
| 13 | P:2700 `_rm_money`, P:3284, P:3334 | `RM 1,234.50` | S: reports, out of lane (card s.5) |
| 14 | E `_run_answer` fit line (`find.fit_lines` find.py:405-445) | `Seat cover for X is Y.` | S (R8) |
| 15 | `product_find.py:61`, :129-155 none lines | `No seat cover is on record for X.` | S (their own keys; K9) |
| 16 | P:2058-2062 `Here are N of the M matching products.`; A:2795 `breakdown_lines` | page / count lines | S: Sets slice and #1744 (out) |
| 17 | `alternatives.fold` product alternative blocks, `crossdomain_render` A:886 | other row printers | UNVERIFIED whether a price prints there (tester greps; if it goes through `_item_line` it gets M1 for free) |
| 18 | `turn/compose.py:143` `_render_row` | hand-built envelopes only | S |

### B3. Gate-on seams

| Seam | Where | Facts read | Gate-on text | Downstream readers, how they keep working | ms/zh |
|---|---|---|---|---|---|
| **P1** (Q3) | `_project_product_specs`: F:2071 no-ask `fields_out` and F:2080-2083 `kept_base` filter, gate on, keep a base field only when `format.present(value)` or an asked word names it (P2 then replaces it) | field `value`, `label`, asked words | cp0-245d689a0c: the `*Dimensions:* Not defined` line goes; `*List Price:* MYR 275.00` stays | `items[].fields` are also `answers` (state). Grep: no chatbot reader of `Not defined` or of the `List Price` / `Dimensions` labels outside F (risk R3). `_rendered_product_count` counts items, not fields. | nothing printed |
| **P2** (R4) | `_project_product_specs` item loop (F:2112-2120): gate on, per item, for each asked word with no value on that item append `format.missing_line(noun, code)` to `it["missing_lines"]`: a spec word with no hit (today's `missed_codes` condition, F:2118-2119), or a base word whose column (`turn/policy_rows.py:397-411`) is `list_price` / `dimensions` and whose field is not `present` (that field is then dropped). `noun` = `format.noun(vocab_label_for(norm) or asked_word)` for spec, `list price` / `dimensions` for base. Gate on, ordinary `spec_miss:` entries are not written; `spec_hidden:` entries are (F:2137-2142 unchanged). `_item_line` (F:3336-3346): gate on, append `"\n" + line` per `it.get("missing_lines")` after the field lines, before the flags. | asked words (parser `requested_attributes`, a closed vocabulary), item code (`_item_code` F:1835), vocab | `1. *Product Code:* SRTWC286-SH\n...\nNo weight on record for SRTWC286-SH.`; price missing: `No list price on record for X.` in place of `*List Price:* Not defined` | Lines inside a block do not start `N. `, so `_line_number`, `_NUMBERED_LINE`, `pickers._annotate_lines` are unaffected. Two company rows of one code each carry their own line (card s.5). `description` / `name` / `cost` / `brand` asks print no line (K8). `it["missing_lines"]` rides in `answers` state: inert to every reader (keys read are `fields`, `title`, `flags`, `id`). | NEW LABELS keys (whole line, not INLINE): `"No {noun} on record for {product}."` ms `Tiada {noun} direkodkan untuk {product}.` zh `没有 {product} 的{noun}记录。`; `"No list price on record for {product}."` ms `Tiada harga senarai direkodkan untuk {product}.` zh `没有 {product} 的标价记录。`; `"No dimensions on record for {product}."` ms `Tiada dimensi direkodkan untuk {product}.` zh `没有 {product} 的尺寸记录。` (pattern of the fit none lines product_find.py:192-193; nouns from LC:457-458; crew / translation memory confirm wording). None prints gate off. Spec nouns stay English inside (registry labels are English by design, LC ~448). |
| **M1** (R13) | `_item_line` F:3339: gate on, `_fmt_value(composers_format.money_field(f))` in place of `_fmt_value(jsc.get(f, 'value'))` | field key / label / value (K7) | `*List Price:* MYR 1,234.00`; `*Selling Price:* MYR 1,050.00`; `*Cost / unit:* CNY 12,345.60`; values under 1,000 unchanged | Print-time only: `answers` keep the presenter's value. No chatbot reader of `MYR` text (grep). | values are never translated; labels as today |
| **M2** (R13) | SA:873-875 `_currency`: gate on, returns `MYR` where it returns `RM` today (`""`, `MYR`, `RM`); amount already `{:,.2f}` (SA:748), so equal to `format.money(cost, currency)` | `last.currency` | `Last cost: MYR 1,234.00 / unit (PO-1, <date>)` | DIRECT_ONLY `COST_VALUE` (SA:129) unchanged; `{currency}` is a token, so the ms/zh fill is unchanged | no key change |

### B4. Money: why print-time and by key / label

The presenter prints money in one exact shape and sends no number beside it; `money_field` is the inverse of that one
printer, applied where the lane prints field values (the S2 `format.label` seam sits beside it in `_field_label`
F:2780-2789). It works on recorded envelopes, so the gate-on replay lists every changed price. The MCP is untouched,
so n8n and `crm_find` keep today's bytes. Trigger to move it into the presenter: the flip PR (gate removed), when the
MCP can print `MYR 1,234.00` for every consumer at once.

### B5. Byte-identical folds

None in S6: the spec miss line, `_money` and the `RM` currency word are divergent, not duplicates.

### B6. Overlap per seam

| Seam | File / function | Open PR touching it | Lands |
|---|---|---|---|
| P1, P2 (projection) | F `_project_product_specs` (F:1897-2145) | none | now |
| P2 print, M1 | F `output_structurer` nested `_item_line` (F:3336-3346) | #1744 changes `output_structurer` from F:3551 on (~210 lines below) | now (outside #1744's hunk range named in the brief) |
| M2 | SA `_currency` | none | now |
| LC keys | LC LABELS | none | now |

### B7. Red tests (S6; `tests/chatbot/test_composers_s6_product.py`, `test_composers_s6_money.py`)

| Test | Assertion |
|---|---|
| `test_money` | `money(1234) == "MYR 1,234.00"`, `money(275) == "MYR 275.00"`, `money("1234567.5") == "MYR 1,234,567.50"`, `money(-1234.5) == "MYR -1,234.50"`, `money(12345.6, "CNY") == "CNY 12,345.60"` |
| `test_money_field` | List Price `MYR 1234.00` -> `MYR 1,234.00`; key `unit_cost` `CNY 12345.60` -> `CNY 12,345.60`; List Price `Not defined` / `MYR abc` unchanged; Description `MYR 1234.00` unchanged |
| `test_present_and_noun_and_missing_line` | `present(None/""/"  "/"Not defined")` False, `present("MYR 1.00")` True; `noun("List Price") == "list price"`, `noun("WELS rating") == "WELS rating"`; `missing_line("weight", "SRTWC286-SH") == "No weight on record for SRTWC286-SH."` |
| `test_item_line_money_gate_on` / `_gate_off` | products envelope List Price `MYR 1234.00`: `*List Price:* MYR 1,234.00` / `*List Price:* MYR 1234.00` |
| `test_promotion_products_money_gate_on` | Selling Price `MYR 1050.00` -> `MYR 1,050.00`, List Price likewise |
| `test_po_last_cost_money_gate_on` | the three keyed cost fields re-printed with thousands; gate off unchanged |
| `test_sub_asks_last_cost_myr_gate_on` | gate on `Last cost: MYR 1,234.00 / unit (PO-1, <date>)`; gate off `Last cost: RM 1,234.00 / unit (PO-1, <date>)` (`<date>` = `SA._date` output, tester pins) |
| `test_price_answer_cp0_245d689a0c_gate_on` | envelope of TRAIN cp0-245d689a0c: gate on == `"Here are the matching products.\n\n1. *Product Code:* SRT79-SS-BL\n*Description:* SORENTO BIDET C/W TWO WAY TAP WITH HOLDER (G1/2 HOSE BIB TAP) SRT79-SS-BL\n*List Price:* MYR 275.00\n\n_Data last updated: 03/10/2026 18:42:43_"` |
| `test_price_answer_cp0_245d689a0c_gate_off` | same envelope gate off == the literal above with `\n*Dimensions:* Not defined` after the List Price line |
| `test_price_answer_keeps_filled_dimensions` (OI-4 (a)) | cp0-7ff78f4770 shape: `*Dimensions:* 150.00 x 150.00 x - mm` stays gate on |
| `test_price_missing_gate_on` | ask `["price"]`, List Price `Not defined`: no List Price line; block ends `No list price on record for X.`; gate off `*List Price:* Not defined` |
| `test_dimensions_missing_gate_on` | ask `["size"]`, Dimensions `Not defined`: `No dimensions on record for X.` |
| `test_no_ask_card_drops_not_defined_gate_on` | no attribute: no `Not defined` line; `Specs:` line byte-identical |
| `test_spec_miss_one_line_per_product_gate_on` | ask `["weight"]`, items A, B with no weight (vocab `{"weight": "Weight"}`): block 1 ends `\nNo weight on record for A.`, block 2 ends `\nNo weight on record for B.`; no `not recorded` anywhere |
| `test_spec_miss_gate_off_unchanged` | same input gate off prints `*Weight:* not recorded for A, B`; seven items print `*Weight:* not recorded for A, B, C, D, E (+2 more)` |
| `test_two_company_rows_each_carry_line` | one code, two companies, both no weight: each company block carries its own line |
| `test_description_and_name_asks_print_no_line` | ask `["description"]` on an item without a Description field: no `No description on record` |
| `test_hidden_spec_line_unchanged` | hidden key asked: `*Weight:* not available` in both gate states |
| `test_fit_line_unchanged_gate_on` (R8) | fit line + price answer: the fit line bytes and position equal gate off |
| `test_missing_lines_translate` | `Localizer` ms / zh: `No weight on record for X.` -> `Tiada weight direkodkan untuk X.` / `没有 X 的weight记录。`; `No list price on record for X.` -> the specific key |
| `test_fit_none_lines_translation_unchanged` | for every `KINDS` kind, `none_line(kind)` and `no_host_line(kind)` filled with a code translate in ms and zh exactly as their own catalogue entry (the generic key never wins) |
| `test_answers_state_keeps_identity` | `answers` items keep Product Code (and Company on two-company rows) gate on |
| `test_s6_output_clean` (property) | no `not recorded for`, no `(+`, no `Not defined`, no middle-dot, no UUID in any S6 gate-on fixture |

Existing tests that pin gate-off text (stay green): `test_product_spec_projection.py`, `test_spec_visibility_projection.py`,
`test_chat_product_labels.py`, `test_discontinued_ask.py`, `test_growth_r1_review_fixes.py`, `test_attribute_asks_round9.py`,
`test_multi_ask_runner.py`, `test_multi_ask_golden.py`, `test_chat_language_slice2.py`, `test_chat_language_slice3*.py`
(prices and `RM`; tester lists the exact asserts).

## C. Open items for crew (5)

- **OI-1 The miss header fold is not byte-identical (K2).** (a) F1 as a gated seam; the intended gate-on diffs
  (`Delivery order:` for a DO number, uuid words dropped) go in the replay review; the copy is deleted at the flip.
  (b) Leave the copy until the flip PR and fold there. Recommend (a): one header writer is the card's S5 point, the
  label then matches what the hit path already prints for the same DO, and it lands ~540 lines from #1744's hunk.
- **OI-2 Two customer-word rules (K4).** SO list header + SO period subject use `family_words` (name-rule family,
  marker stripped); every other header uses `customer_header_words` (explicit group, ungrouped ledger by full name,
  owner ruling (b) 2 Oct). (a) Leave: not a card change, both print no count; trigger: an owner ask or a TRAIN item.
  (b) Gate on, SO uses `customer_header_words` (an ungrouped multi-ledger family then prints each ledger's full name).
  Recommend (a).
- **OI-3 Is an order's own customer a header (H1, H2)?** The card and brief list A:3423/4324 as raw-name writers;
  `family_words`' docstring keeps the full ledger name on DO ROWS. (a) Fold to the group name (one order, one company
  word; R3 and the 2 Oct "never a ledger marker" rule). (b) Keep the ledger (row-level). Recommend (a).
- **OI-4 Price answer field set vs D12 (owner, 8 Sep).** Card s.4 Price: "shows Product Code, Description, List
  Price". D12 keeps every base field on every ask because a wrongly emitted `["price"]` dropped them. (a) Apply Q3
  only: an EMPTY un-asked field goes (fixes cp0-245d689a0c), a filled Dimensions stays (cp0-7ff78f4770 unchanged).
  (b) A price-only ask keeps Company / Code / Name / Description / List Price and drops a filled Dimensions too
  (reverses D12 for price; needs an owner ruling). Recommend (a).
- **OI-5 Q3 beyond product envelopes.** AC-1.2 says the empty-line rule reaches stock blocks "with S6 or the flip";
  other tools print `-` placeholders (P:1411) and the em dash from `_fmt_value`. (a) S6 covers product and price
  envelopes only; every other row block at the flip. (b) Extend P1 to `_item_line` for every tool now (needs an
  "asked" notion per tool that does not exist). Recommend (a).

## D. Risks (top 3)

| # | Risk | Mitigation |
|---|---|---|
| R1 | F1 changes the miss header in more ways than the `Delivery order` label (token fold, title fallback), giving unlisted gate-on diffs (AC-G2). | One fixture per K2 drift; the gate-on replay diff is read line by line for headers; fallback OI-1 (b). |
| R2 | The generic key `No {noun} on record for {product}.` translates a fit none line gate OFF if a KIND is added without `_NOUNS_I18N` nouns (today all covered, K9), breaking AC-G1 for ms/zh. | `test_fit_none_lines_translation_unchanged` over every `KINDS` entry; the base nouns get their own keys. |
| R3 | P1/P2 change `items[].fields`, which are also the `answers` state; a later turn that reads a dropped `Not defined` field, or a price printer outside `_item_line` (inventory row 17, UNVERIFIED), diverges. | Grep found no reader of those labels outside F; `test_answers_state_keeps_identity`; tester greps `alternatives.py` / `crossdomain_render` for money before the red commit. |
