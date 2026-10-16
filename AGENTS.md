# AGENTS.md

Guidance for Codex agents working in this repository. This document is based on the current codebase, root docs, package configs, and README files. Items labeled **Assumption** are inferred from nearby code or docs rather than enforced by tooling.

## Repo Layout

This is a Sorento CRM monorepo with these main projects:

- `sorento_crm_frontend/` - Next.js 15 / React 19 frontend using App Router, Tailwind 4, NextAuth, Prisma for auth/user/session data, TanStack Query/Table, Vitest, and Playwright.
- `sorento_crm_backend/` - FastAPI backend using SQLAlchemy, Alembic, Pydantic v2, APScheduler, Redis/RQ, S3/R2 storage helpers, pgvector, and pytest.
- `sorento_crm_mcp/` - Python MCP server that exposes CRM read tools over Streamable HTTP. It proxies FastAPI `/api/v1` GET endpoints and also registers custom tools such as user-guide tools.
- `sorento_crm_loadtest/` - k6 load/stress suite for backend, frontend, AI, and n8n/webhook scenarios.
- `sorento_crm/` - Docker Compose deployment wrapper for Postgres, backend, and frontend.
- `documentation/` - Product and architecture docs. `PRINCIPLES.md` (repo root) governs, and `documentation/reference/ADR-PRODUCT-STANDARDS.md` is binding for new/refactored product work. `ARCHITECTURE-RULES.md` was deleted; its layering rules live in `PRINCIPLES.md`.
- `scripts/` - Repo-level utility scripts, including user-guide route annotation and Outline sync.

Generated or dependency-heavy directories include `sorento_crm_frontend/.next/`, `sorento_crm_frontend/node_modules/`, `sorento_crm_frontend/playwright-report/`, `sorento_crm_frontend/test-results/`, `sorento_crm_backend/venv/`, `sorento_crm_backend/.venv/`, `.pytest_cache/`, and `__pycache__/`. Do not edit these by hand.

## Common Commands

### Backend (`sorento_crm_backend/`)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

alembic upgrade head
alembic revision --autogenerate -m "message"
alembic downgrade -1

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
python worker.py

pytest
pytest tests/test_rbac.py -q
pytest tests/test_rbac.py::test_name -q
```

Root `pyrightconfig.json` points Pyright at `sorento_crm_backend/venv`, Python 3.12, and `extraPaths: ["sorento_crm_backend"]`.

### Frontend (`sorento_crm_frontend/`)

```bash
npm install --force
npm run dev
npm run build
npm run build:staging
npm run lint
npm run test
npm run test:watch
npm run test:e2e
npm run format
npm run format:check

npx prisma db push
npx prisma generate
node prisma/seed.js
```

Vitest uses jsdom, globals, `vitest.setup.ts`, and `@/` mapped to the frontend root. Playwright specs live in `e2e/`, run Chromium only, use `PORTAL_E2E_BASE_URL ?? http://localhost:3000`, viewport `1400x1600`, one worker, no retries, and trace retention on failure.

### MCP Server (`sorento_crm_mcp/`)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export CRM_BASE_URL=http://localhost:8000
export EXTERNAL_API_KEY=...
export MCP_AUTH_TOKEN=...   # 32+ chars, same value as the backend's MCP_AUTH_TOKEN
python -m sorento_crm_mcp
pytest
pytest tests/test_catalog_compile.py -q
```

The package entry point is `sorento-crm-mcp = sorento_crm_mcp.__main__:main`. Default Streamable HTTP path is `/mcp`.

### Load Tests (`sorento_crm_loadtest/`)

```bash
npm run smoke
npm run load
npm run stress
npm run spike
npm run soak
npm run obs:up
npm run obs:down
```

These scripts call `./ci/run.sh`. The README says k6 is required and `.env` should be copied from `.env.example`.

### Docker Stack (`sorento_crm/`)

```bash
docker compose up -d
docker compose logs -f backend
docker compose exec backend alembic upgrade head
```

The compose file builds `./sorento_crm_backend` and `./sorento_crm_frontend`, starts Postgres 15, runs Alembic on backend startup, and serves the frontend through its Docker/Nginx setup.

## Architecture

### Auth Boundary

The frontend owns NextAuth routes under `/api/auth/*`. The backend validates JWTs created by NextAuth, so frontend `NEXTAUTH_SECRET` and backend `JWT_SECRET` must align when tokens are shared. Business logic lives in FastAPI under `/api/v1/*`.

Backend routes may also accept `X-API-Key` using `EXTERNAL_API_KEY`. The MCP README and backend README state that automated/API-key callers should set backend `EXTERNAL_API_KEY_ACT_AS_USER_ID` to a real `users.id` with required view permissions; otherwise the legacy `system` principal has no useful RBAC grants.

### Backend

Primary entry point: `sorento_crm_backend/app/main.py`.

Key backend structure:

- `app/api/v1/__init__.py` mounts domain routers.
- `app/models/` contains SQLAlchemy models.
- `app/schemas/` contains Pydantic schemas.
- `app/services/` contains domain business logic and integrations.
- `app/modules/` contains module runtime metadata/bootstrap and some self-contained module routes.
- `app/rbac/` contains permission registry logic.
- `app/scheduler/`, `app/jobs/`, `app/tasks/` support scheduled/background work.
- `alembic/` contains migrations.

Mounted API domains include auth, audit, master data, order management, inventory, procurement, incoming stock, marketing, forms, workflow forms, complaints, SLA, resources, user management, integrations, notifications, list query, lookup, activities, tickets, external, public, and system.

Module guards are applied in `app/api/v1/__init__.py` through `require_module_enabled` or `require_module_enabled_with_api_key` from `app.modules.runtime.guards`.

Backend startup registers audit listeners, embedding change listeners, background scheduler, MCP tool catalog sync, RBAC permission sync, IT support bootstrap, and selected scheduled task handlers.

Use `AppException` from `app.services.error_handler` for application errors. `app/main.py` serializes these through the global handler. Validation errors use a custom 422 handler.

### Frontend

Primary app tree: `sorento_crm_frontend/app/`.

Key frontend structure:

- `app/(auth)/` - auth, portal, public view, signup/reset/verification surfaces.
- `app/(protected)/` - authenticated CRM modules and pages.
- `app/api/auth/*` - NextAuth and auth-specific Next.js route handlers.
- `app/api/v1/*` - proxy routes and selected BFF routes for backend-facing API calls.
- `components/` and `app/components/` - shared UI and app-level components.
- `components/ui/` - ReUI/shadcn-style primitives including DataGrid pieces.
- `components/common/` - shared product components such as `ConfirmDeleteDialog`.
- `hooks/`, `providers/`, `lib/`, `services/`, `config/`, `modules/`, `types/` - shared client infrastructure.
- Feature folders often contain local `components/`, `hooks/`, `services/`, and `types/`.

The shared fetch wrapper is `lib/api.ts` (`apiFetch`). It maps legacy frontend API paths such as `/api/master-data/*` to backend `/api/v1/master-data/*`, while keeping explicit Next.js-only routes local.

Shared API utilities live in `lib/api-client.ts`:

- `extractApiError(response, fallback)`
- `buildDataGridParams(params, extra)`

### MCP

Primary entry point: `sorento_crm_mcp/sorento_crm_mcp/__main__.py`.

Key files:

- `server.py` creates the FastMCP server, compiles catalog tools, normalizes selected inputs, applies access checks, and registers custom user-guide tools.
- `catalog.py` defines in-scope CRM tool specs.
- `module_loader.py` merges module-provided catalog entries.
- `http_client.py` proxies CRM requests.
- `access_guard.py` enforces tool access behavior.
- `user_guides.py` registers Outline/user-guide tools.
- `settings.py` reads `CRM_BASE_URL`, `EXTERNAL_API_KEY`, and MCP host/port/timeouts.

Tools that proxy CRM HTTP endpoints are catalog-driven. External/custom tools are registered manually in server code.

### List Query / DataGrid

Backend list-query resources are registered in `app/services/list_query_registry.py` with SQLAlchemy model, permission slugs, serializers, and metadata.

Frontend DataGrid-backed list services should build query params via `buildDataGridParams`. Listings should use the shared DataGrid components with fixed/resizable table layout, explicit column sizes, and truncated long text with `title`.

## Code Conventions

### Plan First

`PRINCIPLES.md` governs and defines a **mandatory order** for every non-trivial feature: guided
journey → grill → UAC → plan → Phase 1 frontend mock → Phase 2 backend TDD → Phase 3 code review
→ Definition of Done gate. Skipping or reordering a step is a process violation; if a step cannot
be done, say so explicitly in the PR description rather than dropping it silently. Read
`PRINCIPLES.md` before starting.

In Claude Code, run this pipeline with `/feature` (`.claude/skills/feature/SKILL.md`). Agents
without that skill follow the same order manually.

For small changes, a brief checklist in the agent's working notes is enough. For larger changes,
identify the files/areas to inspect, expected implementation steps, and verification commands
before editing.

### Frontend Layering

Follow the layering documented in `PRINCIPLES.md` and `documentation/reference/ADR-PRODUCT-STANDARDS.md`:

```text
UI Components
  -> Hooks (useXxxQuery / useXxxMutations)
  -> Feature services
  -> Shared API client/apiFetch
  -> FastAPI backend
```

Do not duplicate shared primitives:

- Use `extractApiError` instead of hand-rolled response JSON parsing.
- Use `buildDataGridParams` instead of manually assembling DataGrid `page`, `limit`, `sort`, `dir`, and `query`.
- Use `services/userSelectService` for user select options.
- Use `ConfirmDeleteDialog` or `AlertDialog` for destructive confirmations.

### CRUD UX

Per `documentation/reference/ADR-PRODUCT-STANDARDS.md`:

- List pages use a DataGrid/table with search, filters, pagination, and an Add/Create toolbar action.
- Create and edit use modals by default.
- Dedicated create/edit pages are for complex, nested, multi-tab, or file-centric flows.
- View uses a dedicated detail page.
- Detail pages render all relevant sections even when empty; use explicit empty states instead of hiding sections.
- Delete is hard delete and always requires confirmation. If retention is needed, add a separate Archive action.

`.cursor/rules/delete-confirmation.mdc` also bans native `confirm()` for delete and requires count-aware bulk delete copy.

### UI Text and IDs

`.cursor/rules/development.mdc` says:

- Do not put feature explanations in the system UI; put them in the Outline user guides / FAQ.
- Do not show UUIDs in frontend UI. Resolve to human-readable identifiers.

### Backend Services

Backend services are class-based for many domains (`ProductService`, `OrderService`, `PromotionService`, `LookupSetService`, `AccessAgentService`, etc.). Keep business logic in services and keep route handlers thin where possible.

Before writing raw SQL, check model `__tablename__` values in `app/models/`. Model class names do not always match table names.

### Testing and Browser Verification

Backend and MCP tests use pytest. Frontend unit/component tests use Vitest. E2E tests use Playwright.

For frontend behavior changes, use `agent-browser` (headless) for browser verification: `npx -y agent-browser@0.27.0 <command>`, with `agent-browser skills get core --full` as the command reference. Playwright MCP is retired for verification. Start the frontend at `http://localhost:3000` and backend at `http://localhost:8000`, then navigate through the app UI rather than jumping straight to deep links so menu/module wiring is exercised too. Check the relevant UI flow, `console` output, and `network requests` before reporting completion.

For backend feature changes, add or update pytest coverage that verifies the new behavior. Prefer focused service tests for business rules and route tests for API contracts, permissions, validation, and serialization.

If Playwright/browser verification is unavailable, state that explicitly and report what was run instead. Do not claim full UI verification from Vitest alone.

## Preferred Patterns and Examples

- API error handling and DataGrid query params: `sorento_crm_frontend/lib/api-client.ts`.
- Feature service using `apiFetch`, `buildDataGridParams`, and `extractApiError`: `sorento_crm_frontend/app/(protected)/master-data-management/lookup-sets/services/lookupSetService.ts`.
- Shared delete confirmation: `sorento_crm_frontend/components/common/ConfirmDeleteDialog.tsx`.
- DataGrid list with explicit column sizes, truncation, modal create/edit, and delete dialog: `sorento_crm_frontend/app/(protected)/system-management/email-templates/components/EmailTemplatesList.tsx`.
- Backend application entry and global handlers: `sorento_crm_backend/app/main.py`.
- Backend route mounting and module guards: `sorento_crm_backend/app/api/v1/__init__.py`.
- Backend error contract: `sorento_crm_backend/app/services/error_handler.py`.
- List-query registry: `sorento_crm_backend/app/services/list_query_registry.py`.
- MCP server creation and tool registration: `sorento_crm_mcp/sorento_crm_mcp/server.py`.
- Load-test authoring rules: `sorento_crm_loadtest/README.md`.

## Risky Areas

- Auth and impersonation: `sorento_crm_frontend/lib/api.ts`, `sorento_crm_frontend/lib/impersonation-store.ts`, `sorento_crm_backend/app/dependencies.py`, NextAuth routes, and RBAC services/tests.
- Migrations and data model changes: `sorento_crm_backend/alembic/`, `sorento_crm_backend/app/models/`, `sorento_crm_frontend/prisma/schema.prisma`.
- Storage and attachments: `sorento_crm_backend/app/services/s3_service.py`, `r2_service.py`, `storage_router.py`, attachment models/routes, and frontend upload components.
- AI/MCP/tooling: `sorento_crm_backend/app/services/ai_assistant_service.py`, MCP catalog/server files, `mcp_tools` sync code, and user-guide sync scripts.
- Public/external portals and n8n flows: backend `external`/`public` routes, frontend `app/(auth)/portal`, and load-test `scenarios/n8n`.
- Generated/build output and caches: do not manually edit `.next`, `node_modules`, virtualenvs, pycache, pytest cache, Playwright reports/results, or load-test results.

## Environment Notes

Backend env is read from `sorento_crm_backend/.env` by `app/main.py` and settings code. Important variables include `DATABASE_URL`, `DIRECT_URL`, `JWT_SECRET`, `JWT_ALGORITHM`, `CORS_ORIGINS`, `REDIS_URL`, `EXTERNAL_API_KEY`, `EXTERNAL_API_KEY_ACT_AS_USER_ID`, storage variables, `RESPOND_*`, and AI/provider settings used by assistant code.

Frontend env uses `.env` / `.env.local`. Important variables include `DATABASE_URL`, `NEXTAUTH_SECRET`, `NEXTAUTH_URL`, `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_BASE_PATH`, `GOOGLE_CLIENT_*`, `EXTERNAL_API_KEY`, SMTP/storage/recaptcha settings, and `FRONTEND_BASE_URL`.

MCP env requires `CRM_BASE_URL` and `EXTERNAL_API_KEY`; optional settings include host, port, timeout, max response size, and log level.

Never commit real secrets.

## Agent Skills

Shared configuration so any agent resolves the same tracker, labels and domain docs. Full detail
in `documentation/agents/`.

### Delivery pipeline

Non-trivial feature work follows the mandatory order in `PRINCIPLES.md`. Claude Code runs it via
`/feature`; the skill map at the bottom of `.claude/skills/feature/SKILL.md` names which skill
belongs at each step. Two rules override the `mattpocock-skills` plugin:

1. **Files are the contract, tickets are the queue.** The UAC and PLAN files under
   `documentation/plans/<domain>/` are the source of truth. `to-spec` writes there - it does not
   publish a spec issue. An issue that contradicts the UAC loses.
2. **Frontend mock before any backend code.** `implement` has no concept of Phase 1, so scope it
   to Phase 2 only. `prototype` output is throwaway and must never become the shipped frontend.

### Issue tracker

GitHub Issues on `jayson-odoo/sorento-crm`, via the `gh` CLI. See
`documentation/agents/issue-tracker.md`.

### Triage labels

`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` - all five exist on
the repo. See `documentation/agents/triage-labels.md`.

### Domain docs

Multi-context. `CONTEXT-MAP.md` at the root indexes two glossaries: root `CONTEXT.md` (Dealer
Sales Kit, Authoring, Products and selling, Space and design, After-sales, Supply and purchasing)
and `documentation/CONTEXT.md` (Project Sales, Company vs Tenant, Core vs Module). ADRs live in
`documentation/adr/`. Use the glossary's vocabulary in output; flag ADR conflicts rather than
silently overriding them. See `documentation/agents/domain.md`.

Note this repo uses `documentation/`, not `docs/`. Anything referring to `docs/adr/` means
`documentation/adr/`.

## Running several worktrees at once

Every lane runs its own frontend and backend on its own ports, all on `localhost`, and
that is where the sharp edges are.

- **NextAuth cookies are scoped by HOST, not by port**, so one lane's session cookie
  collides with another's. `NEXTAUTH_COOKIE_SUFFIX` in `.env.local` is what keeps them
  apart (see the suffixed-cookie entry in `CLAUDE.md` for the `getToken` half of this).
- **Browser verification therefore needs its own browser session per lane.** Run
  `agent-browser --session <lane-name> ...`; the shared default session carries whatever
  cookies another lane left behind, and the symptom is a login that POSTs
  `/api/auth/callback/credentials`, returns 200, and lands back on `/signin` with no
  session token - which reads as wrong credentials rather than as a cookie collision.
- **Check the ports before booting anything** (`lsof -i :PORT -sTCP:LISTEN`) and never
  kill a process another lane owns.

## Alembic across concurrent branches

Two branches cut from the same head each pass review with one head and produce two after
both merge, which fails `scripts/bootstrap_env` at `command.stamp(cfg, "head")` and takes
out the whole CI run before any test executes. Re-check `alembic heads` immediately
before opening a PR, and fix a fork with a merge revision, never by editing a landed one.
Revision ids must stay <= 32 characters. Worked example and the full reasoning:
`sorento_crm_backend/alembic/versions/360_merge_container_status_and_spec.py`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

## Assumptions

- React 19 peer dependency conflicts are expected locally because the checked-in instructions and package state use `npm install --force`.
- The frontend Prisma schema is for NextAuth/user/session-adjacent data, while business CRM data should be served through FastAPI. This is supported by README language and API architecture, but some legacy Next.js API routes still exist.
- Browser verification may require working local env, seeded users, and permissions that are not guaranteed by this repository alone.
