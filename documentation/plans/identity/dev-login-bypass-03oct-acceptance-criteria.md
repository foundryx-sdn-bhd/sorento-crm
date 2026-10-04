# UAC: Dev auto-login for local test copies (DEV-LOGIN-BYPASS)

Plan: `PLAN-dev-login-bypass-03oct.md`.

- AC-01 Off by default: with `DEV_AUTO_LOGIN` unset, `POST /api/v1/auth/dev-login` and
  `GET /api/v1/auth/dev-login/users` answer 404, and the sign-in page shows no picker.
- AC-02 Environment guard: with the flag on, any `ENVIRONMENT` outside development / dev /
  local / test (production, prod, staging, empty, anything else) answers 404.
- AC-03 Host guard: a request whose `Host` hostname is not `localhost`, `*.localhost` or
  `127.0.0.1` answers 404 (e.g. `sorento.example.com`, `localhost.evil.com`).
- AC-04 Peer guard: a request whose client IP is not loopback answers 404.
- AC-04b Server-only secret: a request without `X-Dev-Login-Secret` equal to
  `DEV_AUTO_LOGIN_SECRET`, or carrying `X-Forwarded-Host`, answers 404. This is the shape of a
  browser call relayed by the `next dev` `/api/v1` rewrite (Host localhost, loopback peer).
- AC-05 Allowlist: only an ACTIVE, non-trashed user whose email is in `DEV_AUTO_LOGIN_USERS`
  can be signed in; any other email answers 404. The users list returns only those users
  (name, email, role name), first entry is the default.
- AC-06 Same session: a dev sign-in returns the same body `/auth/login` returns, backed by a
  `user_sessions` row (`auth_method = dev_login`, 30-day sliding) that `get_current_user`
  resolves on a normal `/api/v1/*` call.
- AC-07 Startup crash: importing the app with `DEV_AUTO_LOGIN` on exits non-zero when
  `ENVIRONMENT` is outside the allowlist or not set explicitly, the secret is missing or short,
  or it runs under gunicorn or in a container; with it on in a local dev run it logs a loud WARNING.
- AC-08 Deploy guard: no committed compose file, workflow or production/staging env file sets
  `DEV_AUTO_LOGIN`; a test fails if one does.
- AC-09 FE guard: the `dev-login` NextAuth provider refuses unless server-only
  `DEV_AUTO_LOGIN=true` and `DEV_AUTO_LOGIN_SECRET`, `NODE_ENV !== 'production'`, the dev
  server is bound to 127.0.0.1 only, and the browser Host is localhost-ish.
  No password or secret reaches the browser.
- AC-10 Sign-in page: with the FE guard on, the page signs in as the default dev user once per
  tab, and offers a "Sign in as" picker to switch; after sign-out the picker shows instead of
  auto-signing back in.
