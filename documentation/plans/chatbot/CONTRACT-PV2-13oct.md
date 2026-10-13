# CONTRACT PV2 (13 Oct night, rev 2 after direction review): parser v2, one state, resolve then reuse

Base: origin/main 953f3f716. Design: crew `CHATBOT-TARGET-DESIGN.md` (+ memory addendum); direction review (RIGHT WITH
CHANGES, all 13 applied) + owner answers 13 Oct 23:50. Lanes code against THIS file; a type change here is a contract change.
Owner rules that bind every line: parser routes, code picks the tool; no word/fuzzy matching on user text in code (the
model returns INDEX keys, code exact-matches keys; numbers against data are fine); clarify, never assume; truthful
replies; resolve then reuse; reply lines `*Label:* value`, never ` · `. NEVER leak internal keys/codes/UUIDs/ids in
replies or clarify options (owner 13 Oct 23:50): options show display names/labels; index keys, registry keys, customer
codes and ids stay internal (`Option.key`/`payload`); product codes only where today's formatters already print them.
Paths: `sorento_crm_backend/app/`. ALL new code in `services/chatbot/pv2/`. Flag OFF by default (section 6). No old path
is deleted tonight; today's 59-key schema (`head/parser.py:604-664`) and v1 prompt key stay untouched.

## 1. Parser v2 OUTPUT (single declaration: `pv2/schema.py`, owner PV2-CORE)
One turn-act enum everywhere (asks, fixtures, traces, eval): no `entity_op`, `domain_in_message`, `continuation`, `message_type`.
```python
TurnAct = Literal["answers_bot_question", "refines_held", "new_topic", "switch_back", "small_talk", "unclear"]
Op = Literal["set", "add", "remove", "clear"]       # per changed field, against HELD
Comparator = Literal["eq", "lt", "gt", "between", "about"]
class Entity(BaseModel):
    kind: str                 # ENTITY KINDS key from the index (product, customer, brand, product_type, warehouse...)
    key: str | None           # canonical INDEX key/value for index-listed kinds (brand, product_type...), else null
    phrase: str | None        # raw words: lookup input ONLY for non-index kinds (codes, customer names); else log only
    op: Op
    refers_to_held: bool      # "this", "that one", "the P one": points at something already held
class Spec(BaseModel):
    attribute: str | None     # index spec attribute key; null = model could not place it (code clarifies)
    value_key: str | None     # index enum value key (enum attributes only)
    number: float | None; number_to: float | None; unit: str | None   # numbers stay numbers; matched against data in code
    comparator: Comparator; op: Op; phrase: str | None                # phrase only for the unknown-words log
class Period(BaseModel):
    preset: Literal["today","yesterday","this_week","last_week","this_month","last_month","this_quarter",
                    "last_quarter","this_year","last_year","ytd","last_n_days","last_n_months"] | None
    n: int | None; date_from: date | None; date_to: date | None      # explicit dates win over preset
class Answer(BaseModel):      # answers_bot_question: option_key = exact key printed under PENDING "options:"
    option_key: str | None; free_value: str | None
class Ask(BaseModel):         # 11 always-present keys + 3 nullable groups
    act: TurnAct; domain: str | None; switch_back_to: str | None     # domain = index DOMAINS key; T-key from MEMORY
    entities: list[Entity]; specs: list[Spec]
    attachment_type: str | None                                      # index ATTACHMENT TYPES key (non-null = set)
    period: Period | None; row_pick: int | None                      # row_pick = position in LAST ANSWER ROWS
    answer: Answer | None; cleared: list[str]                        # scalar fields to clear: attachment_type|period|report|specs
    report: ReportSpec | None; low_stock: LowStock | None; ideation: IdeationSignal | None   # optional groups, null unless used
class LowStock(BaseModel): group_by: Literal["category","brand","supplier"] | None; all_categories: bool
class IdeationSignal(BaseModel): idea_count: int; adds_to_held_idea: bool    # fields are ideation's OWN extractor (s.5)
class Escalation(BaseModel): requested: bool; team_key: str | None          # team_key from index, else null
class Clarify(BaseModel):
    needed: bool; phrase: str | None
    about: Literal["domain","entity","spec_attribute","attachment_type","report_measure","report_period","which_held","other"] | None
class ParserV2Output(BaseModel):  # strict json_schema, no extra keys; ~15 always-present keys counting Ask's
    asks: list[Ask]               # >=1; one message may carry several asks/domains, answered in order
    clarify: Clarify; language: Literal["en","zh","ms","mixed"]; escalation: Escalation | None
PV2_JSON_SCHEMA: dict = build_strict_schema(ParserV2Output)   # same json_schema path as head/parser.py:1037
```
Output trim built in: nullable groups emit `null`, no per-entity 10-key blocks (target <= 250 output tokens). Unknown keys
are rejected; a key not in the PINNED index (s.2) becomes null, phrase logged. NO domain cards, re-calls or embeddings.

## 2. INDEX + INPUT layout (PV2-CORE builds; PV2-CARRY supplies history + MEMORY)
Messages, in order (cache prefix = the whole system message; nothing dynamic in it):
1. `system` = published text of prompt key `chatbot_semantic_parser_v2` = head (<= 32k chars, NO variables, no date) +
   `"\n\n"` + INDEX (~1.9k tokens, cap 8k chars). The full index stays in the cached prefix.
2. history = the last 3 EXCHANGES of this session only (user text + bot reply text, oldest first, from `chatbot.turns`,
   `models/chatbot_turn.py:42`, envelope `:84`, response `:106`, created_at `:120`). `PV2_HISTORY_EXCHANGES = 3`.
   No 20-30 raw turns tonight (gpt-5.4-mini cache TTL ~5-10 min idle makes long history uncached). 4/12/30 measured later.
3. final `user` = MEMORY block (structured state) + new message.

INDEX (`pv2/index.py: render_index(db) -> tuple[str, str]` = (text, sha1), CORE). Curated, fixed section order, rows
by (sort_order, key); NO counts, no dates, no ids. Sections: `## DOMAINS` (`chatbot_domains`, `supported` only),
`## ENTITY KINDS`, `## BRANDS`, `## ATTACHMENT TYPES (key = name | aka)`, `## PRODUCT CLASSES` (each product type listed
ONLY under its primary class), `## SPEC ATTRIBUTES` (key, label, unit, enum value keys per class via `active_registry`
`product_spec_registry.py:1735`), `## REPORTS` (from `pv2/report_measures.py: index_lines()`, REPORTS),
`## TEAMS` (escalation). An alias is printed only under its winning key (precedence, section 4).
Rebuild ON PUBLISH, not per edit (as `chatbot_parser_prompt.py:1166-1172`): `publish_v2(db, head_text) -> version_id`
writes an immutable `ai_prompt_versions` row = head + index; the keys `schema.validate` accepts are that PINNED index's.
`index_sha` + version id go into every trace (`pv2_turn` fact), every eval snapshot and `prompt_cache_key =
f"chatbot_semantic_parser_v2:v{version}:{index_sha[:8]}"`.

MEMORY (`pv2/history.py: render_memory(state, profile, now)`, CARRY): fixed key order, every key printed, `none` if empty:
```
<memory>
TODAY: 2026-10-13 Tue (Asia/Kuala_Lumpur)
CONTACT: audience=staff; role=salesperson; company=<group name>; language=zh; brands=SORENTO,MOCHA
HELD: domain=product_attachment
  product: SRTWC286 (family, 10 variants)
  attachment_type: technical_specifications
  spec: trap_type=p_trap; dim_length=600 mm
  report: dataset=sales; measures=amount; basis=delivered; group_by=customer; period=last_month; top_n=10
  idea: held draft "<title>" (missing: desired_outcome)
PENDING QUESTION: kind=clarify_spec_attribute; asked="Which 600 mm do you mean?"
  options: 1=dim_length (Length) | 2=dim_height (Height)
TOPICS (switch_back keys, newest first):
  T1: inventory | product: SRTWC6003
  T2: sales | report: amount by customer, last_month
LAST ANSWER ROWS: 1=SRTWC6003 | 2=SRTWC8066
EARLIER SESSIONS: <1-3 lines from memory.py episodes, or none>
</memory>
<message>{new text verbatim}</message>
```
Profile: `turn/memory.py:457 profile_block` + `turn/profile_facts.py:303`; earlier sessions: `episode_digest.py:582`.

## 3. ConversationState + apply_turn (`pv2/state.py`, `pv2/apply.py`, owner PV2-CARRY)
```python
class Option(BaseModel): key: str; label: str; payload: dict      # payload = what picking it sets (kind/key/value)
class ResolvedRef(BaseModel): kind: str; key: str; label: str; phrase: str | None   # key = live code/id
class ResolvedSpec(BaseModel): attribute: str; comparator: Comparator; value: str|float|bool; value_to: float|None; unit: str|None
class Held(BaseModel):
    domain: str | None; refs: dict[str, list[ResolvedRef]]; specs: list[ResolvedSpec]
    attachment_type: str | None; period: Period | None; report: ReportSpec | None; idea_draft_id: str | None
class PendingQuestion(BaseModel):
    kind: str     # clarify_<about> | pick_row | idea_similar | idea_missing_field | idea_multi | offer_handover | v1_open_question
    asked: str; options: list[Option]; asked_turn: int; misses: int; v1_kind: str | None
class ConversationState(BaseModel):
    v: Literal[1] = 1; session_started_at: datetime; last_turn_at: datetime; turn_no: int
    held: Held; pending: PendingQuestion | None; topics: list[Held]          # cap 5, newest first
    last_rows: list[Option]                                                  # cap 20
Action = Lookup | ClarifyAct | SmallTalk | HandOver | OfferHandover
def apply_turn(state, out: ParserV2Output, *, resolver: Resolver, policy: ClarifyPolicy
               ) -> tuple[ConversationState, list[Action]]: ...   # one Action per ask; pure except via `resolver`
```
One function for every domain. Per ask, in order: `escalation.requested` -> HandOver. `clarify.needed` or `unclear` ->
ClarifyAct (options from held/data, never guesses). `small_talk` -> state unchanged. `answers_bot_question`: needs
`state.pending`; `answer.option_key` must EXACTLY equal an option key; its payload applies to held, pending clears.
`refines_held`: entities/specs by `op` per kind (set = replace kind, add, remove, clear = drop kind); non-null scalars
set; `cleared` clears; report fields edit the held spec field by field (section 5); `row_pick` -> `last_rows[n-1]`;
domain may change ("delivery for this" keeps refs). `new_topic`: push non-empty held onto `topics`, held = this ask.
`switch_back`: `switch_back_to` must be a printed T-key; swap. Several asks: answered in order, the LAST ask's held is
held next turn, earlier ones go on `topics`. Then key-null/unresolved items go to the resolver (section 4); ambiguity
-> ClarifyAct; else Lookup. `ClarifyPolicy` = ask-or-default rows (PR #1570 shape; tonight a constant table, default ask).
Session gap > 4 h after `last_turn_at` = new session: held -> `topics[0]`; pending, last_rows clear.
Short-reply safety (code consistency checks, no text inspection), each = one MISS that re-asks with real options:
answers_bot_question with no pending or no exact option key; refines_held / `refers_to_held` with nothing of that kind
held; switch_back to an unprinted T-key; `row_pick` beyond `last_rows`; a new entity of a held kind with no
`refers_to_held` and op `add` on a single-subject domain (11 Oct rule: carried + new subject -> which_held). After 2
misses on the same pending -> OfferHandover (a normal bot question, options yes/no; yes -> HandOver).
Persistence: `respond_contacts.session_vars["pv2"]` (`models/access.py:245`); CARRY adds `"pv2"` to `FIVE_KEYS`
(`session_state.py:23`; `turn/tail.py:141 write_merged` writes only those). v1 State persists as today.

## 4. Resolver + aliases (`pv2/resolve.py`, `pv2/aliases.py`, owner PV2-RESOLVE)
```python
class Resolution(BaseModel):
    status: Literal["exact", "ambiguous", "none"]; refs: list[ResolvedRef]; options: list[Option]
    phrase: str | None; unrecognised: bool          # true -> chatbot_unrecognised_phrases (the unknown-words log)
class Resolver(Protocol):
    def resolve_entities(self, ents: list[Entity], *, held: Held) -> list[Resolution]: ...
    def resolve_specs(self, specs: list[Spec], *, held: Held) -> list[SpecResolution]: ...
    def resolve_attachment_type(self, key: str | None, *, held: Held) -> Resolution: ...
    def resolve_report_spec(self, spec: ReportSpec, *, held: Held, profile: Profile) -> ReportResolution: ...  # REPORTS
```
Index kinds (brand, product_type, attachment type, spec attribute, enum value, domain, report keys): the model's `key`
is accepted only if it is in the pinned index AND live; never resolved from `phrase`. Key null -> phrase logged, Clarify
with real candidates for the held scope (e.g. the attachment types this product carries). Non-index kinds: phrase
exact-match only (casefold + collapse whitespace) against code/name columns: products via `entity_resolver.py:849
_probe_product`, `:2058 _product_family_order`; customers customer_code / exact name. FORBIDDEN in pv2: prefix/
substring/ILIKE/per-word/embedding (`entity_resolver.py:2665`, `:1218` ILIKE arms, `resolve_references` fallbacks).
Customer name not exact (OWNER 13 Oct 23:50): show REAL customer-name candidates (names containing the phrase, the
contact's grants/company scope, cap 8) as a normal PendingQuestion with options, labels = display names; never
auto-join, never ask the user for a code; 0 candidates -> truthful "no customer named ...". This candidate list is the
ONE owner-approved containing-name search; it only builds options, never resolves on its own.
Numbers: `product_spec_search.py:195 normalise_quantity` (`:164` units), `default_match_window` (`product_spec_registry.py
:407`), `search_specs(exact=True)` (`:1144`). Attribute null: held-class attributes (`:1761`) whose REAL data range holds
the number; 1 -> policy; >1 -> Clarify; 0 -> truthful none + closest rows. Never an invented spec.
Aliases (shown as "aka" in the index; they help the MODEL pick keys, code never matches text with them): sources
`attachment_types.search_synonyms` (new), `product_categories.search_synonyms` (`models/product.py:57`), registry
`merged_synonyms` (`:1623`). Precedence is data: table `chatbot_alias_precedence(kind, alias_norm, winner_key)`. Conflict
check `aliases.check_conflicts(db, kind, key, aliases)` on staff save (RESOLVE wires it into the category, attachment
type and spec registry save paths, listing the exact files in its first commit; no other lane edits them): a save
creating the same alias under two keys with no precedence row is refused with the clash named. `publish_v2` refuses
while any clash is open. Seeds (`pv2_0002_alias_seed.py`): fill `attachment_types.code` where null for certification,
product_photos, technical_specifications, product_videos, installation_guide; aliases TD, CAD, drawing, 图纸, lukisan ->
technical_specifications; photo, picture, gambar (owner call decided by crew), 图片, 照片 -> product_photos; cert, 证书,
sijil -> certification; manual, 安装 -> installation_guide; video, 视频 -> product_videos. Seeds only add.

## 5. Answer contract (reuse, never new formatters; `pv2/answer.py`, CARRY)
- Lookup: `pv2/bridge.py to_v1_verdict(state, action) -> dict` builds the 59-key v1 verdict with RESOLVED codes, exactly
  as if the user had typed the codes, and `focus_from_v2(held) -> Focus` (`turn/state.py:132`); the engine runs its
  existing stages (`turn_apply` `engine.py:5012` -> `turn_route` `:5229`/`:5688` -> `run_fetch` `:6524` ->
  `turn_compose.compose` `:6611` -> `turn_tail.persist` `:7687`). Replies match today's by construction (parity tests).
  Tonight's gated carry: stock, product attachments, sales ranking. Other domains pass through the same generic bridge,
  marked `pv2_ungated_domain` in the trace, no gate claim. `v1_open_question` picks pass through as `open_question_answer`.
- Reports (`pv2/reports.py: answer_report`, REPORTS). The report ENGINE is already generalised (`reports/registry.py:109-111`
  ratio measures `ratio_of`, `:307 PivotLayout` row x col x measures). PV2-REPORTS unifies only the PARSER side: ONE
  report spec feeding that engine and its existing entry points; NO new report code paths, no new formatters. The spec
  replaces today's four overlapping parser shapes (`head/parser.py:225-301`, `:405-432`, `:497`, `:517`):
```python
class Filter(BaseModel): dimension: str; op: Literal["in","not_in","gt","lt","between"]; keys: list[str]; values: list[float]
class ReportSpec(BaseModel):
    dataset: str | None; measures: list[str]          # measure keys; a ratio is "a/b" of two measure keys
    group_by: list[str]; top_n_per_group: int | None  # <= 2 dimension keys, outer first
    filters: list[Filter]; period: Period | None
    compare_to: Literal["prev_period","last_year","custom"] | None; compare_period: Period | None
    grain: Literal["day","week","month","quarter","year"] | None; threshold: float | None
    sort: Literal["desc","asc"] | None; top_n: int | None; basis: str | None
```
  Mapped (data only) through a dimension + measure table (`pv2/report_measures.py`; PR #1572 `Measure`
  `reports/measures.py:16` as the model, #1572 not merged) onto `reports/engine.py:1017 run`. Follow-ups EDIT the held
  spec: "only Johor" adds a filter, "by salesman instead" swaps group_by, "the 2nd one" (row_pick) drills into that row. TONIGHT map only what
  `reports/ask.py:202 run_ask` supports today: one measure, one group_by (DIMENSIONS `:27`), `in` filters (FILTERS `:28`),
  one period, sort, top_n, basis; chat dataset via CHAT_MEASURES `:38`. Anything else (compare_to, ratio, 2nd group, grain,
  threshold, not_in/gt/lt/between, region: `:27` has no state/region dimension) -> truthful "not available yet" line
  naming the part, never a silent drop. Every report reply starts with a header line showing how the question was read
  (`*Read as:* sales amount, delivered, by customer, last month, top 10`). Missing fields come from a policy table
  (`pv2/report_policy.py`), not up to 4 sequential questions (`lanes/business/report_ask.py:8`). CONFLICT, PENDING OWNER:
  `report_ask.py:8-9` records owner ruling 4 Oct "a ranking never defaults the metric or the basis: it asks". Tonight
  the table mirrors it (metric, basis = ask, asked together in ONE question); proposed defaults (period = this_month,
  top_n = 10) are marked pending owner. Reply text = the existing lane formatter (`report_ask.py`, `chat_report.py`,
  `low_stock_ask.py`); grants/company scope exactly as those lanes pass them.
- Ideation (`pv2/ideas.py`, IDEAS-dialogue): shares ONLY dialogue mechanics. Extraction + save stay ideation's own
  (#1625 path: `ideation_capture_service.py:207 handle_capture_turn`, `ideation_extractor.py:302`). (a) The similar-ideas
  list (`:339-382`) becomes PendingQuestion(kind="idea_similar", options 1..N + new); "yang kedua"/"第二个"/"the second"
  arrive as `answers_bot_question` + option_key and are passed in via a new additive kwarg `choice_in` (skips the
  digit/"new" text check at `:273-276` only when given). (b) A reply to the idea's missing-field question
  (PendingQuestion kind="idea_missing_field") updates the HELD draft (`update_held=True` kwarg), never a second idea.
  (c) `ideation.idea_count >= 2` -> PendingQuestion(kind="idea_multi", "N ideas: save both?"). Missing optional fields
  are listed in the reply, not asked (owner call decided by crew). Actions (save, link) keep their explicit state flows;
  only reads use the generic carry. Without the new kwargs, v1 behaviour is byte-identical.
- SmallTalk -> `lanes/casual.py`; HandOver -> `run_escalation_lane` (`engine.py:8765`); ClarifyAct / OfferHandover ->
  `pv2/clarify_copy.py` (numbered options, `label_catalog` labels / display names, never keys or ids; one `*Label:* value`
  per line; records the PendingQuestion).

## 6. Flag, prompt key, migration (PV2-CORE)
- Prompt key `chatbot_semantic_parser_v2` in `ai_prompt_registry.py` `PROMPT_KEYS` (`:1059`), `variables=[]`, fallback head
  `pv2/prompt_v2.txt`; versions/labels via `ai_prompt_versions`/`ai_prompt_labels` (`models/ai_prompt.py:28,58`); no prod label.
- `pv2/flag.py: parser_mode(db, contact, envelope) -> Literal["v1","v2"]`: `v2` when the turn is a console turn
  (`engine.py:1335 _chat_console`), or `respond_contacts.chatbot_parser_mode = 'v2'` (test contacts), or
  `system_settings.chatbot_parser_mode = 'v2'` (dev copies only); else `v1`. Default 'v1'. Rollback = set 'v1'.
- The ONE engine hook (only CORE edits `chatbot/engine.py`): at `:4482-4560` (resolve_config/parse) branch on mode;
  `v2` runs `pv2.turn.run_turn_v2(ctx) -> TurnV2Result(verdict, focus, direct_reply | None, state)` (CARRY) and continues
  the normal stages, or returns `direct_reply` (clarify/small talk/report/idea) through the same tail.
- Record mode: env `PV2_RECORD_DIR` makes `pv2/call.py` append `{index_sha, version, memory, text, parser_out}` JSONL.
- Migration `alembic/versions/pv2_0001_schema.py` (down_revision = head `spec_in_0001_stage_label`; re-check `alembic
  heads` first), additive only: `respond_contacts.chatbot_parser_mode TEXT NULL`; `system_settings.chatbot_parser_mode
  TEXT NOT NULL DEFAULT 'v1'`; `attachment_types.search_synonyms JSONB NOT NULL DEFAULT '[]'`; `chatbot_alias_precedence`;
  `chatbot_unrecognised_phrases(id, phrase, kind, domain, contact_id, turn_id, seen_count, first_seen_at, last_seen_at,
  unique(phrase, kind))`. `pv2_0002_alias_seed.py` (RESOLVE, down_revision `pv2_0001_schema`).

## 7. Tests (no LLM unless budgeted in section 8)
- BEFORE `apply_turn` is written (red first): CARRY converts the recorded replay turns (`tests/chatbot/replay_turns/**`;
  review: 211; this base: 208 case files / 878 turns) + the 46 `tests/chatbot/console_cases/*.yaml` into table tests
  `tests/chatbot/pv2/tables/*.jsonl` (test-only `v1_to_v2.py`). Dev-split recordings (CORE, early) replace converted rows.
- Owner rulings: ~410 references in chatbot code (review count; a quick grep gives 345 here). CARRY lists every one in
  `documentation/plans/chatbot/PV2-owner-rulings.md` (file:line, ruling, how PV2 preserves it or "v1 only"); none dropped.
- Fixtures `tests/chatbot/pv2/fixtures/<lane>_<topic>.jsonl`, one object per TURN, cases in order: `case, turn, now,
  state_in, text, index_sha, parser_out` (full ParserV2Output), `source` (handwritten | converted:<file> | recorded:<run>),
  `expect` {`actions`, `held`, `options` (labels), `reply_must`, `reply_must_not` (always `" · "` + any internal key/id)}.
  `replay.py` (CORE) feeds `parser_out` (no model), chains `state_out` into the next turn, asserts `expect`. Hidden eval
  cases never go into fixtures or prompt examples.

## 8. Eval tonight (owner 13 Oct 23:50: no budget cap; measured plan kept; hidden set at most twice)
- Baseline on the actual prod v52 TEXT: the eval DB `production` label points at v49, so pin by text/version id, never
  by label. Hidden run #1 = v52 baseline (~$2.5). Hidden run #2 = final v2 candidate (~$2.5). No other hidden runs.
- Dev split: recordings (`PV2_RECORD_DIR`, ~$1); gate runs 4 planned, more where they buy confidence; live checks as
  needed; spend measured per run. A/B (owner confirmed): paired flips v52 vs v2, 2 repeats each side, a flip counts only
  when both agree; shadow later.
- Add to the DEV split (never hidden): zh/ms/mixed report follow-ups ("only Johor", "ikut salesman", "按品牌") and an
  ideation set (similar pick in words, missing-field reply, two ideas in one message).
- No prompt examples from hidden cases; no eval expectation changes.
Morning pass bar (all required before any URL): >= 3 NET hidden multi-turn cases fixed vs v52 (paired flips), no
wrong-data or permission regressions, prompt <= 12k tokens, p95 <= 8 s. Then console hand test in en/zh/ms/mixed:
narrow ("the P one", "only SORENTO"), answer the bot's question in words ("yang kedua", "第二个"), switch topic and back.
Scope tonight: new prompt (<= 32k chars) + index + schema behind the flag; one carry for stock, product attachments,
sales ranking (reports); replies from today's formatters; ideation dialogue mechanics.
NOT tonight: merges, label moves, prod, shadow, canary; deleting old code or dropping PRs; domain cards, embeddings,
re-calls, Gemini, tool loop; comparisons/growth/ratios/region; formatter consolidation; spec-registry migration;
20-30-turn history; prompt examples from hidden cases; changing eval expectations.
Owner calls: decided by crew: "gambar" -> Product Photos; missing optional idea fields listed, not asked. OWNER
13 Oct 23:50: inexact customer name -> real-name candidates (section 4); no internal codes in replies. PENDING OWNER:
report defaults vs the 4 Oct ask-ruling (rec. keep ask tonight).

## 9. Lanes (no file in two lanes; tests `tests/chatbot/pv2/<lane>_*`; lanes meet ONLY via the types above, fixtures until merge)
- PV2-CORE (L): schema, index + publish, prompt head, parser call + record, flag, migration, engine hook, replay, evals. Owns
  `pv2/__init__.py, schema.py, index.py, prompt_v2.txt, call.py, flag.py`, `pv2_0001_schema.py`, `tests/chatbot/pv2/replay.py,
  conftest.py`; edits `chatbot/engine.py` (hook only), `ai_prompt_registry.py`, `models/{user,access,resources,chatbot_policy}.py`.
- PV2-CARRY (M): state, apply_turn + safety checks, history/MEMORY, turn orchestration, v1 bridge, clarify copy, table
  tests, owner-rulings list. Owns `pv2/state.py, apply.py, history.py, turn.py, answer.py, bridge.py, clarify_copy.py`,
  `tests/chatbot/pv2/tables/`, `v1_to_v2.py`, `PV2-owner-rulings.md`; edits `chatbot/session_state.py` (FIVE_KEYS only).
- PV2-RESOLVE (M): exact resolvers, customer-name candidates, aliases + precedence + save check, seeds, unknown-words log.
  Owns `pv2/resolve.py, aliases.py, alias_seed.py`, `pv2_0002_alias_seed.py`; edits the 3 alias save paths (named in commit 1).
- PV2-REPORTS (M): parser-side unification only: ReportSpec -> measure table -> existing engine/run_ask; held-spec edits,
  header line, policy table, "not available yet". Owns `pv2/reports.py, report_measures.py, report_policy.py`.
- PV2-IDEAS-dialogue (S/M): similar list as bot question, missing-field reply updates held draft, multi-idea question.
  Owns `pv2/ideas.py`; edits `ideation_capture_service.py`, `ideation_capture_replies.py` (additive kwargs only).

## 10. Integration by hour (MYT; `crew/pv2-integration`, never main; per merge: lane + pv2 + touched v1 tests, no full suite)
| Time | Step |
|---|---|
| 23:45-00:45 | CORE commit 1: `schema.py`, models, `pv2_0001_schema.py`, `flag.py` stub, `replay.py`; all lanes rebase on it at 00:45. Crew: hidden run #1 (v52 text, pinned id). CARRY: table conversion (red). Others code on fixtures |
| 00:45-02:30 | CORE: prompt v2 head + index + `publish_v2` + `call.py`. RESOLVE, REPORTS, IDEAS: red-first then code. CARRY: state + apply_turn against tables |
| 02:30-03:00 | CORE publishes v2 on the eval/dev DB; records the dev split (~$1); recordings land in `fixtures/` for all lanes |
| 03:00-03:30 | Merge RESOLVE, then REPORTS and IDEAS (independent) into integration |
| 04:00-04:45 | Merge CARRY (answer.py wired to real handlers); table + replay suite green |
| 04:45-05:15 | CORE commit 2: engine hook + flag live on dev copy; touched v1 tests (`pytest -n 2`) |
| 05:15-07:00 | Dev-split gate runs (4 planned, more if useful) + paired flips; fixes in owning lanes; live checks |
| 07:00-07:45 | Hidden run #2 on the final candidate; spend reported |
| 07:45-08:30 | Crew: console live checks en/zh/ms/mixed, `laneboard/scripts/<PR>.md`, test copy up + probes |
| 08:30-09:00 | Morning report to owner: pass-bar table, flips, spend, hand-test URL only if the bar is met |
Hand-test outline (one fact per line, nothing invented, no internal keys): A "td draw for SRTWC286" -> "the P one" ->
"stock?" -> "马桶 600mm 图纸" (Length/Height?) -> "length" -> "back to SRTWC286 stock". B "top 10 customers by sales last
month" -> "by qty" -> "SORENTO brand" -> "by salesman instead" -> "the 2nd one" -> "compare with last year" (not available
yet) -> "low stock basin" -> back to sales; inexact customer name -> name candidates. C idea -> similar list -> "yang
kedua" -> missing-field reply updates it -> two ideas ("save both?"). D "boleh hantar gambar SRTWC8066", "tq bos",
"那个第二个呢", two misses -> handover offer.
