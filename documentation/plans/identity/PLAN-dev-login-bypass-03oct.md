# PLAN: Dev auto-login for local test copies (DEV-LOGIN-BYPASS)

Status: Built; reviewer and security review passed after two fix rounds (PR #1449), awaiting CI and hand test. Track: full (auth change, migration,
security review mandatory).

Owner ask (3 Oct 2026): "a dev mode to bypass all these logins for hand testing, to boost our
productivity". Crew runs local test copies (FE `http://<lane>.localhost:31xx`, BE `:81xx`,
NextAuth v4, per-lane cookie suffix). Signing in on every copy is slow, and the automated tester
refuses to type credentials into sign-in forms.

## Crew answers (3 Oct 2026, all recommendations)

1. Session `auth_method` = `dev_login`, one additive migration widening
   `ck_user_sessions_auth_method` (`dev_login_0001`).
2. Frontend guard `NODE_ENV !== 'production'`: copies run `next dev`.
3. Users = env allowlist `DEV_AUTO_LOGIN_USERS` of existing emails (first = default; e.g.
   an admin, then `test.ideas.viewer@example.com` as the view-only user).
4. Auto sign-in once per browser tab; after sign-out the picker shows.

## Behaviour

Visiting a test copy with the flag on lands you signed in as the default dev user. The sign-in
page shows a "Dev sign-in" section: a `SearchableSelect` of the allowlisted users (name + role,
never an id) and "Sign in as user". After a sign-out in the same tab the picker shows instead of
auto-signing you straight back in.

## Design (as built)

- **Backend** `POST /api/v1/auth/dev-login {email}` mints the SAME `user_sessions` row as
  `/auth/login` (`mint_session`, `build_login_response`) with `auth_method = dev_login`;
  `GET /api/v1/auth/dev-login/users` lists the signable allowlist. Both answer a plain 404
  unless every guard holds. Guards: `app/services/dev_login.py`; routes: `app/api/v1/dev_login.py`.
- **Frontend** a `dev-login` NextAuth `CredentialsProvider` (registered only when configured)
  whose `authorize()` takes only an email, re-checks the frontend guards and calls the backend
  server-side with the shared secret. `GET /api/auth/dev-login/users` (Next route) relays the
  picker list. The picker is `app/(auth)/signin/components/dev-login-picker.tsx`, fetched
  client-side through `services/devLoginService.ts` and `useDevLoginUsers`.

## Guards (all must hold; fail closed)

Backend, per request:
1. `DEV_AUTO_LOGIN=true`.
2. `ENVIRONMENT` in {development, dev, local, test}.
3. `X-Dev-Login-Secret` equals `DEV_AUTO_LOGIN_SECRET` (16+ chars, constant-time compare).
   Only the frontend SERVER has it. This closes security review B1: the `next dev` rewrite of
   `/api/v1` relays a browser call with Host rewritten to the backend's localhost address from a
   127.0.0.1 peer, so Host and peer checks alone pass for it.
4. No `X-Forwarded-Host` (the rewrite always adds one; the server-side fetch never does).
5. `Host` hostname is `localhost`, `*.localhost` or `127.0.0.1`.
6. TCP peer is loopback.
7. Email allowlisted, user ACTIVE and not trashed.

Backend, at import (crash, `app/main.py`) when the flag is on and any of: `ENVIRONMENT` not set
explicitly (the settings default `development` does not count), not in the list above, secret
missing or short, running under gunicorn (the production entrypoint), running in a container.
A loud WARNING is logged when it is legitimately active.

Frontend:
1. `DEV_AUTO_LOGIN=true` and `DEV_AUTO_LOGIN_SECRET` set (server-only, never `NEXT_PUBLIC_*`).
2. `NODE_ENV !== 'production'`.
3. The dev server is bound to 127.0.0.1 only (`next dev -H 127.0.0.1`), read from Next's
   `__NEXT_PRIVATE_ORIGIN`. This closes security review B2: the Host header is caller-controlled,
   so a LAN / Tailscale peer could otherwise send `Host: localhost` through NextAuth's own
   callback. Next reports `localhost` when bound to every interface, so only `127.0.0.1` counts.
   Consequence: a copy with dev login on cannot also be reached over Tailscale / LAN. Start it
   with exactly `-H 127.0.0.1`: `-H localhost` reads the same as an all-interfaces bind and is
   refused. The bind check assumes no tunnel in front of the dev server (`tailscale serve`,
   `ssh -L`, a local reverse proxy); never put one in front of a dev-login copy.
4. The browser Host is `localhost`, `*.localhost` or `127.0.0.1` (stops DNS rebinding).
A loud console warning is logged when the provider is registered.

Deploy guard: `tests/test_dev_login.py::test_deploy_files_never_mention_the_flag` scans
workflows, compose files, Dockerfiles, deploy scripts, `start.sh`, `run.sh`, `gunicorn.conf.py`
and every committed `.env*` file. The server-only production compose is outside the repo; the
gunicorn / container startup refusal covers it.

## How crew enables it (test copies only)

Backend env_overrides: `DEV_AUTO_LOGIN=true`, `ENVIRONMENT=development`,
`DEV_AUTO_LOGIN_USERS=<admin email>,test.ideas.viewer@example.com`,
`DEV_AUTO_LOGIN_SECRET=<random 32+ chars per copy>`. Run with uvicorn (not gunicorn).
Frontend env_overrides: `DEV_AUTO_LOGIN=true`, the SAME `DEV_AUTO_LOGIN_SECRET`; start with
`next dev ... -H 127.0.0.1`. Shared dev DB needs the `crew-migration` SQL from the PR.

## Tests

BE `tests/test_dev_login.py` (pytest, every guard has a kill test, including the relayed-proxy
request shape and the startup refusals in a hermetic child process). FE `lib/dev-login.test.ts`,
`app/api/auth/[...nextauth]/auth-options.dev-login.test.ts`,
`app/api/auth/dev-login/users/route.test.ts`, `app/(auth)/signin/components/dev-login-picker.test.tsx`.
