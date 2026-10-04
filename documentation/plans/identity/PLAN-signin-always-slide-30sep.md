# PLAN: CRM sign-in always uses the 30-day sliding session (SIGNIN-ALWAYS-SLIDE)

Status: in review, DoD gate passed pending CI (Track: full). PR #1404. Plan created: 2026-09-30.

Track note: first filed as small fix (under 300 lines, no migration); re-tracked to FULL on the
30 Sep process audit because the diff changes staff auth/session lifetime and NextAuth, which is on
the small-fix exclusion list (`PRINCIPLES.md` "Small fix track": no auth change). Full-track
steps run for this lane: UAC file (`signin-always-slide-30sep-acceptance-criteria.md`), red-first
regression tests with kill test, `reviewer` + `security-reviewer` in parallel, agent-browser pass at
1280 and 375, DoD gate. Journey/grill: the owner's instruction below is the journey; no open
questions to grill (owner ruling is explicit and matches the archived staff-sessions plan).

## Journey

Owner, 30 Sep 2026: "sign in, be it phone or email, needs the same mechanism as portal; I don't want
the user to tick remember me; when there is activity on continuous days the session just continues".

## Grill (feature skill step 2, run late on the 30 Sep process audit)

The code was written before this grill, so every question below is asked against a change that
already implements the recommendation. Sent to the owner as one `crew-ask` on PR #1404
(30 Sep 2026). Owner answered the same day: all as recommended.

Premises re-checked against the code on 30 Sep, after the owner's "get your facts right" ruling.
Every premise below cites file:line (origin/main = b8cdbebe4 for code this lane removed).
**Corrected** marks a premise the first crew-ask stated wrongly. None of the corrections changes a
decision: each wrong premise was an argument for the recommendation, not the thing decided.

| # | Decision | Verified premise (file:line) | Decision (owner, 30 Sep) |
| --- | --- | --- | --- |
| G1 | Absolute cap | The portal slide re-extends with no cap check: `portal_service.py:423-424`. Staff slide has none either: `user_session_service.py:116-118`. The archived plan rules "no absolute cap": `_archive/PLAN-staff-rolling-sessions-fastapi-auth.md:31` (Q11). | (a) no cap |
| G2 | Idle window | **Corrected.** The first ask said "30 days with no request". Actual rule: a request re-extends to now+30d only once under 29d remain (`user_session_service.py:25-26,116-118`). The expiry therefore lands 29 to 30 days after the last request, depending on when in the day the last slide happened. The NextAuth cookie also slides: every `/api/auth/session` read re-issues it with now+30d (`node_modules/next-auth/core/routes/session.js:61-80`, next-auth 4.24.13; `SessionProvider` mounted at `providers/auth-provider.tsx:17`; `maxAge` 30d at `auth-options.ts:181`). So there is no hidden 30-days-from-sign-in cutoff on the cookie side. | (a) keep, same as portal (`portal_service.py:61-62`) |
| G3 | Existing 8h rows | Old code minted `rolling=False, now+8h` when the box was unticked; unticked was the default (origin/main `signin/page.tsx:92`). `resolve_session` only slides `rolling` rows (`user_session_service.py:116`), and expired rows get 401 `session_expired` (`:111-112`). So every such row ends within 8h of its sign-in. | (a) let lapse, no migration |
| G4 | Legacy `remember_me` | **Corrected.** The first ask said a cached old browser bundle would fail without it. Wrong: the browser never posts to FastAPI login. It posts credentials to NextAuth, whose server-side `authorize` builds the FastAPI body (`auth-options.ts:64-90`); the new server sends no `remember_me`. Direct callers of `/api/v1/auth/login` are the NextAuth server (an old one only during a rolling deploy) and `scripts/chatbot_journey.py:105`. Also, `LoginRequest` has no `extra` config (`app/schemas/auth.py:5-10`), so Pydantic ignores unknown fields anyway. Rejecting with 422 would need an explicit `extra="forbid"`. Keeping the field is documentation of the old contract, not what keeps callers working. | (a) accept and ignore (the kept field is harmless and documents it) |
| G5 | Shared devices | Logout revokes server-side: `topbar/user-dropdown-menu.tsx:285` calls `revokeCurrentSession` (`lib/api.ts:177-183`), which calls `POST /auth/logout` (`auth.py:462-467`). Sign out other devices: `auth.py:509-516`. Admin force logout: `user_management/users.py:298-303`. | (a) accept; the trigger for an idle timeout is a real shared-device incident |
| G6 | Impersonation expiry | Impersonation needs admin/superadmin (`dependencies.py:195-197`) and an `ImpersonationSession` row with `ended_at IS NULL` (`dependencies.py:199-206`); the model has no expiry column (`models/impersonation.py:17-26`). **Refined:** only admins who signed in by EMAIL with the box unticked were capped at 8h. Phone-signin admins were already on 30d sliding (origin/main `phone_signin_service.py:440`), so the gap existed before this lane for them. | (a) follow-up BL-068 |
| G7 | Tell users | "No feature explanations inside the UI itself": `CLAUDE.md:172`. | (a) one line in `user-guides/_shared/getting-started-for-new-users.md` section 1 |
| G8 | Phone and portal | Phone already minted 30d sliding (origin/main `phone_signin_service.py:440`). **Corrected:** "the portal slides 30 days" holds only for OTP-verified portal tokens (`portal_service.py:58-62,423-424`). A portal link token is 7 days, fixed (`portal_service.py:57,278`). Neither portal path is touched by this lane. | no change |

## TDD note

Order was code first, tests second, on the small-fix track. Rules allow that track to write tests
and fix in one pass, but still require reds shown red. Once re-tracked to FULL, each behaviour was
proven with a kill test instead: break the implementing line, show the named test go red, restore.
Results are in the PR's DoD gate comment and in the table below.

| Behaviour | Mutation | Tests that went red |
| --- | --- | --- |
| B1 email login mints sliding | `mint_session` `rolling=False` | 2 (login slide cases) |
| B1 email login mints 30d | `expires_at = now + 8h` | 6 (all login regression cases) |
| B2 slide on activity | slide condition `if False:` | 5 (login x2, phone, consecutive days, service slide) |
| B3 legacy `remember_me` accepted | schema `remember_me: None` | 3 (false/true variants 422) |
| B4 no 8h path | `SHORT_TTL` re-added | 5 |
| B5 phone OTP 30d sliding | phone mint then `rolling=False` | 2 (phone regression, ac24) |
| B6 revocation still checked | revoked check `if False:` | 1 (logout then 401) |
| B7 no checkbox (FE) | checkbox re-inserted in the email form | 2 vitest |
| B8 no `rememberMe` in payload (FE) | `rememberMe: false` re-added | 1 vitest |

## Facts (b8cdbebe4)

- Sessions already slide: `user_session_service.resolve_session` re-extends a `rolling` row to now+30d
  once under 29 days remain.
- Phone OTP already minted `remember=True`; only email login honoured the checkbox (`remember_me`,
  default False, so an unticked box gave a fixed 8h session).
- Portal rule is the same pure slide with no absolute cap
  (`_archive/PLAN-staff-rolling-sessions-fastapi-auth.md:31`).

## Change

1. `mint_session` loses its `remember` argument and `SHORT_TTL`: every row is `rolling=True`,
   `expires_at = now + 30d`.
2. `LoginRequest.remember_me` is accepted (an older client still signs in) and ignored.
3. Sign-in page, `signin-schema.ts` and the NextAuth credentials provider drop `rememberMe`.
4. Unchanged: per-request revocation check, device list + revoke endpoints, password reset/change
   revoking all sessions, admin force logout and deactivation. Existing `rolling=False` rows keep
   their fixed 8h and lapse on their own; the column stays (no migration).

## Acceptance

See `signin-always-slide-30sep-acceptance-criteria.md` (AC-1 to AC-6, with the test for each).

## Evidence run (agent-browser, 30 Sep 2026)

Stack: this branch in the cloud sandbox. Backend `uvicorn` on :8000 against a throwaway
`sorento_ci` DB (bootstrap_env), frontend `npm run dev` on :3000. Tool: `agent-browser@0.27.0`
with named sessions `slide` (signed in) and `slideout` (signed out). No shared DB touched.

1. `open http://localhost:3000/` at 1280x800 while signed out. The sign-in form renders with
   Email, Password, Forgot Password?, Continue and the phone button. DOM check:
   `{checkboxes: 0, rememberText: false, overflowX: false}`.
2. Fill `zzt.slide@example.com` / password, then click Continue. `POST /api/v1/auth/login` returns 200 and
   the app shell (Dashboards) loads. DB: `auth_method=password, rolling=t,
   remaining=29 days 23:59:43`.
3. Simulate a day: `expires_at -= 1 day 1 hour` gives `remaining=28 days 22:59:38`. Click the
   sidebar "Dashboards" link. DB: `remaining=29 days 23:59:53`, so the session slid forward on
   activity.
4. Signed-out session at 1280x800 and at 375x812: open `/` (redirects to
   `/signin?callbackUrl=%2F`). Email mode, then click "Phone number". Phone mode shows the country
   select, the Phone number input, Continue and Back to email. The DOM check in all four
   states (email/phone x 1280/375) is `{checkboxes: 0, rememberText: false, overflowX: false}`.
   The 375 screenshot shows the card unclipped.
5. Screenshots committed (repo cap: two per lane, each under 200 KB):
   `evidence/signin-always-slide-email-1280.png` (email mode, 1280) and
   `evidence/signin-always-slide-phone-375.png` (phone mode, 375). The other two states are
   covered by the DOM check in step 4.

Stack note: this ran on the branch's own stack in the cloud sandbox, not on the crew test copy.
The worker cannot reach the owner's machine; the owner's hand test on the crew copy passed.

Phone OTP end-to-end is not walked in the browser. It needs a Respond.io send, which the sandbox
must not call. It is covered by `test_regression_phone_otp_session_is_30d_and_slides_after_a_day`
and was part of the owner's hand test (PASS).
