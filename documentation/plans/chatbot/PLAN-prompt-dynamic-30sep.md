# PLAN - parser prompt wired to its registries + prompt editor that shows it (PROMPT-DYNAMIC)

Status: Review done; pdyn_0003 (owner production text of 1 Oct 2026 as an unlabelled variable version) built, waiting on crew deploy for its version number; waiting on the owner (publish) and the prod-copy render diff (AC-PD-8). Track: full (migration, new admin table, new admin page). Grill run late (see "Grill"). R5c shipped (7f004bf0);
backend S2-S4 under TDD; R5a/b wait on the mock
(`documentation/mockups/prompt-editor-dynamic.html`). Lane `crew-lane: PROMPT-DYNAMIC`, PR #1405.
UAC: `prompt-dynamic-30sep-acceptance-criteria.md`.

## Journey

The owner opens AI Assistant > Prompts > Chatbot semantic parser. He sees the wording he edits,
and every list the CRM owns (domains, their switch words, status words, entity kinds, specs,
brands, teams, agents, access levels) shows as a chip that names its source and row count.
He adds "sales analysis" as a status word on the Chatbot Status Words page, runs a test turn,
and the rendered prompt the model received carries it without a publish. He deletes a chip to
write the list in his own words; the chip does not come back on a later publish. In a diff he
steps change by change; in search, Enter takes him to the next match and he types right there.

## Facts measured (base b8cdbebe4, `sorento_crm_backend/app/services` unless noted)

- Runtime: `chatbot/head/parser.py:590` `render('chatbot_semantic_parser')` -> the version
  labelled `production`; `ai_prompt_registry.py:1408 render` substitutes `{{var}}` only for
  variables the caller passes; `:1323 get_prompt` caches the raw template 60s per (name, label).
- The code constant `chatbot_parser_prompt.SEMANTIC_PARSER_PROMPT` is 123,870 chars; its lists:
  `domain_hint = ONE of: ...` (INTENT & DOMAIN), the DOMAIN IN MESSAGE word list, the
  ORDER_STATUS FILTER bullets, the OUTPUT `order_status` / `status` / `suggested_team` /
  `suggested_agent` enums, the ENTITY OPERATIONS `hint` enum, the ACCESS LEVELS list.
- Policy blocks (`chatbot_parser_prompt.py:730 render_prompt_blocks`) are baked into a version at
  PUBLISH time between `<<<CHATBOT POLICY BLOCKS>>>` markers; a row edit only raises the stale
  banner (`app/api/v1/system/chatbot.py:750`).
- Sources: domains `chatbot_domains` (14 rows seeded); entity kinds `chatbot_entity_kinds` (13);
  specs `product_spec_registry`; brands `brands` (already sent per turn on the user block's
  "Known brands:" line, `chatbot/head/parser.py:670-763`); access levels `contact_access_types`
  (`app/models/access.py:67`, name + sort_order + is_active); teams: `agent_teams.code`
  (`app/models/access.py:614`), code constant `lanes/escalation.py:62 ESCALATION_TEAMS` (8);
  agents: `access_agents.code` (`app/models/access.py:381`), code constant
  `contracts.py:172 SUGGESTED_AGENTS` (6; the prompt's enum lists 5, `ideation` missing);
  statuses: code constants only (`contracts.py:388 SALES_FIGURE_STATUSES`, the prompt bullets).
- Sales today is `order` + `order_status in {sales_report, sales_analysis, top_selling}`. Every
  site that special-cases it: see "S5 (R7)" below.

## Decisions

D1 **Two layers, one template.** The wording layer IS the version's template text, and it holds
   `{{name}}` registry variables where the lists sit. The generated layer is the render of those
   variables. A publish saves the owner's text as-is: nothing in any publish or migration path
   rebuilds a version from the code constant again (the `<<<CHATBOT POLICY BLOCKS>>>` bake is
   retired for this key; the blocks become `{{domains_detail}}`, `{{entity_kinds_detail}}`,
   `{{specs}}`). Deleting a chip = deleting the token; no code path re-inserts it.
D2 **Variables** (module `app/services/chatbot_prompt_vars.py`, one entry each: name,
   label, source table, admin href, `render(db) -> str`, `rows(db)` for the panel):
   `domains` (names, sort_order, ` | ` joined), `domain_words` (switch words of every domain,
   then status trigger words, de-duplicated, `, ` joined), `domains_detail` (today's
   `domain_line` per row), `statuses` (the bullets, per domain, from the new status table),
   `status_values` (`a|b|c` of every status value), `entity_kinds` (`a|b|c`),
   `entity_kinds_detail` (today's kind lines), `specs` (`specification_lines`), `brands`
   (`Name (CODE), ...`, active brands), `teams` (`a|b|c`), `agents` (`a|b|c`), `access_levels`
   (JSON list of active `contact_access_types.name` by sort_order).
D3 **Teams = the escalation lane's `ESCALATION_TEAMS` (grill 2, owner 30 Sep 2026), agents =
   active `access_agents.code`.** The lane matches the parser's team word only against
   `ESCALATION_TEAMS` (`lanes/escalation.py:1234-1261`); `suggested_agent` goes out as the round
   robin's `agent_code` (`escalation.py:1547`). Empty table (CI) falls back to the code constants so a
   fresh DB still renders a complete prompt. Equivalence against production data is proven by
   `scripts/prompt_dynamic_render_diff.py`, run by crew on the prod-copy dev DB.
D4 **Brands.** Stay on the per-turn user block (no behaviour change). `{{brands}}` exists in the
   picker and the wired panel; the migration does not insert it into the wording.
D5 **Freshness.** `render()` substitutes registry variables at request time from a process cache
   (TTL 30s) that an SQLAlchemy `after_commit` hook clears when a registry model row was written
   in that session. Other processes (worker) converge within the TTL. The template cache
   (`get_prompt`) is unchanged; the label moves only through the owner's label route.
D6 **Status words registry (R2).** New table `chatbot_status_words` (id, domain -> 
   `chatbot_domains.name`, value, label, trigger_words text[], sort_order, timestamps),
   seeded from the prompt bullets + the sales statuses (`sales_report`, `sales_analysis`,
   `top_selling`) with the owner's words ("sales", "sales report", "top selling",
   "sales analysis", "best selling"). Admin page = the Chatbot Domains page pattern
   (`app/api/v1/system/chatbot_config.py:340-403`, list + modal, deferred hard delete).
D7 **Wording migration (R1).** `pdyn_0002_wording_layer` reads the `production`-labelled template
   of `chatbot_semantic_parser` (v42 on prod, owner edits included), replaces each duplicated
   list by exact-substring match with its token, strips the baked policy blocks for
   `{{domains_detail}}` etc., and inserts the result as a NEW UNLABELLED version. A list the
   owner had reworded (no exact match) is left verbatim and logged. Labels untouched.
D8 **Drift test (R4).** `tests/chatbot/test_prompt_dynamic_drift.py`: the rendered prompt's
   lists equal their registries; the wording layer (latest version + code fallback) contains no
   literal copy of any registry list (e.g. `master_products | product_attachment`, the team
   enum, the access level JSON).
D9 **R7 sales domain.** Its own row `sales` in `chatbot_domains` (migration + `policy_rows`
   seed + `bootstrap_env` replay): switch words sales, sales report, top selling, sales
   analysis, best selling; tools `crm_sales_report`, `crm_sales_analysis`,
   `crm_top_selling_report`; reveal key `sales_orders.sales_report`; escalation
   `customer_service`. Routing moves the three statuses from `order` to `sales` at every
   special-case site (list in S5). Coordinate with PR #1401 (rebase if it lands first).

## Slices

S1 R5c search fix - DONE 7f004bf0 (tests `components/common/find-in-text`).
S2 Registry variables + render-time substitution + cache/invalidation + drift test (D1-D5, D8).
S3 Status words table, seed, admin API + page (D6); `statuses`/`status_values` read it.
S4 Wording migration + render-diff script (D7), before/after diff attached to the PR.
S5 R7 sales domain (D9): contracts `DOMAIN_HINTS` from the live table, `select_tool` /
   allow-list, `turn_runtime._report_status_means_order_domain`, business lane overrides
   (`lanes/business/__init__.py:1558-1702`), `gate.ALLOWED`, `answer.DOMAIN_LABELS`,
   `mcp_tool_domains`, `apply._is_report_hop`, `pending.py:142`, the sales addenda wording.
S6 R5a/b editor (after mock OK): chips, wired panel, preview toggle, variable picker, diff
   next/prev + counter + shortcuts + changes-only.

## Triggers deliberately not built

- Variables for other prompt keys: only `chatbot_semantic_parser` reads registries today.
- Per-variable formatting options: one format each until a second format is asked for.

## Grill (feature skill step 2, run LATE on 30 Sep 2026 after the owner's process audit)

The grill was not run before the build. It was run after the audit: every decision the lane
had taken alone went to the owner as one `crew-ask` on PR #1405 (comment 5914198889), each with
a recommendation. Answers are recorded here as they arrive. Until then, each recommendation stands. Every premise was re-checked against the code after the owner's rule of 30 Sep 2026 ("every statement grounded in file:line"); the two wrong ones (2, 7) are marked CORRECTED.

| # | Decision | Recommendation | Owner answer |
|---|---|---|---|
| 1 | Wording layer published unlabelled; the owner promotes (`pdyn_0002_wording_layer.apply`, no label row) | keep unlabelled (R3) | no answer: recommendation stands |
| 2 | CORRECTED. Teams: the lane matches `suggested_team` only against `ESCALATION_TEAMS` (`lanes/escalation.py:61-70`, `:1234-1261`), so `{{teams}}` from `agent_teams` could name codes the lane cannot route. Agents: `suggested_agent` goes out as `agent_code` to the round robin (`escalation.py:1547`, `:1579`), so `{{agents}}` from `access_agents` is right | teams = `ESCALATION_TEAMS` via `lane_vocabulary`; agents = `access_agents` | re-asked (comment 5914415234) |
| 3 | Access levels from `contact_access_types.name`, swapped only when they cover the 7 tier names. UNVERIFIED on prod: CI table empty | keep the guard | as recommended |
| 4 | Domain words = union of switch words and status words; measured +2,712 chars on the CI DB | union | no answer: recommendation stands |
| 5 | OUTPUT status enums list all 8 statuses; the mapping to `sales` is PENDING R7 | accept | no answer: recommendation stands |
| 6 | Sales addenda keep `domain_hint "order"` (constant; UNVERIFIED on prod v42) | code mapping only | owner: do NOT change his wording |
| 7 | CORRECTED. The plan-level reveal gate never runs (`turn_runtime.py:420`, `:451` grants None; `apply.py:2135`). The sales refusal is the business lane (`lanes/business/__init__.py:1558-1566`), unchanged. The `sales` row's `reveal_key` has no runtime effect today | nothing to decide | as recommended |
| 8 | `order` row keeps the 3 sales tools | remove in a follow-up | as recommended |
| 9 | Worker turns (`app/tasks/chat_turns.py:12`) converge within the 30s TTL; the API process clears at commit | 30s | as recommended |
| 10 | Code-constant publishers stand down. INCOMPLETE at first: `sales_s1_reports_module.py:121-149` was unguarded; guarded in the review round | accept | as recommended |
| 11 | Stale banner silenced for a wording-layer production | accept | as recommended |
| 12 | Brands not inserted. The per-turn line carries the contact's own companies' brands (`turn/context.py:100-116`, `head/parser.py:754`) | accept | as recommended |
| 13 | Wired panel and preview need `system.ai_assistant_settings.view`; the brand list is system-wide (raw SQL `_brands`) | accept | as recommended |
| 14 | Status Words page built without its own mock (clone of Chatbot Domains) | accept | as recommended |

## Process deviations and how each was closed (owner audit, 30 Sep 2026)

- **Red-first commit order.** S1 (R5c), S2, S3, S4 and the Status Words page were committed
  with their tests IN the same commit, so the commit order does not show red first. History
  is not rewritten (no force-push). Red was proven after the fact instead: each slice's test
  file was run against the code as it stood just before that slice:
  - R5c (7f004bf0): 3 of the 4 new tests fail on the old `SearchableTextarea`. The typing test
    reproduces the owner's bug as `'w a   b ne c'`.
  - S2 (427b309c) test file on 7e4affa3: collection error `cannot import name
    'ChatbotStatusWord'` (the model did not exist).
  - S3 (ef271758) test file on 427b309c: 11 failed, all `404 == 200/201/409/422/403` (the
    routes did not exist).
  - S4 (888f4ae3) test file on ef271758: 10 failed with `AttributeError`, because
    `wording_layer`, `is_wording_layer` and `publish_wording_edit` did not exist, and the
    migration published no version (`assert 5 == 4`).
  - Status Words page (b3a940d5): both vitest files fail when the service and the list are
    removed.
  R7 and R5a/b are committed red-test-first (`test(prompt-dynamic): red tests for ...`, then
  `feat(...)`).
- **Kill test** (captain's own, before the reviewer's independent one), each restored after:
  - AC-PD-1: the `after_commit` cache clear disabled. 4 tests go red (the committed-domain
    render, the cache test, and the API create and update/delete renders).
  - AC-PD-3/R3: the s4/s12 wording-layer guards disabled. The stand-down test goes red
    (`assert 5 == 4`).
  - AC-PD-7: the `{{statuses}}` substitution disabled. 3 drift tests go red.
  - AC-PD-10: the old `SearchableTextarea` effect restored. 3 find tests go red.

## Next lane (owner, 30 Sep 2026)

- Move `ESCALATION_TEAMS` (`app/services/chatbot/lanes/escalation.py:61-70`) into the
  `agent_teams` registry so teams become admin-editable end to end; `{{teams}}` then renders
  from that registry instead of the code list.
- Remove the three sales tools from the `order` row once `sales` has run a week (grill 8).
- The Prompts detail page overflows at 375px (document 1357px wide), measured identical on
  main b8cdbebe with this lane's files swapped out, so not this lane's defect: logged in
  `documentation/backlogs/backlog.md`.

## Browser evidence run (agent-browser 0.27.0, session `pdyn`, 30 Sep 2026)

Stack: the lane's sandbox, not the crew test copy (a cloud worker cannot reach it). Backend is
uvicorn :8000 on the private `sorento_ci` DB, bootstrapped by `scripts.bootstrap_env` with every
module installed; the frontend is `npm run dev` :3000. Login is a local superadmin
(`E2E_EMAIL`/`E2E_PASSWORD` in the gitignored `sorento_crm_frontend/.env.local`). Navigation
was by sidebar clicks from `/`.

1. 1280x800, `/`, then System > Messaging > **Status Words**. The URL is
   `/system-management/chatbot-status-words`, and the grid shows the 8 seeded rows with real data.
2. Row `sales_report`, then the modal: added the customer word "jualan bulan ini", then Save.
   The backend logged `PUT /api/v1/system/chatbot/status-words/<id>` 200, and the row's
   Customer words cell shows the new word.
3. Without a publish, `POST /ai-assistant/prompts/chatbot_semantic_parser/render-preview`
   on the v4 wording-layer template returns the word in `{{domain_words}}` and in the
   `sales_report` bullet, with 0 literal `{{` left. The version list is unchanged: v4 has no
   label and v3 is production.
4. AI Assistant > Prompts > chatbot_semantic_parser, then Ctrl+F on the editor, query
   "outstanding":
   - The count reads 1/107, the selection is [2739, 2750] = "outstanding", and scrollTop is 830.
   - Enter x3 gives 4/107. Shift+Enter gives 3/107, with the selection on [5483, 5494].
   - The active `<mark>` offsetTop is 2109, inside the visible range 1950-2268.
5. Caret at 5500, which is between match 3 (5483-5494) and match 4 (5524), then typed "ZZ ".
   The text reads `outstanding DO foZZ r 7445`, the caret moved to 5503, the find bar stayed
   open, and the count stayed 3/107. The owner's bug does not reproduce.
6. 375x812, same page: Enter moves to 4/107 and the active match is visible. The page itself
   is 1357px wide at 375. That overflow is identical with main b8cdbebe's files, so it is
   pre-existing: BL-069.

(The two screenshots of this first run were replaced by the R5a/b run below, per the
two-per-lane cap.)

## Browser evidence run, R5a/b editor (agent-browser 0.27.0, session `pdyn2`, 30 Sep 2026)

Same sandbox stack, rebooted after a container restart. Navigation by sidebar: System > AI
Assistant > Prompts > chatbot_semantic_parser. v4, the unlabelled wording layer, was loaded
from the version list.

1. 1280x800: the editor shows 12 chips and no raw `{{domains}}`. "Wired to this agent" lists 12
   sources with counts, for example Domains 15, Domain words 96, Status words 8, Teams 8 "Escalation
   lane (code list)". Brands shows "not in wording", Insert and "Also sent every turn".
2. The Domains chip's toggle shows the live list ("master_products | product_attachment |
   ..."). Its x removes it, the wired row flips to "not in wording" and the draft reads
   "unsaved changes". Undo restores the chip, and the draft is clean again.
3. Caret at offset 4, real keyboard "XY ", then Insert variable > Brands. The text starts "You XY
   [Brands chip] are the Sorento...". The chip sits exactly at the caret, and the wired Brands
   row now reads "In wording".
4. Preview rendered prompt shows 0 literal `{{`, `domain_hint = ONE of: master_products | ...`,
   and today in Malaysia time ("Thursday, 01 October 2026"). Edit brings the chips back.
5. Diff against v3 shows "Change 1 of 11". Next x2 gives "Change 3 of 11" (the domain-words
   hunk is outlined). Alt+ArrowUp gives "Change 2 of 11" (the `{{domains}}` line). Changes only
   gives 6 gaps, the first "... 67 unchanged lines".
6. Overflow. The first pass showed a horizontal scrollbar inside the editor: block chips
   carried side margins. Fixed with no margin plus `overflow-x-hidden`, then re-measured: editor
   scrollWidth equals clientWidth at 375 and at 1280, with 0 overflowing children.
7. Layout. The third column squeezed the editor to 286px at 1280, so the side-by-side panel now
   starts at 2xl. At 1280 the editor is 582px wide, with the panel below it.
8. 375x812: the document is 360px wide (no page overflow: BL-069 is fixed by the `min-w-0` grid
   columns). The wired panel stacks under the editor. The Insert variable menu ends at x=325.

Screenshots from this run were replaced (two-per-lane cap) by the owner hand-test repro below.

Kill tests, R5a/b (each restored after):
- chip serialisation dropped: 2 red;
- validateVars without the registry names: 2 red;
- diff step frozen: 2 red.

## Browser re-check after reviewer pass 2 (agent-browser 0.27.0, session `pdyn3`, 1 Oct 2026)

Fresh login, then System > AI Assistant > Prompts > chatbot_semantic_parser, v4.
- **CSS break found.** Moving the `::highlight()` rules into `css/components/prompt-find.css`
  broke the page: Next's CSS parser rejects `::highlight` ("not recognized as a valid
  pseudo-element") and the route returned 500. vitest does not parse CSS, so no test could
  catch it. Reverted to the inline style, with the reason in a comment. The page is back to 200.
- **B1, copy/paste.** Selected the text around the Domains chip, Ctrl+C, caret at the top,
  Ctrl+V. That gives 2 Domains chips and no label text in the editor.
- **B2, Enter then find.** Enter x2 mid-text leaves 0 `<div>` in the editor. Ctrl+F
  "outstanding" and Enter x3 give 4/97, active 10166-10177, and CSS highlights
  `prompt-find` + `prompt-find-active` registered. Escape leaves the selection "outstanding",
  inside the editor.
- **Tabs (Radix pill).** Preview rendered prompt shows 0 `{{`, and Edit brings the chip
  editor back.

## Owner hand test #1405 = FAIL, fix round (1 Oct 2026)

Three items from the owner. Items 1 and 2 were red-first: af0738ea (4 red), fix 6babf079 (114/114
green). Item 3: red cb915805, then 56ceba99.

1. **Find after a version switch.** The highlighted text was built from `emitted.current ?? value`.
   After a version switch, `emitted` still held the previous draft, so the ranges were offsets into
   the wrong text. `findText` is now derived from `value` (`PromptChipEditor.tsx`). Kill test (stale
   `findText`): 1 red.
2. **Insert at the caret.** Two causes:
   - the wired panel appended to the draft;
   - a registry refetch rebuilt the editor DOM, which detached the remembered Range.

   The fix has three parts:
   - The caret is remembered as a value offset (`savedOffset`) on keyup, mouseup, blur and input.
   - A registry refetch now relabels the chips in place instead of rebuilding the DOM.
   - The panel's Insert calls the editor's `insertVariable` through a ref. The editor stays mounted
     (hidden) while Preview is shown.

   Kill tests: offset memory disabled, 4 red; panel append restored, 1 red.
3. **Rendered-identical version.** `chatbot_prompt_vars.identical_wording_layer` swaps a hand list
   for its variable only when the registry renders exactly the same text, and reports every other
   list with what differs. `scripts/prompt_dynamic_identical_version.py`:
   - It is a dry run by default.
   - `--save` refuses unless the rendered diff is empty.
   - `--save` inserts one unlabelled version and is idempotent.
   - It never labels, publishes or stages.

   Kill test (always replace): 3 red. Sandbox (CI DB, production v3), rendered output identical:
   True.
   - Replaced: `{{teams}}` and `{{entity_kinds_detail}}`.
   - Kept literal, because the registry differs: domains, status_values (x3), agents,
     entity_kinds, access_levels, statuses, domain_words, domains_detail.
   - The differences on the real v53 come from crew's run on the crew copy.

Browser repro (agent-browser 0.27.0, session `pdyn4`, sandbox stack):
- Find at 1280: on v4, "current" gives 1/84, and all 84 highlight ranges read exactly "current".
  - Switch to v3 with find open: still exact.
  - Back to v4: still exact.
  - Type "current " at the top: 2/85, 0 wrong ranges.
- Panel Insert at 1280: with the caret after "domain_hint = ONE of:", Insert on Brands puts the
  chip there, before the Domains chip, not at the bottom.
- Toolbar Insert: Access levels lands after "== ACCESS LEVELS ==".
- Panel Insert at 375: the chip lands at the caret, and the document is 360 wide.

Screenshots:
- `documentation/plans/evidence/prompt-dynamic-find-1280.png`
- `documentation/plans/evidence/prompt-dynamic-insert-375.png`

## Owner requirement, 1 Oct 2026: the live production text as a variable version (replaces the "identical to v53" ask)

The owner pasted the live production `chatbot_semantic_parser` text. Crew committed it verbatim
on `crew/prod-semantic-parser-20261001` (6409fa769, 1741 lines, sha256 fdbf2ea1ba0cc019...).
This lane copies it byte for byte to
`sorento_crm_backend/alembic/data/chatbot_semantic_parser.prod-20261001.txt`. The file sits
inside the backend tree because the backend image builds from `sorento_crm_backend/`
(`sorento_crm/docker-compose.yml:28`). The text carries the owner's em dashes, so the pre-push
dash guard skips that one directory (`scripts/git-hooks/pre-push`, exclude pathspec).

Migration `pdyn_0003_prod_identical` (after `pdyn_0002`):
- Inputs: it loads the file and runs `chatbot_prompt_vars.identical_wording_layer`. That swap
  happens only where `literal == render_value(db, variable)` at migration time.
- Proof: before insert, it checks that rendering the result gives the file. If the check fails,
  it inserts the text verbatim instead.
- Insert: ONE unlabelled version at max+1, with
  `config_json {prod_snapshot, prod_snapshot_sha256, rendered_identical, identical_report}`.
- Idempotent: it skips when the same template or the same snapshot sha already exists.
- Downgrade: deletes only its own row, and only while it is unlabelled.
- `bootstrap_env` applies it after `pdyn_0002`.

Tests (`tests/chatbot/test_prompt_dynamic_prod_snapshot.py`) were red first (b307df27) and are
green at 870054ef:
- seeded registries: the variables are swapped in, and the render equals the file;
- a differing registry: the list is kept literal and reported, and the render still equals the
  file;
- no label is set and no existing version is touched;
- idempotent;
- downgrade is scoped.

Kill tests:
- guard off: 2 red;
- guard off and proof off: 2 red;
- idempotency off: 1 red;
- downgrade unscoped: 1 red.

A fresh `bootstrap_env` database ran the 79 prompt tests green.

On the sandbox (CI tables) the version came out as v5, unlabelled, rendering equal to the file:
132034 characters both. On those tables only `{{teams}}` and `{{entity_kinds_detail}}` are
swapped. Expected on prod, worked out from the text and from what this lane's own migrations
add:

| List (line in the file) | On prod | Why |
| --- | --- | --- |
| teams (773) | variable | Rendered from code (`ESCALATION_TEAMS`), the same everywhere. |
| domains (82) | literal | `pdyn_0001` adds `sales`; the text also omits `purchase_order`, which its own policy block lists. |
| status_values (770, 778, 821) | literal | The three lines differ from each other, and `pdyn_0001` seeds 8 statuses. |
| statuses (643) | literal | The text wraps each status over two lines; the registry renders one line each. |
| domain_words (87) | literal | The text carries words no registry holds (ETA, DO, SO, PO, purchase cost, price, photo, certificate, forms, GRN). |
| domains_detail (1662) | literal | `pdyn_0001` adds the `sales` row. |
| entity_kinds (401) | literal | The hint list omits `specification`, which the kind table holds (the text's own block lists it). |
| agents (773) | UNVERIFIED | Depends on prod `access_agents`. |
| access_levels (352) | UNVERIFIED | Depends on prod `contact_access_types`. |
| entity_kinds_detail (1677) | UNVERIFIED | Likely a variable: the text's block was published from the prod table. |
| specs (1691) | UNVERIFIED | Depends on prod `product_spec_registry`. |

The real answer is the migration log on deploy (`prod snapshot vN: {{x}} line L replaced|kept literal`),
and the version's `config_json.identical_report`.

### Crew-migration SQL twin (crew copy, 1 Oct 2026)

The crew copy's dev DB is migrated by SQL, not alembic. Its last crew-migration SQL (hash
97a3eba5) carried only pdyn_0001, so dev stayed at v53.

`scripts/prompt_dynamic_crew_sql.py` generates
`sorento_crm_backend/alembic/data/crew-migration-prompt-dynamic.sql`. The file is 152 KB, over
GitHub's 65,536-character comment limit, so it is committed instead of pasted. It holds:
- pdyn_0001's idempotent statements;
- a `DO` block that does what pdyn_0003 does.

How the `DO` block works:
- Each list becomes its variable only where a SQL rendering, computed the way
  `chatbot_prompt_vars` renders it, equals the owner's text. That applies to teams, domains,
  status_values, entity_kinds, agents, access_levels and entity_kinds_detail.
- domains_detail, specs, statuses and domain_words have no SQL renderer here, so they stay
  literal.
- The owner's em dashes are written as a placeholder that `chr(8212)` restores, so the file
  passes the dash guard.

pdyn_0002 has no SQL twin: its transform is Python, run over the copy's own production text.
`scripts.publish_parser_wording_layer` still publishes it.

Tests (`tests/chatbot/test_prompt_dynamic_crew_sql.py`, red fc5c72af):
- the committed file equals the generator's output;
- the file has no dash characters;
- with seeded tables the expected lists are swapped and the render equals the file;
- every SQL swap is also a Python swap;
- with differing tables the list is kept literal and the render still equals the file;
- running it twice is idempotent, it sets no label, and it leaves other versions alone;
- alembic pdyn_0003 skips once the SQL has run.

Kill tests:
- always swap: 3 red;
- wrong dash character: 2 red;
- no idempotency: 1 red;
- agent order reversed: 1 red.

`psql -f` twice on the sandbox DB:
- first run: v5, unlabelled, render equal to the file (132034 = 132034 characters). Only
  `{{teams}}` and `{{entity_kinds_detail}}` were replaced;
- second run: "already published; nothing to do".

Round 2, the same day. Crew's applier takes the comment body as `crew-migration:` plus ONE
`sql` fence and nothing else (worker-contract.md:88). The first post put prose before the
fence, and the applier ran it as SQL. The 152 KB file could not fit in one comment either
(the limit is 65,536 characters).

The generator now emits a compact `DO` block: 54,792 characters, a 54,818-character comment
body. It encodes the owner's text in three steps:
1. Its 83 distinct non-ASCII characters become `^` plus an index into a code-point table.
2. The result is LZ77-coded with `~hex,hex;` back-references.
3. Each pair of ASCII characters is packed into one code point from U+4E00.

The block decodes this in plain PL/pgSQL, with no extension, and checks the sha256 against
the file before writing anything. The pdyn_0001 statements are dropped, because crew already
applied them.

New tests:
- the comment body is exactly the prefix plus one fence, and under the limit;
- the encoding round-trips the owner's text.

Kill tests:
- always swap: 3 red;
- LZ offset off by one: 5 red;
- the sha "already published" guard removed: stays green, because the identical-template
  guard also stops a second insert.

main was merged at 41bf6c12f; `pdyn_0001` was re-parented onto `dcm_0001_compare_mappings`,
leaving a single head `pdyn_0003_prod_identical`. The backlog clash on BL-068 was resolved by
keeping main's entry and renumbering this lane's entry BL-069. On a fresh `bootstrap_env` DB,
the PR's changed backend tests pass (197), and so does the chatbot subset for sales, business,
engine, domain, status and prompt (1458 passed, 30 skipped, 5 xfailed).

Round 3, the same day. The lookup SQL (a 6 KB crew-migration comment, which finds the owner's
text on the database by its sha256) applied on dev as crew sha 0ba15480. It wrote nothing,
because dev tops out at v53 and holds no version with the owner's text.

Crew's ruling has two parts:
- seed the owner's text on dev first;
- on prod, the migration must find the live text and must never guess.

**pdyn_0003 on prod (alembic).**
- It finds the live text through the `production` label: the version that label points at.
  It never searches.
- That text must equal the owner's file character for character, with no newline folding
  and no trimming.
- On any difference it writes nothing. The log names the first difference: line, column,
  both characters, and both lengths.
- If there is no `production` label, it writes nothing and logs that.
- A failed render proof now also writes nothing; the old fallback inserted the verbatim text.
- The new version records `from_production_version`.
- Tests: red 68ea0fbf, covering one changed character, a trailing newline, CRLF line ends,
  and no label.
- Kill test (match with CRLF folded and trailing newlines trimmed): 2 red.

**The crew SQL for dev** (the full `sorento_crm_backend/alembic/data/crew-migration-prompt-dynamic.sql`,
55,862-character comment body):
1. It decodes the owner's text and checks its sha256.
2. It inserts that text verbatim, unlabelled, with commit message
   `prod snapshot 1 Oct (dev seed)`, unless a version with that exact text already exists.
3. It builds the variable version from it.

Simulated dev on the sandbox (top v4, inside a transaction that was rolled back):
- seed v5, with 132,040 characters;
- variable version v6, with `{{teams}}` and `{{entity_kinds_detail}}` replaced and the rest kept
  literal;
- a re-run is a no-op.

Kill test (seed removed): 3 red.

Round 4, the same day. Crew applied the full crew SQL on dev. It created **v54**, the seed
(132,040 characters), and **v55**, the variable version (130,682 characters). v55 swaps
`{{teams}}` and `{{entity_kinds_detail}}` and keeps everything else literal.

Each kept list was checked against the owner's text: where it first parts, and why. Measured on
the sandbox with the Python renderers; rows that depend on dev's own data are marked UNVERIFIED.

| List (line) | First difference, owner vs registry | Kind |
| --- | --- | --- |
| domains (82) | item 13: `purchase_cost` vs `purchase_order`; the registry also ends with `sales` | content: the owner's hint list omits `purchase_order`, which his own policy block (line 1674) lists; `sales` is this lane's |
| domain_words (87) | item 1: `stock` vs `catalogue` | content: a hand list (with ETA, DO, SO, PO, price, photo...) that no registry holds |
| access_levels (352) | sandbox: the table is empty | dev data, UNVERIFIED (the order or names of the `contact_access_types` rows on dev) |
| entity_kinds (401) | item 13: end vs `specification` | content: the hint list omits `specification`, which the owner's own block (line 1687) lists |
| statuses (643) | line 1: the owner wraps by hand (line lengths 89, 87, 38, 80, 35; no wrap width reproduces it); the registry also has 6 more statuses | format AND content; the format alone cannot swap, because the content differs |
| status_values (770, 778, 821) | three different subsets in the owner's text vs one list of all 8 | content: one variable cannot equal three different lists |
| agents (774) | item 6: end vs `ideation` (a code fallback when `access_agents` is empty) | dev data, UNVERIFIED (the active `access_agents` codes on dev) |
| domains_detail (1662) | line 1: `master_products ... product narrows list_all.` vs no narrowing; plus the `sales` row | dev data (master_products narrowing) plus this lane's `sales` row: content |
| specs (1691) | the crew SQL has no specs renderer, so it was never compared on dev | UNVERIFIED; the Python dry run on dev decides |

No render-format gap was found that a renderer change would close so that a list swaps. The
only format gap, statuses, also differs in content.

`scripts.prompt_dynamic_identical_version` now prints the first differing item for each kept
list, and gains `--verify N`. That flag renders version N and compares it with the owner's file,
printing the first differing line. A report line-number bug turned up during the demo: `agents`
was reported as 773, but it is on 774. It is now fixed and covered by a test.

On the sandbox, the dry run from the seed rendered identical, and `--verify` on the variable
version gave equal (132,025 = 132,025 characters).

Round 5, the same day. The owner set the goal: every hard-coded list becomes a variable, and the
version with variables renders text byte-identical to his plain-text version. The per-list table
went to crew before any rebuild (PR comment 5924667945).

**A. Format gaps, fixed in this PR.**
- **Statuses:** the quoted values are padded so the arrows line up, and the words wrap greedily
  at 89 columns with a 4-space continuation.
- **Domain words:** they wrap greedily at 89 columns.
- Tests (red cf938d1d) show that rows matching the owner's content now render his exact text.
- Kill test (wrap width 200): 3 red.

**Crew decision Q5.** The `status_values` list at line 821 is exactly the order-domain statuses.
It now uses a new variable, `order_status_values` (no data change), which the crew SQL renders
too.
- Tests: red 118970e5.
- Kill test (filter dropped): 2 red.

**Crew's dev answers** (dry run `--from-version 54`, read-only; `--verify 55` passed, 132,025 =
132,025 characters):
- access_levels: the same items, but the sort_order differs.
- agents: dev has 5 extra active codes (ideation, complaint, conversation_analysis,
  lead_time_enquiries, purchase_request).
- domains_detail: as on the sandbox (no master_products narrowing; plus the `sales` row).
- specs: matches the owner's text, so it is not a gap.

**B. Content gaps:** domains, domain_words, entity_kinds, statuses and status_values (770, 778),
access_levels, agents and domains_detail. These wait for the owner's answers (Q1-Q4, Q6); none
are built yet.

**Rebuild on dev:** `scripts.prompt_dynamic_identical_version --from-version 54 --save`, then
`--verify N`. Expected swaps: teams, order_status_values, entity_kinds_detail and specs.

The crew SQL moved to `sorento_crm_backend/alembic/data/crew-migration-prompt-dynamic.sql`, next
to the owner text it encodes. Its test reads it, and `tests/test_ci_docs_only_filter.py` rejects
a test-read `documentation/` path that CI would still classify as docs-only (CI run on e2bc03b0).

## Owner answers of 2 Oct 2026 to the per-list table, and the design

| # | List | Owner answer | Design |
| --- | --- | --- | --- |
| 1 | domains (82) | (a) add `purchase_order` and `sales` to the hint text; the registry rows win | No code. The text change is the owner's (on the Prompts page); the proposed line is posted for him. |
| 2 | domain_words (87) | (a) a curated list in the DB, seeded with his 19 words in his order | D-B2: a new table `chatbot_domain_words` (`word` unique, `sort_order`), additive migration `pdyn_0004_prompt_lists`, seeded with the 19 words. `{{domain_words}}` renders that table with the 89-column wrap. No admin page in this PR (trigger: the owner asks to edit the list in the UI). |
| 3 | entity_kinds (401) | (a) add `specification` | As 1: a text change, the owner's. |
| 4 | statuses (643), status_values (770, 778) | keep his subsets; a row attribute, additive | D-B4: `chatbot_status_words.prompt_lists text[] NOT NULL DEFAULT '{}'` (same migration). Seeds: `outstanding` and `delivered` get `statuses`, `status_values`, `status_field_values`; `sales_report` gets `status_field_values`. `{{statuses}}` and `{{status_values}}` render only rows tagged with their own name. A new `{{status_field_values}}` renders line 778. The Status Words page edits the tags (a multi-select of the three list names). The API field is optional, and a missing field keeps the row's tags. |
| 5 | status_values (821) | domain filter | Done: `{{order_status_values}}`. |
| 6 | access_levels, agents, domains_detail, specs | (a) align the DB rows to the text | A crew-ask with the exact row changes and their risks, before any prod data change (owner-gated). |

Browser check, owner answer 4 (agent-browser 0.27.0, session `pdyn5`, sandbox stack, 2 Oct 2026).
Reached by sidebar clicks: System > Messaging > Status Words, then the `outstanding` row.
- 1280: the modal shows "Parser prompt lists" with Status bullets, Order status values and
  Status field values as chips.
- The tag edit round-trips:
  - Removing "Status field values" and saving set the row to `{statuses,status_values}`, and
    `{{status_field_values}}` rendered `delivered|sales_report` with no publish.
  - Re-adding the tag in the multi-select and saving restored
    `{statuses,status_values,status_field_values}`.
- 375: the dialog spans 0 to 375 and the field 25 to 335; the document is 375 wide, so there is
  no overflow.

Owner answers, round 2 (2 Oct 2026):
- **Q-A (a):** the owner applies the 3 text edits (lines 82 and 401, plus the `sales` line in the
  policy block) himself, as a new plain-text version; the rebuild starts from it.
- **access_levels reorder:** approved, as `pdyn_0005_access_level_order`.
- **Q-B (b):** `access_agents.in_parser_prompt`, as `pdyn_0006_agents_in_prompt`. `{{agents}}`
  renders flagged rows only.
- **master_products narrowing:** fixed on DEV only (crew-migration). The owner ran the
  read-only prod check `SELECT narrowing FROM chatbot_domains WHERE name='master_products'`,
  which returned `{"product": "list_all"}`. Prod already matches his text, and this PR changes no
  prod narrowing data.
