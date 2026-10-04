# UAC: CRM sign-in always uses the 30-day sliding session (SIGNIN-ALWAYS-SLIDE)

Plan: `PLAN-signin-always-slide-30sep.md`. Owner, 30 Sep 2026: "sign in, be it phone or email, needs
the same mechanism as portal; I don't want the user to tick remember me; when there is activity on
continuous days the session just continues".

## Journey

Actor: any staff user (email/password or phone). They arrive at `/signin`. The page is the same as
before, minus the "Remember me" checkbox, so there is no decision to make about it. They sign in
by email + password or by phone code. From then on, using the CRM pushes their session end out to 30 days
from that moment, at most once a day (`user_session_service.py:116-118`), so a person who works on consecutive days never sees the
sign-in page again. They are signed out only by 29 to 30 days without activity, by logging out, by
"sign out other devices", by a password reset/change, or by an admin force logout / deactivation.
No one else is notified; nothing else changes.

## Acceptance criteria

| AC | Given / When / Then | Test |
| --- | --- | --- |
| AC-1 [BE] | Given a staff user, when they sign in by email with no `remember_me` or with `remember_me=false`, then a session is minted with `rolling=true` and about 30 days left. | `sorento_crm_backend/tests/test_auth_login.py` `test_regression_login_mints_30d_session_that_slides_after_a_day` |
| AC-2 [BE] | Given that session with under 29 days left (a day or more has passed), when the user makes any request, then `expires_at` moves forward to about now + 30 days. Repeated daily activity keeps it alive. | same test; `tests/test_user_session_service.py` `test_activity_on_consecutive_days_keeps_session_alive` |
| AC-3 [BE] | Given any `/auth/login` payload (`remember_me` missing, false, null or true), then no session shorter than 30 days is minted; the 8h path no longer exists. | `tests/test_auth_login.py` `test_regression_login_never_mints_a_session_shorter_than_30d`; `tests/test_user_session_service.py` `test_mint_takes_no_remember_choice` |
| AC-4 [BE] | Given phone OTP sign-in, then it still mints the 30-day sliding session and slides on activity. | `tests/test_identity_s1_phone_signin.py` `test_regression_phone_otp_session_is_30d_and_slides_after_a_day`, `test_ac24_verify_success_returns_login_shape_and_mints_session` |
| AC-5 [FE] [E2E] | Given the sign-in page, in email mode and in phone mode, at 1280px and 375px, then there is no "Remember me" checkbox, and the email submit sends no `rememberMe`. | `sorento_crm_frontend/app/(auth)/signin/page.test.tsx` (email/phone mode tests, payload test, AC-29 control count); agent-browser evidence run in the plan |
| AC-6 [BE] | Revocation still applies: logout, device revoke, revoke-others, password reset/change and admin force logout / deactivation end the session on the next request. | `tests/test_auth_login.py` `test_email_session_revoke_still_ends_it`; existing `tests/test_user_session_service.py` revoke tests, `tests/test_identity_s1_password.py`, `tests/test_identity_s1_security_round.py` |

No-motion list: this lane adds no motion; removing the checkbox changes no animation.

Out of scope: an absolute session cap (owner: pure sliding, exactly like the portal); migrating
existing `rolling=false` rows (they keep their fixed 8h and lapse).
