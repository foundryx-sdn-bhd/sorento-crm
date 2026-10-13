# CONTRACT PV2 (13 Oct night): parser v2, one state, resolve then reuse

Base: origin/main 953f3f716. Design: crew `CHATBOT-TARGET-DESIGN.md` + `chatbot-index-architecture.html`.
Five lanes code against THIS file; changing a type here is a contract change (say so on the PR, never drift).
Owner rules that bind every line: parser routes, code picks the tool; no word/fuzzy matching on user text in code
(model maps words to index keys, code exact-matches keys; numbers against data are fine); clarify, never assume; truthful
replies; resolve then reuse; reply lines `*Label:* value`, never ` · `. Paths: `sorento_crm_backend/app/`; new code `services/chatbot/pv2/`.

## 1. Parser v2 OUTPUT (single declaration: `services/chatbot/pv2/schema.py`, owner PV2-CORE)
```python
TurnAct = Literal["answers_bot_question", "refines_held", "new_topic", "switch_back", "small_talk", "unclear"]
Comparator = Literal["eq", "lt", "gt", "between", "about"]
class EntityChange(BaseModel):          # raw words + index key when the INDEX lists one
    kind: str                           # chatbot_entity_kinds.kind (index), e.g. product|customer|brand|category
    phrase: str                         # the customer's own words, verbatim
    key: str | None                     # index key ONLY if printed in the index, else null (never invented)
    op: Literal["add", "replace", "drop"] = "add"   # against HELD (refines_held); ignored on new_topic
class SpecChange(BaseModel):  # attribute_key = registry spec_key from the index, else null; value_to only for between
    phrase: str; attribute_key: str | None; value: str | float | bool | None; value_to: float | None; unit: str | None; comparator: Comparator
class Period(BaseModel):
    preset: Literal["today","yesterday","this_week","last_week","this_month","last_month","this_quarter",
                    "last_quarter","this_year","last_year","ytd","last_n_days","last_n_months"] | None
    n: int | None; date_from: date | None; date_to: date | None      # explicit dates win over preset
class ReportSpec(BaseModel):            # generic; keys come from the REPORTS index section (section 2)
    measure: str | None                 # e.g. "sales.amount", "sales.qty", "stock.low", "orders.outstanding", "chat.asks"
    basis: str | None                   # ordered|delivered|invoiced|outstanding (per measure, from index)
    group_by: list[str]                 # 0..2 dimension keys from the index, outer first (customer, brand, month...)
    filters: list[EntityChange]         # kind = filter dimension key, phrase raw; resolved like entities
    period: Period | None; top_n: int | None; sort: Literal["desc", "asc"] | None
class IdeaFields(BaseModel):            # only when domain == "ideate"; null fields = not said
    problem: str | None; desired_outcome: str | None; use_cases: list[str]; current_workaround: str | None; title: str | None
class Answer(BaseModel):   # answers_bot_question only; option_key = exact key from MEMORY "options:", else free_value words
    option_key: str | None; free_value: str | None
class Clarify(BaseModel):
    needed: bool; phrase: str | None    # phrase = the words that were unclear
    about: Literal["domain","entity","spec_attribute","attachment_type","report_measure","report_period","which_held","idea_problem","other"] | None
class ParserV2Output(BaseModel):        # 12 keys, all required, strict json_schema (no extra keys)
    turn_act: TurnAct; domain: str | None; switch_back_to: str | None   # domain = chatbot_domains.name; T-key from MEMORY
    changes: list[EntityChange]; specs: list[SpecChange]
    attachment_type_key: str | None                # attachment_types.code from the index
    report: ReportSpec | None; idea: IdeaFields | None; answer: Answer | None
    clarify: Clarify; wants_human: bool; language: Literal["en", "zh", "ms", "mixed"]
PV2_JSON_SCHEMA: dict = build_strict_schema(ParserV2Output)   # same json_schema path as head/parser.py:1037
```
`schema.validate(raw) -> ParserV2Output` rejects unknown keys; a key not in the CURRENT index becomes null, phrase kept
(code clarifies), never an error. Today's 59-key schema (`head/parser.py:604-664`) stays untouched.

## 2. INPUT layout (PV2-CORE builds, PV2-CARRY supplies history + MEMORY)
Messages, in this order (cache prefix = system + oldest history first; nothing dynamic before history):
1. `system` = prompt text of key `chatbot_semantic_parser_v2` (label `production`, `ai_prompt_registry.render`
   `:1548`, NO variables, no date) + `"\n\n"` + `render_index(db)`. Budget: head 32k chars, index cap 8k.
2. history = this SESSION's turns, oldest first, raw: `{"role":"user","content":<text>}`,
   `{"role":"assistant","content":<bot reply text>}`; source `chatbot.turns` (`models/chatbot_turn.py:42`, envelope `:84`,
   response `:106`, created_at `:120`). Session = turns since the last gap of >= `PV2_SESSION_GAP_HOURS = 4`.
   Cap `PV2_HISTORY_TURNS = 24` (tunable; eval at 4/12/30). Over the cap drop the OLDEST whole turns only.
3. final `user` = MEMORY block + new message (exact format below).

`services/chatbot/pv2/index.py` (CORE): `render_index(db: Session) -> tuple[str, str]` returns (text, sha1). Sources:
`chatbot_domains` (name, label, switch_words; `supported` only), `chatbot_entity_kinds.kind`, `brands`,
`attachment_types` (code, type_name, `search_synonyms`; only types products carry), `product_categories.class_label` +
`search_synonyms`, `product_spec_registry` (spec_key, unit, short allowed values per class via `active_registry` `:1735`),
REPORTS section from `pv2.report_measures.index_lines()` (owner REPORTS). Format = the HTML page section 3
(`# INDEX`, `## DOMAINS`, `## ENTITY KINDS`, `## BRANDS`, `## ATTACHMENT TYPES (key = name | aka)`, `## PRODUCT CLASSES`,
`## REPORTS`). Cache-stable: fixed section order; rows by (sort_order, key); NO counts, no dates, no ids; cached
in-process and dropped by the existing after-commit dirty hook (`chatbot_prompt_vars.py:473-499`, add the new tables
to `REGISTRY_TABLES`). `prompt_cache_key = f"chatbot_semantic_parser_v2:v{version}:{sha1[:8]}"`.

MEMORY block (`pv2/history.py: render_memory(state, profile, now) -> str`, CARRY). Fixed key order, every key always
printed, `none` when empty, one line per item:
```
<memory>
TODAY: 2026-10-13 Tue (Asia/Kuala_Lumpur)
CONTACT: audience=staff; role=salesperson; company=<group name>; language=zh; brands=SORENTO,MOCHA
HELD: domain=product_attachment
  product: SRTWC286 (family, 10 variants)
  attachment_type: technical_specifications
  spec: trap_type=p_trap; dim_length=600 mm
  report: measure=sales.amount; basis=delivered; group_by=customer; period=last_month; top_n=10
  idea: problem="..."; desired_outcome=none; use_cases=none; current_workaround=none
PENDING QUESTION: kind=clarify_spec_attribute; asked="Which 600 mm do you mean?"
  options: 1=dim_length (Length) | 2=dim_height (Height)
TOPICS (switch_back keys, newest first):
  T1: inventory | product: SRTWC6003
  T2: sales | report: sales.amount by customer, last_month
LAST ANSWER ROWS: 1=SRTWC6003 | 2=SRTWC8066
EARLIER SESSIONS: <1-3 lines from memory.py episodes, or none>
</memory>
<message>{new text verbatim}</message>
```
Profile: `turn/memory.py:457 profile_block` + `turn/profile_facts.py:303`; earlier sessions: episodes (`episode_digest.py:582`).

## 3. ConversationState + apply_turn (`pv2/state.py`, `pv2/apply.py`, owner PV2-CARRY)
```python
class Option(BaseModel): key: str; label: str; payload: dict      # payload = what picking it sets (kind/key/value)
class ResolvedRef(BaseModel): kind: str; key: str; label: str; phrase: str   # key = live code/id
class ResolvedSpec(BaseModel): attribute_key: str; comparator: Comparator; value: str|float|bool; value_to: float|None; unit: str|None
class Held(BaseModel):
    domain: str | None; refs: dict[str, list[ResolvedRef]]; specs: list[ResolvedSpec]
    attachment_type: str | None; report: ReportSpec | None; idea: IdeaFields | None
class PendingQuestion(BaseModel):
    kind: str            # clarify_<about> | pick_row | idea_similar | idea_problem | v1_open_question
    asked: str; options: list[Option]; asked_turn: int; v1_kind: str | None   # mirror of v1 pending (pending.py)
class ConversationState(BaseModel):
    v: Literal[1] = 1; session_started_at: datetime; last_turn_at: datetime; turn_no: int
    held: Held; pending: PendingQuestion | None; topics: list[Held]          # cap 5, newest first
    last_rows: list[Option]                                                  # cap 20, "the 2nd one"
Action = Lookup | Clarify | SmallTalk | HandOver
class Lookup(BaseModel):   type: Literal["lookup"]; domain: str; held: Held        # fully resolved
class Clarify(BaseModel):  type: Literal["clarify"]; about: str; asked_key: str; options: list[Option]; default_key: str | None
class SmallTalk(BaseModel): type: Literal["small_talk"]
class HandOver(BaseModel): type: Literal["hand_over"]; reason: str
def apply_turn(state: ConversationState, out: ParserV2Output, *, resolver: Resolver,
               policy: ClarifyPolicy) -> tuple[ConversationState, Action]: ...   # pure except via `resolver`
```
One function for every domain. Order: `wants_human` -> HandOver. `clarify.needed` or `unclear` -> Clarify (options
from held/data, never from guesses). `small_talk` -> SmallTalk, state unchanged. `answers_bot_question`: needs
`state.pending`; `answer.option_key` must EXACTLY equal an option key, its payload is applied to held and pending clears;
no pending, or no key match -> re-ask the same options (pending kept). `refines_held`: apply `changes` by op per kind,
specs by attribute_key, report fields that are non-null override held.report field by field, domain may change
("delivery for this" keeps refs). `new_topic`: push non-empty held onto `topics`, held = this turn only. A new ref of a
kind already held with no `op` the model was sure of -> Clarify(which_held). `switch_back`: `switch_back_to` must be a
printed T-key, swap held with it. Then every phrase with key null or unresolved goes through the resolver (section 4);
any ambiguity -> Clarify; else Lookup. `ClarifyPolicy` = ask-or-default rows (PR #1570 shape; tonight a constant table
in `pv2/apply.py`, default ask). Session gap: a turn more than 4 h after `last_turn_at` starts a new session: held,
pending, last_rows clear; held moves to `topics[0]`.
Persistence: `respond_contacts.session_vars["pv2"]` (JSONB, `models/access.py:245`), no migration; CARRY adds `"pv2"` to `FIVE_KEYS`
(`session_state.py:23`) since `turn/tail.py:141 write_merged` writes only that key set. v1 State (`turn/state.py:272`) persists as today.

## 4. Resolver contract (`pv2/resolve.py`, owner PV2-RESOLVE; report spec in `pv2/reports.py`, owner PV2-REPORTS)
```python
class Resolution(BaseModel):
    status: Literal["exact", "ambiguous", "none"]; refs: list[ResolvedRef]; options: list[Option]
    phrase: str; unrecognised: bool          # true -> logged to chatbot_unrecognised_phrases
class Resolver(Protocol):
    def resolve_entities(self, changes: list[EntityChange], *, held: Held) -> list[Resolution]: ...
    def resolve_specs(self, specs: list[SpecChange], *, held: Held) -> list[SpecResolution]: ...
    def resolve_attachment_type(self, key: str | None, phrase: str | None) -> Resolution: ...
    def resolve_report_spec(self, spec: ReportSpec, *, held: Held, profile: Profile) -> ReportResolution: ...
```
Exact-match rules (casefold + collapse whitespace; nothing else): an index `key` is accepted only if it is a live key
(else treat as phrase). Phrases match ONLY equality against code, name and staff alias columns: attachment_types
code/type_name/`search_synonyms` (new); product_categories class_label/`search_synonyms` (`models/product.py:57`);
brands code/name; products code or family via existing exact probes (`entity_resolver.py:849 _probe_product`,
`:2058 _product_family_order`); customers customer_code / exact name. FORBIDDEN in pv2: prefix/substring/ILIKE/per-word/
embedding (`entity_resolver.py:2665`, `:1218` ILIKE arms, `resolve_references` fallbacks). No match -> `none`, phrase
logged, Clarify with real candidates of that kind for the held scope (e.g. all attachment types the product carries).
Specs: attribute_key must be in `active_registry` (`product_spec_registry.py:1735`); enum values exact against
allowed values + `merged_synonyms` (`:1623`, staff-editable); numbers via `product_spec_search.py:195 normalise_quantity`
(`:164` unit table) and `default_match_window` (`product_spec_registry.py:407`, mm = 5 exact / 150 decay); search via
`search_specs(..., exact=True)` (`product_spec_search.py:1144`). Numeric with attribute null: candidates = attributes of
the held class (`applicable_keys_for_code` `:1761`) whose REAL data range holds the value; 1 -> policy (default and state
it, or ask); >1 -> Clarify options; 0 -> truthful none + closest rows. Never an invented spec.
`resolve_report_spec`: measure/basis/group_by/sort exact against `pv2/report_measures.py` keys; filters resolved as
entities with kind = dimension; period preset -> dates in Asia/Kuala_Lumpur; missing measure or period -> Clarify
(report_measure / report_period) unless the measure row declares a default; returns `ReportCall(entry, kwargs)`.

## 5. Answer contract (reuse, never new formatters)
`pv2/answer.py: answer(ctx, state, action) -> AnswerResult` (CARRY) dispatches by action/domain:
- Lookup (catalogue, attachment, stock, order, incoming, PO/SPO/cost, promotion, forms, portal): `pv2/bridge.py
  to_v1_verdict(state, action) -> dict` builds the 59-key v1 verdict with RESOLVED codes as entities (same as the user
  typing the codes; parity tests) and `focus_from_v2(held) -> Focus` (`turn/state.py:132`); the engine then runs its
  existing stages: `turn_apply` (`engine.py:5012`) -> `turn_route` (`:5229`/`:5688`) -> `run_fetch` (`:6524`) ->
  `turn_compose.compose` (`:6611`) -> `turn_tail.persist` (`:7687`). Misses use `answer_bridge.answer_for` (`:1771`).
  `answers_bot_question` on a `v1_open_question` passes the pick through as v1 `open_question_answer` and keeps v1 pending.
- Reports (`pv2/reports.py: answer_report`, REPORTS): sales ranking + chat `reports/ask.py:202 run_ask`
  (DIMENSIONS `:27`, FILTERS `:28`, CHAT_MEASURES `:38`); top/bottom selling `sales_report_service.py:599 top_selling`;
  sales report `sales_report_delivered.py:232 delivered_sales_report`; analysis pivot (2 group_by) `api/v1/sales/analysis.py:436`;
  low stock `scm/low_stock_report_service.py:271 build_low_stock_view`; outstanding `outstanding_report_service.py:175`.
  Engine underneath: `reports/engine.py:1017 run`, `:1045 run_summary`, `reports/registry.py:374 ReportDefinition`.
  Reply text = the existing lane formatter for that report (`lanes/business/report_ask.py`, `chat_report.py`,
  `low_stock_ask.py`); grants/company scope exactly as those lanes pass them. PR #1572 `Measure` (`reports/measures.py:16`
  on `crew/reports-step2`) is the model for `report_measures.py`; do not merge #1572 tonight.
- Ideation (`pv2/ideas.py`, IDEAS): `services/ideation_capture_service.py:207 handle_capture_turn` gains an additive kwarg
  `fields_in: IdeaFields | None`; when given, `ideation_extractor.py:302 extract_ideate_turn` is skipped. Similar ideas
  (`:339-382`) become `PendingQuestion(kind="idea_similar", options=[1..N, "new"])`; picks arrive as `answer.option_key`
  (no literal "new"/digit parsing on text in pv2). Missing `problem` -> Clarify(idea_problem) once, then the existing
  `ask_idea_gave_up` end. Field names follow PR #1625 (`desired_outcome`, `use_cases`, `current_workaround`).
- SmallTalk -> `lanes/casual.py`; HandOver -> `run_escalation_lane` (`engine.py:8765`); Clarify -> `pv2/clarify_copy.py`
  (CARRY) rendering numbered options via `label_catalog` labels, one `*Label:* value` per line, records PendingQuestion.

## 6. Flag, label, shadow (PV2-CORE)
- Prompt key `chatbot_semantic_parser_v2` registered in `ai_prompt_registry.py` `PROMPT_KEYS` (`:1059`), `variables=[]`,
  fallback text from `pv2/prompt_v2.txt`. Versions/labels via existing `ai_prompt_versions`/`ai_prompt_labels`
  (`models/ai_prompt.py:28,58`); v1 key untouched; prod never gets a v2 `production` label until the owner says so.
- Mode = `respond_contacts.chatbot_parser_mode` (TEXT NULL, per contact, like `requires_registered_project`) else
  `system_settings.chatbot_parser_mode` (TEXT NOT NULL DEFAULT 'v1'; switches read like `console_service.py:226`).
  Values `v1 | shadow | v2`. `pv2/flag.py: parser_mode(db, contact) -> Literal["v1","shadow","v2"]`.
- Hook (only CORE edits `chatbot/engine.py`): at `:4482-4560` (resolve_config/parse) branch on mode; `v2` runs
  `pv2.turn.run_turn_v2(ctx) -> TurnV2Result(verdict, focus, direct_reply|None, state)` (CARRY) and continues the
  normal stages with that verdict/focus, or returns `direct_reply` (clarify/small talk/report/idea) through the same tail.
  `shadow`: v1 answers and sends; after `turn_tail.persist` (`:7687`) run parse+apply+resolve only, no send, no state
  write; store `{parser_out, action, ms, tokens}` as trace fact `pv2_shadow` on the turn; usage feature
  `chatbot_parser_v2_shadow`. Dev copies: `UPDATE system_settings SET chatbot_parser_mode='v2'`. Rollback = set 'v1'.
- Record mode: env `PV2_RECORD_DIR` makes `pv2/call.py` append `{memory, text, parser_out}` JSONL (replay corpus).
- Migration `alembic/versions/pv2_0001_schema.py` (CORE, down_revision = current head `spec_in_0001_stage_label`,
  re-check `alembic heads` first), additive only: the two mode columns; `attachment_types.search_synonyms JSONB NOT NULL
  DEFAULT '[]'`; table `chatbot_unrecognised_phrases(id, phrase, kind, domain, contact_id, turn_id, seen_count,
  first_seen_at, last_seen_at, unique(phrase, kind))`. `pv2_0002_alias_seed.py` (RESOLVE, down_revision `pv2_0001_schema`):
  fills `attachment_types.code` WHERE code IS NULL for the 5 carried types (certification, product_photos,
  technical_specifications, product_videos, installation_guide) and seeds aliases (TD, CAD, drawing, 图纸, lukisan ->
  technical_specifications; photo, picture, gambar, 图片, 照片 -> product_photos; cert, 证书, sijil -> certification;
  manual, 安装 -> installation_guide; video, 视频 -> product_videos). Seeds only add; never overwrite staff data.

## 7. Lanes tonight (file ownership: no file in two lanes; tests under `tests/chatbot/pv2/`, prefix = lane)
- PV2-CORE (L): prompt v2 text, index renderer, parser call, schema, flag, shadow, migration, engine hook. Owns
  `pv2/__init__.py, schema.py, index.py, prompt_v2.txt, call.py, flag.py, shadow.py`, `pv2_0001_schema.py`, `tests/chatbot/pv2/replay.py` +
  `conftest.py`; edits `engine.py` (hook), `ai_prompt_registry.py`, `chatbot_prompt_vars.py`, `models/{user,access,resources,chatbot_policy}.py`.
- PV2-CARRY (M): ConversationState, apply_turn, session history + MEMORY, turn orchestration, v1 bridge, clarify replies.
  Owns `pv2/state.py, apply.py, history.py, turn.py, answer.py, bridge.py, clarify_copy.py`; edits `chatbot/session_state.py`.
- PV2-RESOLVE (M): exact resolvers (entities, specs, attachment types), alias seeds, unrecognised-phrase log.
  Owns `pv2/resolve.py, aliases.py, alias_seed.py`, `pv2_0002_alias_seed.py`.
- PV2-REPORTS (M): report spec -> measure table -> existing report entry points + formatters, generic over
  measure/group_by/filters/period. Owns `pv2/reports.py, report_measures.py`.
- PV2-IDEAS (M): ideation through the same turn_act/state (parser fields, similar-idea picks as options, problem
  follow-up). Owns `pv2/ideas.py`; edits `ideation_capture_service.py`, `ideation_capture_replies.py`, `chatbot/lanes/ideate.py`.
Interfaces between lanes are ONLY the types above; each lane fakes the others with fixtures until integration.

Integration order (branch `crew/pv2-integration` off main): 1) CORE commit 1 within the first hour = `schema.py` +
models + `pv2_0001_schema.py` + `replay.py` (all lanes rebase on it); 2) RESOLVE; 3) REPORTS + IDEAS (independent);
4) CARRY (wires `answer.py` to the real handlers); 5) CORE commit 2 (engine hook + flag) last. Each merge: lane tests +
`tests/chatbot/pv2/` replay suite green, touched v1 tests only (`pytest -n 2`), no full suite locally.

Fixture format (no LLM; `tests/chatbot/pv2/fixtures/<lane>_<topic>.jsonl`, one object per TURN, cases in order):
```json
{"case": "td-draw-1", "turn": 1, "now": "2026-10-13T23:30:00+08:00", "state_in": null, "text": "td draw for SRTWC286",
 "parser_out": {"turn_act": "new_topic", "domain": "product_attachment", "...": "full ParserV2Output"}, "source": "handwritten | recorded:<run>",
 "expect": {"action": "lookup|clarify|small_talk|hand_over", "domain": "product_attachment",
            "held": {"refs": {"product": ["SRTWC286"]}, "attachment_type": "technical_specifications"},
            "options": ["1", "2"], "reply_must": ["*Product:*"], "reply_must_not": [" · "]}}
```
`replay.py` feeds `parser_out` instead of calling the model, chains `state_out` into the next turn of the same case,
and asserts `expect`. DB-backed asserts use the existing test DB fixtures; hidden eval cases never go into fixtures.

## 8. Morning eval (spend needs the owner's OK; est. from baseline run cost $2.01 per hidden 214)
1. Baseline: prod v52 text loaded into the eval DB, labelled via `tooling/parser_label.sh`, hidden 214 (`eval-gate.sh`). ~$2.0.
2. v2: same 214 with `chatbot_parser_mode='v2'` on the eval DB, `PV2_RECORD_DIR` on (builds the replay corpus). ~$1.2 est.
3. Multi-turn: the 57 hidden multi-turn cases (`--ids-file`), v2 at `PV2_HISTORY_TURNS` = 4 / 12 / 30. ~$1 total.
Gate for hand test: v2 >= v52 overall, multi-turn better, no topic worse by more than 1 case, p95 <= 8 s.
Owner hand-test script outline (`laneboard/scripts/<PR>.md`; every reply one fact per line, nothing invented):
- A attachments+specs: "td draw for SRTWC286" -> "the P one" -> "stock?" -> "马桶 600mm 图纸" (clarify Length/Height)
  -> "length" (truthful "no drawing on file" for SRTWC8066) -> "back to SRTWC286 stock" (switch_back).
- B management report: "top 10 customers by sales last month" -> "by qty" -> "dealer only" -> "SORENTO brand" ->
  "by salesman instead" -> "by month and brand" -> "low stock basin" (new topic) -> "back to the sales one, top 5".
- C ideas: "idea: customers keep asking TD drawings after office hours" -> similar list -> "2" / "new" ->
  "it's for the showroom team too" (refines idea) -> idea with no problem stated (one follow-up only).
- D cross-domain: mid idea follow-up ask "eh stock SRTWC6003?" -> answer -> "ok log my idea as new".
- E mixed language + small talk: "boleh hantar gambar SRTWC8066", "tq bos", "ok la", "那个第二个呢".
- F unclear/hand-over: "the cheaper one" with nothing held (clarify), "can I talk to someone" (hand over).
Owner calls (rec. first): inexact customer name -> ask for the code (rec.) vs containing-name candidates; "gambar" ->
Product Photos (rec.); idea optional fields listed, not asked (rec., as main).
