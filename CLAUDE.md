# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo layout

Monorepo. Four siblings:

- `sorento_crm_frontend/` - Next.js 15, React 19, Tailwind 4, NextAuth, Metronic 9 + ReUI shell. No database of its own (Prisma is gone); calls FastAPI for all business logic, users and sessions included.
- `sorento_crm_backend/` - FastAPI + SQLAlchemy + Alembic. All `/api/v1/*` business logic, RBAC, RQ workers, embedding pipeline.
- `sorento_crm_mcp/` - Read-only Streamable HTTP MCP server. Wraps backend GETs as MCP tools for n8n.
- `sorento_crm/` - Top-level `docker-compose.yml` + `deploy.sh` for the full stack.

Shared docs live in `documentation/`. `PRINCIPLES.md` at the repo root governs (it holds the layering rules the deleted `ARCHITECTURE-RULES.md` used to carry); treat `documentation/reference/ADR-PRODUCT-STANDARDS.md` as binding alongside it.

**Plans:** every implementation/design plan (from planning sessions, grill-me, etc.) is written to `documentation/plans/<domain>/PLAN-<slug>.md`, with its UAC file alongside as `<slug>-acceptance-criteria.md` (or `UAC-<slug>.md`, the older-convention name some plans still use), before implementation starts. Update the plan's Status line as work progresses. Once that Status line reads terminal (implemented, done, shipped, merged, built, deployed, complete, closed, superseded, or abandoned), the plan and its UAC move to `documentation/plans/_archive/<domain>/`.

## Common commands

### Backend (`sorento_crm_backend/`)

```bash
# venv + deps
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# migrate
alembic upgrade head
alembic revision --autogenerate -m "msg"
alembic downgrade -1

# dev server (root has main.py shim re-exporting app.main:app)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# or: ./run.sh

# RQ worker (imports queue) - needs REDIS_URL
python worker.py

# tests
pytest                                 # all
pytest tests/test_rbac.py -q           # one file
pytest tests/test_rbac.py::test_x      # one test
```

`SORENTO_ENV_FILE=.env.ci-tests pytest ...` runs tests against a private DB without touching
`.env` - the file a running dev server also reads (see `app.config._resolve_settings_env_file`,
which Settings itself reads, and `app.main._load_env_file`, which does the same for os.environ).
A SORENTO_ENV_FILE that is set but does not resolve to an existing file raises at import instead
of silently falling back to the real `.env`.

Pyright: root `pyrightconfig.json` points to `sorento_crm_backend/venv` and Python 3.12.

### Frontend (`sorento_crm_frontend/`)

```bash
npm install --force          # React 19 peer-dep conflicts; --force is expected
npm run build                # production
npm start        # copies .env.staging -> .env.local first
npm run lint                 # eslint .
npm run test                 # vitest run
npm run test:watch
npm run test:e2e             # playwright (e2e/, chromium, baseURL :3000)
npm run format               # prettier --write .
npm run format:check         # prettier --check . (currently red: 1743 files predate the config, see BL-008)
```

Vitest: jsdom env, `@/` aliases repo root. Single test: `npx vitest run path/to/file.test.ts`.

### MCP server (`sorento_crm_mcp/`)

```bash
pip install -e ".[dev]"
export CRM_BASE_URL=http://localhost:8000 EXTERNAL_API_KEY=...
python -m sorento_crm_mcp        # default :8765/mcp
pytest                            # tests/
```

### Full stack via Docker

```bash
docker compose up -d            # from sorento_crm/ (root compose at sorento_crm/docker-compose.yml)
```

## Dev sessions (Claude-managed)

For any development task, Claude boots and owns the local stack as **background Bash sessions** so the user can test immediately. Boot all four at session start (or on first dev task):

For a lane that goes to a Claude Code cloud environment instead of a local worktree, see `documentation/agents/cloud-lanes.md`.

| Service  | Command (run from its own dir)                                                                 | Port | Reload behavior |
|----------|------------------------------------------------------------------------------------------------|------|-----------------|
| Backend  | `venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` (in `sorento_crm_backend/`) | 8000 | `--reload` - backend file edits auto-restart uvicorn; nothing to do |
| Worker   | `no_proxy='*' PGGSSENCMODE=disable OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES venv/bin/python worker.py` (in `sorento_crm_backend/`) | -    | **No reload.** Restart manually after editing any RQ task (`app/tasks/*`). |
| Frontend | `npm run dev` (Turbopack; in `sorento_crm_frontend/`)                                                      | 3000 | **HMR - edits hot-reload; no rebuild.** ONE dev server machine-wide; see "Frontend dev loop". |
| MCP      | `CRM_BASE_URL=http://localhost:8000 EXTERNAL_API_KEY=<from backend .env> backend venv's python -m sorento_crm_mcp` (in `sorento_crm_mcp/`; package installed in the backend venv) | 8765 | Restart manually after MCP code/catalog changes |

- Run each as `run_in_background: true` Bash so logs are inspectable and sessions survive across turns.
- Before booting, check ports (`lsof -i :3000 -i :8000 -i :8765 -sTCP:LISTEN`) and the worker (`ps aux | grep worker.py`) - if already running, reuse, don't double-boot.
- **Worker is required for imports.** RQ jobs on the `imports` / `respond_io` / `catalogue_render` / `media` / `flyer_read` queues (Excel imports, GRN lines, Respond.io sends, catalogue PDF exports / dealer-kit PDF renders, chatbot media extraction, dealer-kit flyer reads) run ONLY on the worker - the API process no longer drains them in-process. No worker = uploads enqueue but never process (a flyer read sits at `Processing` forever), and every `/external/media` turn waits out `media_sync_wait_seconds` then returns `pending`. Queue list is overridable via `WORKER_QUEUES` (see `worker.py`). The `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` prefix is mandatory on macOS: RQ forks a work-horse and the Obj-C runtime aborts it (signal 6) without it. `PGGSSENCMODE=disable` is mandatory for the same reason and a different signal: without it the forked child segfaults (signal 11) in libpq's Kerberos/XPC path the first time it opens a connection, and the job dies before it runs - see the lesson at the bottom of this file. Needs `REDIS_URL` reachable (`redis-cli ping`). Run `worker.py` with `ENABLE_SCHEDULER` unset locally (cron ticks aren't needed for most dev).
- **Remote testing (laptop over Tailscale / LAN):** FE `.env.local` leaves `NEXT_PUBLIC_API_URL` UNSET and sets `FASTAPI_INTERNAL_URL=http://localhost:<be port>`, `AUTH_TRUST_HOST=true`, `NEXT_DEV_ALLOWED_ORIGINS=tehs-mac-mini,*.ts.net,<tailscale ip>`. Browser calls then stay relative and `next.config.mjs` proxies `/api/v1` to the lane backend, so `http://<tailscale name or ip>:<fe port>` works with no tunnel and no CORS entry. A `NEXT_PUBLIC_*` value is inlined into the browser bundle, so `localhost:8000` there means the LAPTOP, not the Mini. Env or `next.config.mjs` edits need a dev-server restart.
- **Dev auto-login (crew test copies only):** `DEV_AUTO_LOGIN` signs a local copy in without a password. It is mutually exclusive with remote testing: the FE answers only when `next dev -H 127.0.0.1`, and both sides need the same `DEV_AUTO_LOGIN_SECRET`. Setup and every guard: `documentation/plans/identity/PLAN-dev-login-bypass-03oct.md`. Never set it in any committed or deployed env file (a test fails).
- FE edits hot-reload - no action needed, and no rebuild (see "Frontend dev loop" for the build rule).
- Backend changes need no action beyond confirming uvicorn's reload log line - **except** edits to `app/tasks/*` (RQ tasks), which require restarting the Worker session.
- **Pre-push hook mirrors CI's fast gates (alembic heads, py3.12 compile, touched vitest, dash guard) locally.** Install once per clone: `git config core.hooksPath scripts/git-hooks` (see `scripts/git-hooks/README.md`; bypass with `SKIP_PREPUSH=1 git push`).

## Architecture

### Auth boundary between FE and BE

Staff tokens are opaque sessions, not JWTs. NextAuth's Credentials provider posts to FastAPI `/api/v1/auth/login`, which mints a `user_sessions` row (`app/services/user_session_service.py`, 30-day sliding for every sign-in with no remember-me choice, `auth_method` recorded) and returns its opaque token as `apiToken`; NextAuth keeps it inside its own httpOnly cookie and hands it to the browser through `/api/auth/token`. Every `/api/v1/*` call sends `Authorization: Bearer <token>`, and `get_current_user` (`app/dependencies.py`) resolves the row on each request, so revocation and role or status changes apply at once. `JWT_SECRET` still signs the few short-lived link tokens (dealer-kit render, SLA summary, ticket draft), not staff sessions.

Alternative principal: `X-API-Key`, resolved through `integration_api_keys` to an `integrations` row and that integration's `act_as_user_id` (a real user; `app/services/integration_auth.py`). There is no `system` principal any more: RBAC and audit apply to the act-as user, and audit rows name the integration (`actor_type = integration`, plan `documentation/plans/identity/PLAN-unified-identity-26sep.md` section 8). The legacy shared `EXTERNAL_API_KEY` keeps working because its hash was seeded as an integration; `EXTERNAL_API_KEY_ACT_AS_USER_ID` is still read by a few in-process callers (chatbot business lane, order inquiry import) that act without a request. The MCP server sends the key.

NextAuth routes (`/api/auth/*`) stay in Next.js. Everything else is FastAPI under `/api/v1/*`.

### Backend module structure

Routes are mounted in `app/api/v1/__init__.py` per domain (`master_data`, `order_management`, `inventory`, `procurement`, `marketing`, `forms`, `complaints`, `sla`, `resources`, `user_management`, `workflow_forms`, `incoming_stock`, `integrations`, `notifications`, `list_query`, `audit`, `system`, `external`, `public`).

Each router is wrapped in `Depends(require_module_enabled_with_api_key("<module_key>"))` from `app.modules.runtime.guards`. The guard short-circuits when `module_guard_strict` is off OR when the user has `superadmin`/`admin` OR when the tenant has no module rows yet (legacy installs). Per-tenant module enablement lives in `app.modules.runtime.installer`. Multi-tenant is stubbed: `_tenant_id_for_request()` returns `DEFAULT_TENANT_ID` until real tenant resolution lands.

`app/modules/<domain>/bootstrap.py` files are compatibility shims declaring `MODULE_KEY`. Domain code still lives under `app/api/v1/...`, `app/services/...`, `app/models/...`, `app/schemas/...`.

### List query / DataGrid contract

`app/services/list_query_registry.py` registers per-resource SQLAlchemy models, response schemas, and serializers. Frontend personalization (column order/visibility) is keyed by `listing_key` - either a permission slug (e.g. `order_management.orders.view`) or `<perm_slug>::<stable_id>`. Endpoints: `GET|PUT|DELETE /api/v1/list-query/column-config/{listing_key}`. See `documentation/reference/LISTING-COLUMN-PREFERENCES.md`.

### Embedding pipeline

Event-driven worker writes pgvector embeddings outside the request path. Producer = SQLAlchemy listeners in `app/services/embedding_change_listener.py` + `embedding_events.py`; consumer = `embedding_worker.py` over a queue. Source-of-truth is still the OLTP tables - embeddings are for RAG/semantic search only. Stock/order numerics intentionally not embedded (answer via SQL). See `pgvector-event-driven-functional-spec.md`.

### Frontend layering (enforced)

```
UI → Hooks (useXxxMutations / useXxxQuery) → feature service (services/xxxService.ts) → lib/api-client → backend
```

Hard rules (`PRINCIPLES.md` Layering section; `ARCHITECTURE-RULES.md` no longer exists):

- Use `extractApiError(response, fallback)` from `lib/api-client` - do not hand-roll `response.json().catch(() => ({}))`.
- Use `buildDataGridParams(params, extra)` for DataGrid query strings - do not hand-build `URLSearchParams` for `page/limit/sort/dir/query`.
- User selects: `services/userSelectService` (no per-feature `getUsersSelect`).
- DataGrid listings MUST use `tableLayout: { width: 'fixed', columnsResizable: true }` with `columnResizeMode: 'onChange'`; columns need explicit `size` and long text uses `truncate` + `title`.

Mutation hooks: shared `useCreateMutation` / `useUpdateMutation` / `useDeleteMutation` patterns. On success: invalidate queries + toast. On error: extracted message + toast.

### CRUD UX standard

**`PRINCIPLES.md` "Design mandates" + `documentation/reference/ADR-PRODUCT-STANDARDS.md` +
`documentation/reference/DESIGN-LANGUAGE.md` are the contract.** Summary only, so the three
cannot drift:

- List = DataGrid + search/filters + Add. Create/edit = **modal by default**; dedicated page only
  for complex/multi-tab/file-centric flows. View = `/{module}/{id}` detail page rendering **every**
  section, with an explicit empty state (heading + hint, no button: one CTA per page, in the
  header, and no subtitle; `DESIGN-LANGUAGE.md` section 6).
- **Delete = hard delete, no confirmation dialog** (D7, Apple Alignment S6). A destructive or
  detach action - Delete, Archive-as-delete, Unlink - is a server-deferred pending action: the
  button becomes a countdown (10s hard delete / 5s reversible, both from System Settings) with a
  Cancel, the server commits when the window lapses even if the tab is closed, and Escape does not
  cancel it. Never `confirm()`; `ConfirmDeleteDialog` is retired - a new importer of it or of a
  destructive `AlertDialog` is a defect. A soft-delete endpoint is called Archive, never "delete".
- **View and Edit are the SAME layout** - same tabs in the same order, same fields in the same
  order; editing swaps a read-only value for an input in place. Read-only metadata sits directly
  under the page header, never in a tab body.
- Detail pages carry prev/next record navigation (`components/common/RecordNavigation`).
- Every optional select is `clearable`. Every dropdown is `SearchableSelect`/`SearchableMultiSelect`.
- Usable and non-clipped at **375px AND 1280px**.

### Cursor rules (apply to all `.ts`/`.tsx`)

- **No UUIDs in the frontend UI.** Resolve to human-readable identifiers.
- **No feature explanations inside the UI itself.** Put them in the Outline user guides / FAQ.

### Backend service conventions

- App exception flow: raise `app.services.error_handler.AppException` (caught by the global handler in `app/main.py` and serialized to JSON with the correct status). Validation errors get a custom 422 handler.
- Audit listeners: registered at startup via `app.services.audit_service.register_audit_listeners`.
- Logging middleware: `app.middleware.logging_middleware.LoggingMiddleware`.
- Background scheduler initializes in `startup_event` in `app/main.py`.

## Env quick reference

Backend (`sorento_crm_backend/.env`): `DATABASE_URL`, `DIRECT_URL`, `JWT_SECRET`, `JWT_ALGORITHM`, `API_HOST`, `API_PORT`, `CORS_ORIGINS`, `REDIS_URL`, `AWS_*`, `CLOUDFRONT_*`, `STORAGE_DEFAULT_PROVIDER`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_CDN_DOMAIN`, `N8N_WEBHOOK_URL`, `EXTERNAL_API_KEY`, `EXTERNAL_API_KEY_ACT_AS_USER_ID`, `USE_REMOTE_TIME`, `RESPOND_*`, `DEALER_KIT_PRINT_BASE_URL`.

`DEALER_KIT_PRINT_BASE_URL` is where the PDF worker reaches the FRONTEND to render a catalogue (inside compose this is the service name, not the public hostname). It defaults to `http://localhost:3000`, so an unset value in a container renders nothing and the export fails on a render timeout.

Storage routing: each `attachments` row carries a `storage_provider` (`s3` or `r2`). New uploads use `STORAGE_DEFAULT_PROVIDER` (defaults to `s3`); reads (preview, download, presigned URL, webhooks) dispatch through `app/services/storage_router.py` so traffic for already-migrated rows is served via Cloudflare R2 + CDN while remaining rows continue to hit S3 + CloudFront. Use `scripts/migrate_attachments_to_r2.py` to copy bytes and flip provider per row.

Frontend (`sorento_crm_frontend/.env` or `.env.local`): `NEXTAUTH_SECRET` (must align with backend `JWT_SECRET` if sharing tokens), `NEXTAUTH_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_BASE_PATH`, `GOOGLE_CLIENT_*`, `EXTERNAL_API_KEY`, `SMTP_*`, `STORAGE_*`, `RECAPTCHA_*`, `FRONTEND_BASE_URL`.

MCP (`sorento_crm_mcp/`): `CRM_BASE_URL`, `EXTERNAL_API_KEY`, optional `CRM_MCP_HOST/PORT/TIMEOUT/MAX_RESPONSE_BYTES/LOG_LEVEL`.

## Simplest thing that works (governing)

`PRINCIPLES.md` carries this as its first section and it outranks everything below. Restated
here because this is the file that gets read while code is being written:

**Build the most direct thing that satisfies the journey, and nothing more.** Two designs that
both work: the one with fewer moving parts wins.

- A registry, rule engine, abstraction layer, configuration surface or plugin point needs a
  problem that exists **today**, in this codebase, with evidence. "We might want to configure
  this later" is not evidence. Name the trigger in the plan instead, so the machinery gets built
  when the condition actually arrives.
- One event does not need a registry. One preference does not need a table - add the column.
  Let the second case pay for the generalisation.
- A precedent is not evidence. Copy a mechanism only with the justification that earned it.
- **Check whether it already exists, before designing it.** Read the code; where the claim is
  about behaviour, measure against real data. Something that ships already and is merely broken
  needs a repair plan, not a build plan.
- Push back on review findings that add layers. The reviewer owes the same evidence.

## Development methodology

**`PRINCIPLES.md` steps 0-6 is the contract; `/feature` (`.claude/skills/feature/SKILL.md`)
executes it.** Not restated here - one copy, one place to update.

The shape: journey → grill → UAC → plan → tickets → **Phase 1** frontend-first against mocks (no
backend, no tests yet) → **Phase 2** tester-first backend wiring, test-FIRST (the `tester` agent
writes the red tests from the UAC + captain's test list BEFORE the `coder` agent, one instance
kept alive for the whole lane, makes them green; pytest + vitest land here, never deferred) →
**Phase 3** `reviewer` + browser verification in parallel, once per lane (`security-reviewer`
joins only when the diff touches auth, RBAC, external ingest, uploads or multi-company scoping;
otherwise it is skipped and the PR body says so) → DoD gate → PR. `guide-writer` no longer runs
per lane (owner ruling, 24 Sep 2026): it runs on-request or as a weekly batch over merged lanes -
see `.claude/agents/guide-writer.md`.

Skipping or reordering a phase is a process violation; if a phase genuinely cannot be done, say so
in the PR description.

**Track is chosen by the diff, not by feel** (`PRINCIPLES.md` "Small fix track", owner rulings
18 Sep 2026 + 24 Sep 2026): a lane whose expected diff is under ~300 changed lines, with no
migration, no auth/RBAC/permission change, and no new external ingest surface, is the small fix
track - no DB clone, one coder writing tests + fix, one reviewer, browser pass only for a
changed screen. Name the track in the plan's Status line.

## Lane merge discipline (standing rule, 2026-09-02)

- **One lane = one branch = one PR.** A feature lane's slices land as commits on the lane branch,
  folded by the captain; slices are never opened as separate GitHub PRs. The user tests the lane
  once on its stack, then one merge. A merge does NOT deploy (owner decision 29 Sep 2026): a push
  to main runs only the alembic head gate; the release is a `workflow_dispatch` of `deploy.yml` on
  main that the orchestrator triggers after a batch has merged, and its deploy job waits for the
  owner's approval in the GitHub `production` environment (see
  `documentation/plans/ci/PLAN-ci-fast-gate-29sep.md`). Exception: genuinely independent work in a
  different domain gets its own lane, not a sub-PR.
- **Pre-PR gate (mandatory before marking a PR ready, and again right before merge if another
  lane merged first):**
  1. `git fetch origin main` and merge or rebase main into the lane.
  2. `./scripts/alembic-reparent.sh` re-parents the lane's first new migration onto main's
     current alembic head (manual down_revision flips are retired).
  3. Confirm CI's fast gate logic locally: single alembic head.
- CI enforcement already in place: `check-migration-heads` job fails any PR/merge-queue entry
  with two heads; `build-and-deploy` runs in the `deploy-production` concurrency group so
  two releases dispatched close together deploy serially instead of overlapping.

## Browser verification (agent-browser)

Frontend changes are not done until verified in a real browser. Type-check + Vitest = code
correctness, not feature correctness. **Use `agent-browser` (headless), never Playwright MCP** -
`npx -y agent-browser@0.27.0 <command>`; read `agent-browser skills get core --full` before driving it.

Three rules that cost real time when broken:

- **Navigate by sidebar clicks from `/`, never a deep URL** - a deep URL hides nav-config,
  `moduleKey` and permission-gating bugs.
- **The daemon's browser is SHARED across every agent on this machine, and it is one tab list.**
  Another agent's `open` navigates the page out from under you with no warning. Run `get url`
  before you trust any read. Never `close --all` - close only your own session.
- **`scrollintoview @ref` before `click @ref`** for anything in a scroll container; an off-screen
  click is a silent no-op.

No NEW Playwright spec is added (standing order) - a recorded agent-browser evidence run stands in.
The ~40 committed specs in `e2e/` are untouched and still run.

Full policy, command table, login env vars and the persisted-spec rules:
`documentation/agents/browser-verification.md`.

## Frontend dev loop

**`npm run dev` (HMR) for ALL frontend work and agent-browser verification, sub-agents included -
and exactly ONE dev server running at a time, machine-wide. `npm run build` ONLY when the user
explicitly asks for a hands-on build.** Standing rule since 2026-08-15, reaffirmed 2026-08-23; it
supersedes the older "every handoff is a prod build" habit.

**Why:** HMR is what makes iteration fast - an edit is visible in seconds, and a rebuild throws that
away. Prod builds peg several cores for minutes, and parallel ones from several worktrees took the
machine down twice in one day (load 90-190 on 10 cores, dev Postgres at 96/100 connections). Never
spawn a build "for handoff" on your own initiative.

- **ONE dev server, machine-wide, not one per worktree.** Before starting one, check
  `lsof -i :3000 -sTCP:LISTEN` and `ps aux | grep 'next dev'` - if another lane already has one,
  use it or ask, do not start a second. Kill only a server you started yourself.
- Edits hot-reload; do NOT kill/restart it to see a change. That is the whole point of the rule.
- Hand off on the dev server. It is the environment the work was verified in.
- Dev hides some `next build`-only errors (RSC / server-component / type). If that error class
  matters for the change, **say so and ask** rather than building.
- Auth on dev: the dev server must share the backend `JWT_SECRET` via `.env.local`
  (`NEXTAUTH_SECRET`) or NextAuth login flaps on :3000. Fix the env, don't build around it.
- If HMR wedges: `rm -rf sorento_crm_frontend/.next`, restart `npm run dev`, hard-refresh.
- **A finished lane gives its `.next` back.** A build cache reaches 2-3G per
  worktree and never shrinks, so idle lanes cost tens of gigabytes (they reached
  44G across 23 worktrees on 2026-08-24). It is regenerable, so it must not
  outlive the work. When a PR merges or a lane is abandoned, run
  `./scripts/worktree-gc.sh --apply` from the primary checkout (add `--merged` to
  also drop clean worktrees already in `origin/main`, `--deep` for `node_modules`
  and `venv`), then `git worktree prune`. The script skips any worktree running
  `next dev` and never kills a process. This is `/feature` Step 11.
- **Post-merge cleanup is pre-authorised, not owner-run** (`PRINCIPLES.md` "Post-merge cleanup",
  owner ruling 24 Sep 2026): once a lane's PR merges, the captain removes the worktree, drops its
  `.next`, and drops that lane's private `*_ci` DB + redis index without asking, subject to the
  two standing guards there (grep every remaining worktree's `.env*` before any `DROP DATABASE`;
  never touch a worktree that is a live process's cwd the captain did not start).
- **Never `npm run build` while a `next start` serves that same `.next`** - the build replaces chunk
  files under the running server, which keeps its old manifests, so pages come back half-rendered.
  The tell looks like a code defect elsewhere: `tests/test_dealer_kit_pdf_render.py` failed 5 of 7
  with "no tiles were drawn" on a branch where nothing about rendering had changed.
- At most 2 sub-agents driving a headless browser at once; each closes its own session by name,
  never `close --all`.

## PR checklist

`documentation/reference/PR-CHECKLIST.md` - verify CRUD pattern, deferred-action delete (no confirm dialog) + hard-delete semantics, empty states render, no duplication of `extractApiError` / `buildDataGridParams` / user-select helpers, and the Apple Alignment items (status pill via `Badge`, `rowHref`, `PageHeader`, line tabs, icon-button labels).

## Lessons learned

**Full log: `LESSONS-LEARNT.md` (113 entries).** Read it when a bug's cause is not obvious, before
touching the worker, migrations, tests-in-CI, or anything Respond.io / Outline / storage related.
When a lesson's cause is fixed in code, retire the entry rather than leaving it to accumulate.

The ones that bite most often, in one line each:

- **Worker on macOS needs `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES PGGSSENCMODE=disable no_proxy='*'`** - without them the forked work-horse dies (signal 6 / 11) before your task runs, and the row stays `processing` forever.
- **Tests run on Postgres ONLY, never sqlite.** Use `tests/_pg_fixture.py`. sqlite coerces UUIDs to ints and pollutes shared metadata.
- **CI's database has NO data** - the local one is a prod copy. Seed your own chain; never `LIMIT 1` off an existing table.
- **A new DB column must be added to BOTH manual dict builders** (`get_user`/`get_me`, `system_settings`) or it never reaches the FE.
- **`response_model` silently drops undeclared fields.** Assert the field in a test.
- **A migration-seeded reference table needs `__company_shared__ = True`** or its own seeds are invisible to scoped users (looks like an auth bug).
- **Alembic: head revision id must be ≤ 32 chars**, and `down_revision` must chain onto a committed main head. Re-check `alembic heads` right before merging.
- **Backend table names ≠ model class names.** `grep __tablename__` before writing raw SQL.
- **NEVER use em-dashes or en-dashes in any writing** - code comments, commits, PR bodies, docs, chat.

## Agent skills

Reference docs this file defers to, so nothing is stated twice:

| Doc | Holds |
| --- | --- |
| `PRINCIPLES.md` | The binding contract: methodology, DoD gate, design mandates, layering, hard-fail rules. **Governs on conflict.** |
| `LESSONS-LEARNT.md` | 80 gotchas. Read before debugging anything non-obvious. |
| `documentation/agents/browser-verification.md` | Full agent-browser policy + command table. |
| `documentation/agents/chatbot-verification.md` | The chatbot's equivalent: the console check, run per PR and after deploy. |
| `documentation/reference/ADR-PRODUCT-STANDARDS.md` | CRUD/UX standard in full. |
| `documentation/reference/DESIGN-LANGUAGE.md` | Tokens, motion presets, primitives roster, external-skill precedence. |
| `documentation/reference/PR-CHECKLIST.md` | Reviewer checklist. |
| `CONTEXT-MAP.md` | Which glossary covers which domain. |


### Subagent model routing (standing rule, 2026-08-30)

The main session (Fable) plans and briefs; execution subagents run on **Sonnet** by default -
`coder`, `tester`, `guide-writer` and `triage` declare `model: sonnet` in `.claude/agents/`;
`reviewer`, `security-reviewer` and `planner` stay `model: opus` (the review is the quality gate
before a PR, and it has caught merge-blocking defects the cheaper pass would risk missing -
captain's call, 30 Aug 2026). This routing stands as of 24 Sep 2026: the `opus` alias currently
resolves to Opus 5.5, whose default effort is `medium` (one step below Opus 5), but the review
seat is still the quality gate, so neither this default nor the per-spawn escalation rules below
change. The captain's job is to make the brief precise enough that Sonnet
can execute it mechanically: measured facts, exact file paths, the test list, the contract
shapes. A vague brief is the captain's defect, not a reason to upgrade the model.

**One coder per lane, continued not respawned.** The `coder` agent stays alive for every slice
of a lane; the captain sends fix rounds and later slices as follow-up messages to the same
agent instance instead of a cold spawn, so worktree state and context carry over. Reviewer,
security-reviewer and browser verification run once per lane, in parallel, after the coder -
not once per slice.

Escalate a SINGLE spawn to Opus (pass `model: "opus"` on the Agent call; do not edit the agent
files) only for:

- **Complex architecture / tangled refactors** - deeply interdependent state machines or
  cross-cutting refactors that a mechanical brief cannot fully pin down (`planner` stays
  `model: opus` for the same reason).
- **Hard debugging** - a bug that survived a Sonnet diagnosis pass or has a non-obvious cause.
- **Critical security reviews** - auth boundaries, permission gating, external ingest surfaces,
  anything `/security-review`-shaped.
- **Drift control** - a Sonnet coder that rewrote the plan, ignored the UAC, or drifted from the
  brief: rerun that slice on Opus rather than iterating with the drifting agent.

Never spawn subagents on Fable. Escalation is per-invocation and should be named in the brief
("on Opus because ...") so the reason is auditable.

### Delivery pipeline

Non-trivial feature work runs through **`/feature`** (`.claude/skills/feature/SKILL.md`), which
executes the mandatory order in `PRINCIPLES.md` and calls the `mattpocock-skills` plugin at each
slot. Two overrides: UAC + PLAN **files** under `documentation/plans/` are the contract (tickets
are only the queue), and the frontend mock is built before any backend code (so `/implement` is
scoped to Phase 2). See the skill map at the bottom of that file.

### Session handoff (instead of autocompact)

Long sessions take a deliberate cut rather than letting autocompact pick one: **`/handoff`**
writes a resume document to `.claude/handoffs/<UTC ts>-<slug>.md` (gitignored, worktree-local),
the user runs `/clear`, then **`/resume-handoff`** restores from it - re-reading the artifacts
the document points at and re-checking its "Assumed, not verified" section before acting. An
agent cannot clear its own conversation, so the middle step is the user's; an unattended agent
instead reports `blocked:` with the document path and its supervisor resumes from it. **Never run
`/compact`** - it is the lossy summary this replaces, not a lighter alternative to it. Upstream
`/mattpocock-skills:handoff` stays available for work that leaves this checkout (it writes to
the OS temp dir and has no resume half). See `documentation/agents/session-handoff.md`.

### Issue tracker

GitHub Issues on `jayson-odoo/sorento-crm`, via the `gh` CLI. See `documentation/agents/issue-tracker.md`.

### Triage labels

Canonical five-label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `documentation/agents/triage-labels.md`.

### Domain docs

Multi-context: `CONTEXT-MAP.md` at the root points at two glossaries; ADRs in `documentation/adr/`. See `documentation/agents/domain.md`.
