# CHAT-COMPOSER seam design: S0 scaffolding + S1 stock (coder-facing)

Lane CHAT-COMPOSER, plan `PLAN-chat-composer-17oct.md`, card `chat-composer-behaviour-card.md` (v1).
Verified on worktree HEAD a221328079 (= origin/main 20c191d86 + docs). Read-only design, no code yet.
Paths: B = `sorento_crm_backend/app/services/chatbot`, P = `sorento_crm_mcp/sorento_crm_mcp/presenters.py`,
F = `B/lanes/business/fetch.py`, A = `B/lanes/business/answer.py`, C = `B/turn/compose.py`, E = `B/engine.py`,
AB = `B/answer_bridge.py`, T = `B/turn/task.py`, LC = `B/label_catalog.py`.

**Crew rulings, 17 Oct (card Q1-Q4, all option (a)), designed to as decided:**
Q1 #1744 merges first, so S1 never touches set headers, `build_set_header`, `breakdown_lines` or the set row path
(F:3474-3488). Q2 one env gate, gated merge, gate-off path never deleted in S1 (the flip PR deletes it); only
byte-identical duplicates fold now, with no gate. Q3 an un-asked empty field is left out; an asked one says
"No <attr> on record for <product>." (not used by S1, the helper lands in S0). Q4 one line
`Reply "more" to see the next N.` on every capped list; pickers stay uncapped (helper in S0, first printed by S3).

## Lead decisions 17 Oct (override any line below that disagrees)

- **Change (b) is dropped from S1.** The trailing `CODE - ` line is an owner ruling (round 6, ruling 2,
  `turn/task.py:394-397`); the card's "never a blank CODE - " was wrong and is withdrawn. G3 and G4 are removed. The
  bare presenter question vs the named one ("How many units do you need?" P:121 vs "How many units of X?" T:395)
  moves to S3 (questions), which also leaves S1 with no hunk in a function #1744 changes (OI-2, OI-3 closed).
- **No fact rebuild of dealer lines in S1.** The MCP presenter is already the single printer of the dealer verdict
  sentences; S1 does not copy them into the backend (`StockFact`, `fact_of`, `dealer_line` and the parity tests are
  not built; risk R3 gone). G1 changes only the miss line, through `composers.stock.miss_line`.
- **Helpers land in the slice that first prints them**: `Reply` in S3, `date` in S2, `more_line` in S3, `money`,
  `present` and `missing_line(noun, product) -> "No {noun} on record for {product}."` in S6. S0 is the gate plus the
  F-a fold plus the import-boundary test, so S0 and S1 run as ONE tester/coder round.
- **S0+S1 surface**: `composers/__init__.py` (`enabled()`), `composers/format.py` (`EM_DASH`, `ISO_RE`, `timestamp`,
  `value`, `label_line`), `composers/stock.py` (`miss_line`, `none_for`, `block`). Seams G1, G2, G5, G6; folds F-a,
  F-b, F-c.
- OI-1 ruled (a) by crew 17 Oct: G1 gate-on also prints each answered item as its full block via
  `composers.stock.dealer_block(item)` = the item title, then `\n⚠️  *(PRODUCT DISCONTINUED)*` when
  `flags.discontinued`, then `\nAlternative: {line}` per `flags.alternative_lines` entry: exactly what `_item_line`
  (fetch.py:3382-3425) prints for that item with numbering off and English text. Applies whenever `_stock_ask_reply`
  rebuilds the text (a miss or a question). Surface adds `dealer_block`.

## 0. Key findings that shape the design

| # | Finding | Effect |
|---|---|---|
| K1 | The MCP envelope already passes the raw dealer facts through: `_PASSTHROUGH_KEYS` includes `stock_availability` (P:138), stamped by `_stamp_refers` (P:2085-2086), and the lane keeps it (F:3728-3736). Each entry has `product_code`, `product_name`, `requested_qty`, `branch`, `eta` (dd/mm/yyyy, inventory_service.py:1794), `available_qty`, `is_discontinued`, `needs_quantity`, `alternatives`, `refers_to_salesman`. | S1 needs **no MCP change and no additive key**. Recorded envelopes (replay, fixtures) already carry the facts. |
| K2 | `_stock_ask_reply` (E:7545) rebuilds a dealer reply from item TITLES only (`_stock_answer_lines` E:7652), so with a miss the discontinued marker and `Alternative:` lines vanish (TRAIN cp0-0498229323 PREV vs BOT). | Not a card section 4 change, so S1 keeps it byte-identical. Open item for crew (section 8, OI-1). |
| K3 | Every quantity question goes through one function: `StockQtyTask.question` (T:383-405), called by `with_owed` (T:1066) and `after_reply`. The fill-in blank is a deliberate owner ruling: "round 6, ruling 2 ... one numbered line per product ending ' - ' for the dealer to fill" (T:397-400 comment). | Card change 4(b) reverses an older owner ruling. Needs crew confirmation before the gate flips (section 8, OI-2). |
| K4 | `MAX_SLOTS = 20` (T:209) caps the slots, so the "100+ rows" of TRAIN cp0-29c26e8809 cannot come from `question()` alone. UNVERIFIED: the full recorded reply (the excerpt shows 11 lines then "..."). | Tester checks `recorded.jsonl` before writing the (b) test's fixture. |
| K5 | The stock rows under an ETA miss (`crossdomain_render` A:886, rows A:1014-1034) are read back by `_RUNG_ROW_CODE_RE` (AB:1592, `^(?:-[ \t]*)?\*Product Code:\*`) in `_prefix_zero_note` (AB:1645). | Numbered rows (change 4(c)) break that read, so the gate-on path stamps the codes as data (section 3, G6). |
| K6 | All engine.py hunks of #1744 / #1747 / #1755 are in `_run_stages_body`, `turn_apply`, `_demand_qty_missing`, `_record_memory_trace`, `run_tail`, `_complete_canned_lane`, `_contact_brand_scope` and the top import block (lines 58-79). | S1's engine seams sit in `_stock_ask_reply` / `_dealer_names_the_miss` only, with lazy imports inside the function bodies (no top-of-file hunk). |

## 1. S0 scaffolding

### 1.1 Gate

| Item | Decision |
|---|---|
| Field | `chat_composer_enabled: bool = False` in `Settings`, `sorento_crm_backend/app/config.py`, placed right after `chat_topic_labeller_enabled` (config.py:190), with a 3-line comment naming the lane and `CHAT_COMPOSER_ENABLED`. |
| Not copied from the labeller | No `system_settings` column, no `runtime_settings.FIELDS` / `BOOL_FIELDS` entry, no migration, no `_TurnSwitches` plumbing. Q2 says one env gate, and the flip PR removes it. The labeller's DB override exists for an owner-flips-in-prod need this gate does not have. |
| Read | `composers/__init__.py`: `def enabled() -> bool: from app.config import settings; return bool(getattr(settings, "chat_composer_enabled", False))`. Read at CALL time, never at import, so a monkeypatch takes effect. Same `getattr` form as `chatbot_turn_on_worker` (E:1819, E:3237). |
| Who reads it | Only the S1 seams in section 3, each through `composers.enabled()`. A turn reads one process-wide value, so all seams of one turn agree. |
| Tests flip it | `monkeypatch.setattr(settings, "chat_composer_enabled", True, raising=False)` (pattern tests/test_runtime_settings.py:489). |
| Deploy | Every backend-image service that runs a turn needs the same value: backend_blue, backend_green, and the worker when `CHATBOT_TURN_ON_WORKER` is on. MCP services do not read it. Default unset = off = main's text. |
| Gate-on replay | The replay driver pins `SORENTO_ENV_FILE=crew/state/eval/replay.env` (crew-owned). UNVERIFIED: exporting `CHAT_COMPOSER_ENABLED=true` in the replay process's shell takes precedence over the env file (pydantic default is env var over env file; check `config._resolve_settings_env_file`). If it does, the gate-on replay lists every changed reply with no crew file edit. |

### 1.2 Package name: `B/composers/` (not `B/compose/`)

There is no import clash: every existing import is fully qualified (`chatbot.turn.compose`, `chatbot.tail.compose`,
E:79, AB:100-103, ~40 test files), and `tests/chatbot/test_import_boundary.py` allows any path under
`app/services/chatbot/`. It is renamed anyway because the codebase already has two `compose.py` modules, the
`compose()` / `compose_question()` / `crossdomain_compose()` functions and the aliases `turn_compose` /
`tail_compose`. A fourth `compose` makes grep, tracebacks and `from app.services.chatbot import compose` ambiguous.
`composers` has 0 code hits today (only prose in dealer_stock.py:24 and E:3054/5281). The card's section 2
`B/compose/` reads as `B/composers/`.

Placement constraints (verified):
- Outside `turn/`: `tests/chatbot/test_rearch_r4_deletions_and_cap.py:59` forbids "Did you mean" and
  "Which product do you mean?" in any `turn/*.py` (S3's `misses.py` will hold such sentences).
- `composers/*` imports only `app.services.chatbot.jsc`, `app.config` (lazily) and, under `TYPE_CHECKING`,
  `turn.pending.Pending`. Never `engine`, `head`, `tail`, `dialogue`, `turn.compose` or `lanes`, so that `turn/task.py`
  can import it (`test_rearch_s2_apply_is_pure.py:23` forbids turn importing head/dialogue/tail/engine) and so that
  no import cycle forms when S3 makes `turn/compose.py` call it.

S0 files: `composers/__init__.py` (gate + `Reply`), `composers/format.py`, `composers/stock.py` (empty in S0, filled by
S1). The other modules in card section 2 are created by their own slices, not as empty files now.

### 1.3 Types (`composers/__init__.py`, `composers/stock.py`)

```python
@dataclass(frozen=True)
class Reply:                      # card section 2 output
    text: str
    question: "Pending | None" = None   # the open question as data (turn.pending.Pending)
    offer: Any = None                   # turn.compose.Offer or None; stock never offers (dealer refers)

@dataclass(frozen=True)
class StockFact:                  # one dealer availability entry, from the passthrough (K1)
    code: str                     # product_code, else product_name; an entry with neither is skipped (P:1512 rule)
    requested_qty: Any
    branch: str | None            # in_stock | incoming | no_incoming | too_big | None
    eta: str | None               # as served, already dd/mm/yyyy
    available_qty: int | None     # only a real int >= 1 counts (P:1595-1600 rule)
    discontinued: bool            # entry.is_discontinued is True
    needs_quantity: bool
    alternatives: tuple["StockFact", ...] = ()   # only those with branch and a label (P:1556 `_printed_alternatives`)

def fact_of(entry: dict) -> StockFact | None   # reads the entry KEYS above, never a title
```
Staff stock rows need no new record: the envelope item (`{title, fields:[{key,label,value,granted_value?}], flags}`) is
already keyed data, and `stock.block()` reads it (section 3, G5).

### 1.4 `composers/format.py` (shared rules, card R1 / R13)

| Helper | Output | Replaces / must equal | Fold now? |
|---|---|---|---|
| `EM_DASH = "\u2014"`, `ISO_RE` | the constants | F:1644 `_EM_DASH`, F:1448 `_ISO_RE` | yes, moved; F keeps `_EM_DASH = format.EM_DASH`, `_ISO_RE = format.ISO_RE` |
| `timestamp(v) -> str | None` | `dd/mm/yyyy` or `dd/mm/yyyy HH:MM:SS` | body of F:1613 `_fmt_ts`, byte for byte | yes, moved; F keeps `_fmt_ts = format.timestamp` (alternatives.py imports it from fetch) |
| `value(v) -> str` | em dash for empty, Yes/No, ISO via `timestamp`, `*` to U+2217, list joined ", ", dict joined " em dash " | body of F:1647 `_fmt_value`, byte for byte | yes, moved; F keeps `_fmt_value = format.value` |
| `label_line(label, v) -> str` | `f"*{label}:* {value(v)}"` | the per-field line inside `_item_line` (F:3383-3387) | no: `_item_line` is the generic row printer for every tool, the flip/sets work owns it |
| `date(v) -> str | None` | `dd/mm/yyyy`, from `YYYY-MM-DD` with or without a time part; an already `dd/mm/yyyy` string passes unchanged; else None | new. Differs from `_fmt_ts` (never prints a time) and from A:854 `_fmt_xd_value` (which prints "-" for empty) | no (new rule; first used by S2) |
| `money(v, currency="MYR") -> str | None` | `MYR 1,234.00`; None when empty; `f"{currency} {v}"` when not numeric (parity with `_money`'s fallback) | new. P:168 `_money` (`MYR 12.00`, no separator) lives in the MCP process and cannot be imported; it stays for the gate-off path | no (first used by S6) |
| `present(v) -> bool` | False for None, "", whitespace, and the presenter's empty literals "-" (P:1411) and "Not defined" (P:1106) | new (Q3 rule) | no (first used by S6) |
| `more_line(n) -> str` | `Reply "more" to see the next {n}.` | new (Q4). Replaces 5 trailers in later slices (P:2280, B/ref_status.py:62, P:1652, T:891) | no (first printed by S3; its ms/zh catalogue key lands in that slice, LC rule "a new sentence gets its key in the same slice") |

Not folded, with the reason: `_fmt_xd_value` A:854 (prints "-" and date-only, so not byte-identical; it stays for the
gate-off ETA-miss block and goes in the flip PR); `_dmy` F:2811 (report paths, out of lane); `_money` P:168 and
`_qty` P:177 (MCP process); `sub_asks._qty` B/turn/sub_asks.py:869 (different compaction).

## 2. S1 inventory: every stock text producer, verdict per card section 4

Card section 4 Stock: Stays = dealer lines and the staff compact / detailed views. Changes = (a) one dealer miss wording,
(b) one quantity question per case, no blank `CODE - `, (c) the stock block under an ETA miss uses the numbered block.

| Producer (file:line) | Gate-on text | Seam |
|---|---|---|
| Dealer line `_availability_line` P:1545 / `_verdict_tail` P:1586 (MCP) | byte-identical (Stays) | none: the presenter stays the single printer (lead decision) |
| Staff detailed `_stock` P:1362, compact `_stock_compact` P:1451, printed by `_item_line` F:3382 | byte-identical (Stays) | none in S1 (the baseline layout (c) copies) |
| `_stock_ask_reply` E:7545 (dealer stock mix) | changes: miss line (a) only | **G1** |
| `_dealer_names_the_miss` E:7631 (dealer ETA ask with a miss) | changes: miss line (a) | **G2** |
| `dealer_stock.did_you_mean` dealer_stock.py:77-121 | byte-identical: "Couldn't find X. Did you mean Y?" already uses the target grammar | none |
| compose "No stock found for X." C:652 / C:682 (zero rows for a found product, a stock fact, not a miss) | byte-identical | none (its footer search C:676-690 is untouched) |
| AB:1663 `_prefix_zero_note` "No stock for X." and A:1104 `only_other_note` | byte-identical | fold, no gate (section 4) + **G6** read of the codes |
| `crossdomain_render` stock rows under an ETA miss, A:1014-1034 (`- *Label:*`), lead A:1037 | changes (c), origin_domain == "incoming" only | **G5** |
| `crossdomain_render` incoming rows under a stock miss (same loop, origin inventory) | byte-identical in S1 (ETA block is S2) | G5 skips it |
| Quantity question `StockQtyTask.question` T:383-405 | changes (b): owed lines lose the trailing " - " | **G3** (DROPPED from S1, lead decision: owner ruling keeps `CODE - `; named-vs-bare question goes to S3) |
| Presenter bare question "How many units do you need?" P:121, printed as the lane intro F:3328-3332 when items still need a quantity | changes (b): the named question instead | **G4** (DROPPED from S1, lead decision: owner ruling keeps `CODE - `; named-vs-bare question goes to S3) |
| Family pick `pick_question` T:865, `picks_question` T:1067 | byte-identical (a which-one question, not a quantity question; every option is already a code = its name, R5) | none |
| `sub_asks` "Stock: N" B/turn/sub_asks.py:750-760 | byte-identical (card names it a divergence but lists no Stock change) | none; later slice |
| crm_find "Code:/Stock:" P:1628-1660 | byte-identical | none. A later change needs an additive MCP key (the envelope carries only `response` text, P:1656-1660): pass `groups[].rows[]` facts through |
| Set stock rows / set dealer question F:3474-3488, `build_set_header` A:2944 | out of S1 (Q1) | none |
| `turn_runtime._lane_text_without_withheld_header` turn_runtime.py:5503 | not reached by S1 text (set path) | none |

## 3. S1 gate-on seams

Every gate-on branch has the shape `if composers.enabled(): <new>` else today's code unchanged, the import inside the
function body.

| Seam | Where the branch goes | Facts it reads (source) | Gate-on text (card change) | Downstream re-readers and how they keep working | ms/zh keys |
|---|---|---|---|---|---|
| **G1** | E:`_stock_ask_reply`, the `Couldn't find:` append at E:7616 becomes `parts.append(composers.stock.miss_line(misses))`; nothing else in the function changes (lines still from `_stock_answer_lines`, titles as served). | `misses = _unplaced_tokens(envelopes)` (E:7699) | gate-on `Couldn't find X, Y.`; gate-off `Couldn't find: X, Y.` (the helper holds both, fold F-c + change 4(a)). | `dealer_stock._ESCALATION` unaffected (no offer); `Localizer.tail` untouched (dealer lines unchanged); the miss line translates via LC:359 / INLINE LC:640. | "Couldn't find {names}." exists (LC:359, INLINE LC:640). No new key. |
| **G2** | E:`_dealer_names_the_miss`, only the `line = ...` statement E:7644 becomes `line = composers.stock.miss_line(misses)`. | `misses = _unplaced_tokens(envelopes)` | `Couldn't find X, Y.` (4(a)). The refer line stays last, through `refer.after` as today. | `_COMPOSE_MISS` (E:7628) still finds compose's "I could not find ..." line, which S1 does not change. S3, when it rewords that line, must change `_COMPOSE_MISS` in the same slice (or replace the text match with a structured `miss` flag). | same key as G1 |
| **G5** | A:`crossdomain_render`, the row loop A:1014-1034: gate-on AND `origin_incoming` (A:929) prints `composers.stock.block(n, it)` with `n` counting printed rows from 1 across all codes, instead of `f"- {field_lines}"` + markers. Gate-on also adds `"rendered_codes": [...]` (codes of the printed rows) to the `_xdBlock` dict built at A:1139. | the envelope items (`fields[].label`, `fields[].value` / `granted_value` via `_field_render_value` A:869, `flags.discontinued`, allocation flags) | `1. *Product Code:* SRT5734-WH` / `*Total:* 60 (O/S: 0)` / `*BRW:* 60 (O/S: 0)`: the main-path block (4(c), TRAIN cp0-5430419641, cp0-338d26fba4). Values through `format.value` (em dash for empty, as the main path), not `_fmt_xd_value`. Sort order and `seen_rows` de-dupe unchanged. Lead line A:1037 unchanged. | (1) `_RUNG_ROW_CODE_RE` AB:1592 does not match `1. *Product Code:*`: see G6. (2) `_NUMBERED_LINE` AB:488 sets a member-offer picker's first number after the highest `N.` in the text (AB:530), so a picker under this block starts at N+1, which is that code's own intent (no number clash); test it. (3) `pickers._annotate_lines` (lanes/business/pickers.py:71) annotates numbered lines of a roster; UNVERIFIED that it never runs over an `_xdBlock` text: test it. (4) `compose.crossdomain_compose` folds the block as TEXT only (comment A:1043-1047); no footer search inside it. The `_xdBlock` key set is graded by the s6c corpus replay (`test_s6c_answer_lane.py`); `rendered_codes` is added gate-on only. | lead "But here are the stock details for the requested products:" exists (LC:399); labels are translated line by line by `localizer.reply` (`_NUMBERED = r"\d+\. |- "` LC:788 accepts both shapes). No new key. |
| **G6** | AB:`_prefix_zero_note`, AB:1645: `rendered_codes = set(block.get("rendered_codes") or ()) or _block_product_codes(block.get("block"))`. No gate check: the key exists only when G5 ran gate-on. | `_xdBlock["rendered_codes"]` | none (keeps "No stock for X." naming only rendered zero codes) | replaces the English re-read on the composer path (card section 2, coupling 2) | none |

`composers/stock.py` S0+S1 surface: `miss_line`, `none_for`, `block` (lead decision; no `fact_of` / `dealer_line` /
`dealer_reply` / `quantity_question`). `miss_line(misses)` returns `Couldn't find: {names}.` gate-off and `Couldn't find {names}.`
gate-on, so the fold of the two E:7616 / E:7644 copies and the gate change are one helper.

## 4. Byte-identical folds, no gate (L1 replay must stay 0 diffs)

| Fold | Copies today | Into | Note |
|---|---|---|---|
| F-a | `_fmt_ts` F:1613, `_fmt_value` F:1647, `_EM_DASH` F:1644, `_ISO_RE` F:1448 | `composers/format.py` (`timestamp`, `value`, `EM_DASH`, `ISO_RE`); fetch.py keeps the four names as aliases | S0. fetch.py lines 1448-1668 are not in #1744's hunks (1229-1266, 3591-3715). |
| F-b | "No stock for {codes}." built twice: AB:1663 `f"No stock for {', '.join(codes)}."` and A:1104 `f"No {primary_word} for {', '.join(only_other)}."` | `composers.stock.none_for(word, codes)`; AB passes "stock", A passes `primary_word` | S1. Neither function is in an open PR's hunks. S2 reuses it for incoming. |
| F-c | "Couldn't find: {names}." built twice: E:7616 and E:7644 | `composers.stock.miss_line(misses)` (gate inside, section 3) | S1, together with G1/G2. |

Not folds (not byte-identical or cross-process): `REFER_TO_SALESMAN` P:984 vs T:42 (two processes; pinned by a test,
UNVERIFIED which: `tests/chatbot/test_refer_salesman.py`), `_fmt_xd_value` vs `_fmt_value`, the C:652 / C:682 "No
{label} found" pair (C:652 interpolates the policy label; equal only when it is "stock", UNVERIFIED which labels the
policy rows use), A:3862-3885 `search_scope_header` copy (status slice S5).

## 5. Overlap with open PRs

| File | Open PR hunks (function) | S1 hunks | Same function? |
|---|---|---|---|
| E | #1744 `_run_stages_body` 6894-6961; #1747 top imports, `turn_apply` 1163, `_run_stages_body` 4513-5047 / 7232, `_demand_qty_missing` 8369, `_record_memory_trace`, `run_tail`, `_complete_canned_lane`; #1755 imports 58, `_contact_brand_scope` 1078, `_run_stages_body` 4500-4764 / 7143 | `_stock_ask_reply` 7612-7617, `_dealer_names_the_miss` 7644 | no. Imports lazy, inside the two functions. The call sites E:7022 / E:7204 (inside `_run_stages_body`) are NOT edited. |
| F | #1744 `entity_ids_transformer` 1229-1266; `output_structurer` 3591-3715 (hunks labelled `_co_of_row`, a nested def) | F-a 1448-1668; G4 3328-3332 | **G4 is inside `output_structurer`, the function #1744 changes** (different lines, 3328 vs 3591+; textual merge expected clean). Unavoidable: the intro is chosen only there. If crew wants zero shared functions, G4 moves out of S1 to S3 (miss / clarify) and (b) ships as G3 only. |
| A | #1744 `near_miss_reply` 2867, `unknown_values_reply` 2892, `build_set_header` 2952-2983, `build_breakdown_msg` 4409 | G5 in `crossdomain_render` 1014-1034 + 1139; F-b 1104 | no |
| AB | none | G6 1645; F-b 1663 | no |
| T (turn/task.py) | none (#1747 touches turn/apply, chat_state, compose, context, pending, state, tail) | G3 401-405 | no |
| turn_runtime.py, turn/compose.py, turn/apply.py, resolve_gate.py, lanes/business/gate.py | #1744 / #1747 / #1755 | none | n/a: S1 does not touch them |
| config.py | none | S0 field | no |
| LC | none | none in S1 | no |
| P (MCP) | #1744 touches `catalog.py` only | none in S1 | no |

## 6. Red tests for the tester (test(red) commit first)

All under `tests/chatbot/`, Postgres-free where the function is pure. Gate on = `monkeypatch.setattr(settings,
"chat_composer_enabled", True, raising=False)`.

**S0 (`test_composers_s0.py`)**
| Test | Assertion |
|---|---|
| `test_gate_defaults_off` | `Settings().chat_composer_enabled is False`; with env `CHAT_COMPOSER_ENABLED=true` a fresh `Settings()` reads True |
| `test_enabled_reads_settings_at_call_time` | `composers.enabled()` is False, flips to True after monkeypatch, no reload |
| `test_fetch_helpers_are_the_format_helpers` | `fetch._fmt_value is format.value`, `fetch._fmt_ts is format.timestamp`, `fetch._EM_DASH == format.EM_DASH` |
| `test_value_matrix_unchanged` | `format.value` over [None, "", True, False, 5, 5.5, "2026-09-27T00:00:00", "2026-09-27T10:11:12", "*REPLACE", [1, None], {"a": 1, "b": ""}] equals the literal outputs captured from main's `_fmt_value` (pinned strings, not a call to the old code) |
| `test_label_line` | `label_line("Total", 60) == "*Total:* 60"`; `label_line("X", None) == "*X:* \u2014"` |
| `test_composers_import_boundary` | no module under `composers/` imports `engine`, `head`, `tail`, `dialogue`, `turn.compose` or `lanes` (AST scan, the pattern of `test_rearch_s2_apply_is_pure.py:46`) |

**S1 change (a): one dealer miss wording (`test_composers_stock_miss.py`)**
| Test | Assertion |
|---|---|
| `test_stock_mix_miss_gate_on` | `_stock_ask_reply` over one answered entry + unplaced tokens "cb890-bl", "cb890-GM- diy" (TRAIN cp0-0498229323 shape): text ends `Couldn't find cb890-bl, cb890-GM- diy.`; no `Couldn't find:` |
| `test_stock_mix_miss_gate_off_unchanged` | same input gate-off: `Couldn't find: cb890-bl, cb890-GM- diy.` exactly |
| `test_eta_miss_gate_on_refer_stays_last` | `_dealer_names_the_miss` over a text ending in "I could not find X." + refer: gate-on gives `...\n\nCouldn't find X.\n\nPlease refer to your salesman.` |
| `test_eta_miss_gate_off_unchanged` | same, gate-off: `Couldn't find: X.` |
| `test_miss_line_translates_ms_zh` | `localizer.reply` (ms, zh catalogue defaults) of the gate-on miss line gives "Tidak dapat menemui X." / "找不到 X。" |
| `test_dealer_did_you_mean_unchanged_gate_on` | `dealer_stock.did_you_mean` text identical in both gate states |

**S1 change (c): numbered stock block under an ETA miss (`test_composers_stock_xd.py`)**
| Test | Assertion |
|---|---|
| `test_eta_miss_stock_block_numbered_gate_on` | `crossdomain_render`, origin incoming, one compact row: block contains `1. *Product Code:* SRT5734-WH\n*Total:* 60 (O/S: 0)\n*BRW:* 60 (O/S: 0)`; no line starts with `- *` |
| `test_xd_block_equals_main_path_block` | for the same item, the gate-on row text equals what `output_structurer` prints for it on the product-code answer (numbered, loc IDENTITY) |
| `test_stock_miss_incoming_rows_unchanged` | origin inventory (incoming rows): identical in both gate states (S2 owns it) |
| `test_rendered_codes_stamped_gate_on_only` | `_xdBlock` has `rendered_codes` gate-on, has no such key gate-off |
| `test_zero_note_names_only_rendered_codes_gate_on` | `_prefix_zero_note` with two zero codes, one rendered: names only the rendered one, in both gate states |
| `test_member_offer_numbers_after_block` | a member-offer picker appended under the gate-on block starts at N+1 and a reply "1" does not resolve to a picker option (UNVERIFIED path; write it red, coder confirms the path) |
| `test_xd_labels_translate_ms` | `localizer.reply` (ms) of a gate-on block translates `Product Code` / `Total` like the dash form |

**Gate-off byte identity and properties**
| Test | Assertion |
|---|---|
| `test_gate_off_recorded_envelopes_golden` | on a handful of recorded envelopes (dealer mix with a miss, dealer ETA with a miss, staff compact ETA miss with stock block, staff detailed, quantity question with 3 slots), gate-off output equals golden strings captured from main 20c191d86 at the red commit |
| `test_composer_output_clean` (property, parametrized over the fact matrix plus a product_name fallback and a uuid-shaped token) | no output contains " · ", no UUID (`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`), no count line (`\(\d[\d,]* (products?|items?)\)`, `matches \d+ products`, `Showing \d+ of`, `and \d+ (more|others)`) |

Outside pytest: L1 replay gate-off vs main = 0 unintended (turn tier compares the reply sha256, replay.py:339-347;
baseline turn 0/585, grounding 0/1506); gate-on replay (if the env flag reaches the replay process, section 1.1)
lists the changed replies, every one of them a (a) / (b) / (c) case.

## 7. Existing tests that pin gate-off text (stay green, gate off by default)

`Couldn't find: `: test_stock_miss_render.py, test_avail_mode_scenarios.py, test_chat_language_incoming.py,
test_rearch_r11_catalogue.py, test_lane_require.py, test_multi_ask_runner.py, test_attribute_asks_round6/9.py.
`How many units for each?`: test_stock_ask_ht26_family_pick.py, _r8_open_question.py, _r9_open_question_kinds.py,
_resume_narrows.py, test_avail_mode_scenarios.py. Stock block under an ETA miss: test_crossdomain_ladder.py,
test_rearch_r4_miss_engine.py, test_rearch_s3_compose_data.py, test_rearch_r5_production_decides.py,
test_rearch_r9_handpass9_replay.py, test_chat_language_incoming.py, plus the s6c corpus (`test_s6c_answer_lane.py`).
None is edited in S1; the flip PR retargets them.

## 8. Open items for crew (do not block S0)

- OI-1 (K2): with a miss, `_stock_ask_reply` drops the discontinued marker and `Alternative:` lines. Proposed card v2
  change: G1 prints each answered product as its full line block (line, marker, alternatives, as `_item_line` does
  with numbering off). Not in S1 until crew rules.
- OI-2 (K3): change 4(b) reverses owner ruling "round 6, ruling 2" (point form ending " - " for the dealer to fill,
  T:397-400). Crew to confirm before the flip; G3 is cheap to drop.
- OI-3: G4 is the only S1 hunk inside a function #1744 changes (`output_structurer`). Keep it in S1 or move it to S3.

## 9. Risks (top first)

| # | Risk | Mitigation |
|---|---|---|
| R1 | The L1 replay runs the RECORDED parse, so it cannot see that a changed reply is the next turn's chat history: gate-on (a)/(b) text can shift a follow-up parse, which is exactly what TRAIN follow-ups measure. | Gate-on proof = pytest + one TRAIN live run per PR (card section 6); gate-off proof = replay 0 diffs. |
| R2 | (c) puts numbered lines into a block that three numbered-line readers scan (AB:1592, AB:488/530, pickers.py:71). | G6 structured `rendered_codes`; tests `test_zero_note_*`, `test_member_offer_numbers_after_block`. |
| R3 | `dealer_line` is a backend copy of the MCP's R6 sentences (P:1536-1606): two copies can drift until the flip. | `test_dealer_line_parity_recorded` + matrix test; the flip PR makes the composer the only printer for chatbot replies. |
| R4 | (b) contradicts an older owner ruling (OI-2). | Crew confirmation before the flip. |
| R5 | Gate-on replay depends on env precedence over `replay.env` (UNVERIFIED). | Check `config._resolve_settings_env_file` before relying on it; else gate-on evidence is pytest + TRAIN only. |
