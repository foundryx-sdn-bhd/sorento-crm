# PLAN: Unified identity, one login for the portal and the CRM (#1280)

Status: draft plan + UAC, round 4 (27 Sep 2026): owner rulings on Q1, Q2, Q3, Q4, Q6, Q8, Q9, Q10
(26 Sep 2026 23:45 MYT), Q5 (27 Sep 2026 00:20 MYT), the screen notes of 27 Sep 2026 00:45 MYT
(Q7 ruled, Q16) and the invite email ruling of 27 Sep 2026 00:50 MYT (Q17) applied (section 12).
Owner alignment page: `alignment-unified-identity-27sep.html` (same folder). Q3 and Q4
reversed the recommendation: no user is ever created automatically, the owner creates and sets up
every user at the backend (section 6). Round 4: no new pages (the Salesperson accounts worklist
and the separate admin identity slice S4 are withdrawn; creating and linking live in the existing
Administrative Users and Internal Users pages), one sign-in page (the existing `/signin`) with an
Email / Phone toggle, and no email is ever sent by creating or linking a user (section 6.4).
Track: full (migration, auth, RBAC, portal ingest). S0 built on PR #1303 (27 Sep 2026, owner ruling
of 27 Sep 01:04 MYT to build without further alignment); the orchestrator's review at 03d3b474
(needs work) is addressed by fix lane round 2 on the same PR, which records rulings 4, 10, 12 and
13 in the PR body. The plan rides in that PR. Main 52b0ac24 merged into it (27 Sep 2026):
`identity_0001_s0_model` now chains on main's single head `sales_s1_reports_module` instead of
merging 527 and spec_0003 itself. Main af86bcfd merged into it (27 Sep 2026, round 2): it now
chains on main's single head `sales_0005_commission_tiers`. S0 merged to main (#1303). S1 built on
PR #1307 (branch `claude/identity-s1-phone-signin-w6akca`, full track); the reviewer pass at 56daafae
(needs work) is addressed by S1 fix lane round 2 on the same PR; main 9d150067 merged into it and the
PR retargeted to main (28 Sep 2026, retarget round), with the owner ruling of 27 Sep applied: sign-in
reuses the approved `portal_otp` WhatsApp template; main cd220251 merged in (28 Sep 2026, merge-main
round, clean merge, single alembic head `fin_0001_billing_documents`); main 2e894c4c merged in
(28 Sep 2026, merge round 4, single alembic head `merge_28sep_esc_fin`); S1 fix round 2 (29 Sep
2026, owner rulings): the shared `PhoneInput` on the sign-in phone step, and the OTP kept readable
in the Respond outbox (B1 overruled). S2 not started.
S3 built on PR #1306 (stacked on S0; no migration); the reviewer pass at 6a7f0bcd (needs work:
B1, S1 to S5, N1 to N6) is addressed by S3 fix lane round 2 on the same PR (27 Sep 2026), ready
for the orchestrator's review; the retarget round (28 Sep 2026) merged main 9d150067 into it and
moved its base to main (single alembic head `identity_0001_s0_model`, S3 adds no migration;
backend, migration, serial_ddl, SCM and touched vitest green on the merged tree); the merge-main
round (28 Sep 2026) merged main cd220251 (#1318, #1325, #1278) cleanly, single head
`fin_0001_billing_documents`, the same suites green again. S1 and S2 per their own lanes.
UAC: `identity-unified-login-acceptance-criteria.md` (same folder; the Journey is there, and every
AC traces to a step in it).
Classification: CORE (auth and users are base-platform), tables stay in `public`.
Companion: #1281 (audit trail analysis) relies on the actor contract in section 8.

All line refs are `origin/main` at 51d30ccc5. Paths are relative to `sorento_crm_backend/` (BE)
and `sorento_crm_frontend/` (FE) unless given in full. No database was reachable from the
planning session, so every row count this plan needs is named as an S0 pre-flight query rather
than stated as a fact.

## Contents

0. Owner words
1. Why
2. The decision in one paragraph
3. What exists today (measured)
4. Design: one principal, several ways in
5. Sign-in and recovery flows
6. Counterpart users: the owner creates and sets up every user
7. Permissions each kind of user gets
8. The audit actor contract (for #1281)
9. Migration path: zero downtime, no re-registration
10. Slices, each with its definition of done and UAC ids
11. Risks, out of scope, named triggers
12. Grill questions for the owner

## 0. Owner words

26 Sep 2026 ~13:50Z, issue #1280, verbatim and binding:

> "I think eventually all our portal and user sign in needs to be unified. Because the one who use
> the portal for basic portal submission function will grow to use our project sales modules, use
> our sales opportunities module. So I think we need to find a way to unify. I'm thinking that we
> should support like user login, user email and password login, or also a phone number login. We
> should unify this. So for all the contacts that are like for product salesperson, retail
> salesperson, we also need to create like a counterpart user for them so in the future all of
> them can use our system and be audit trailed by our system."

Read as four requirements: (R1) one sign-in for portal and CRM; (R2) email + password OR phone
number; (R3) a counterpart user for every product and retail salesperson contact; (R4) everything
those people do is audit-trailed as them.

Owner ruling 26 Sep 2026 23:45 MYT (Q3, Q4), verbatim: "i should be able to consciously create and
setup at the backend first, it should be controlled by me" and "no it shouldn't, it should be
consciously by me". So R3 is met by the owner, not by the system: every salesperson contact CAN
have a counterpart user, the owner creates each one deliberately from the backend, and the system
shows which salesperson contacts still have none (section 6). Nothing creates a user by itself:
no backfill, no sync hook, no first sign-in.

This reverses a recorded ruling: `app/models/sales_agent.py:7-10` and `:206-209` carry the
captain's 14 Aug 2026 ruling that salespeople "shouldn't have user account in our system, or
optional at least", repeated in `documentation/plans/_archive/sales/PLAN-customer-sales-agent-assignment-24sep.md:15`.
Owner ruling 26 Sep 2026 23:45 MYT (Q2): "yeah correct", #1280 supersedes it; S3 corrects the
docstring and that plan.

Owner notes on the alignment page, 27 Sep 2026 00:45 MYT (PR #1285), verbatim and binding:

> On the Salesperson accounts worklist: "just use our existing user management page, don't need
> new page" and "there is no need for this, I will just create the user account and link to the
> contact, i don't even want to send email verification to these ppl at this stage, you can maybe
> help me to quick create from respond contact la, but i don't need another page called
> salesperson account, just need existing Administrative Users and Internal contacts".
> On the S3 / S4 card: "i don't need a brand new page for user management".
> On the one-field sign-in sketch: "i just need 1 sign in page which is
> https://fe-sorento.foundryx.my/signin with option to sign in by phone number, this is for normal
> user, then for salesperson, they will still access via portal which will prompt them to enter
> verification code when needed" and "we already got this send otp mechanism so just reuse it,
> make sure i can toggle between phone auth and email pw auth in the sign in page while obeying
> the design element of the current sign in page".

Owner ruling 27 Sep 2026 00:50 MYT (PR #1285), verbatim and binding:

> "cause i think now if we create the user with email, it will send email verification right? can
> this be a manual step ah cause i worry will accidentally send the email to the salespersons when
> we create for them, this one you also need to update in the lavish"

"Internal contacts" is the page the sidebar calls **Internal Users** (Users & Access > People,
the WhatsApp contact list, `config/menu.config.tsx:684-688`; the contact list page titles itself
the same, `app/(protected)/user-management/contacts/page.tsx:15`). This plan uses the sidebar name.

## 1. Why

Today there are two identity worlds that never meet (section 3):

- **CRM staff** are `users` rows, signed in by email + password, holding an opaque
  `user_sessions` token behind NextAuth.
- **Portal visitors** are WhatsApp contacts (`respond_contacts`), holding an opaque
  `portal_tokens` token after a WhatsApp code, with no user row at all.

The owner's point is that these are the same people on a timeline: a salesperson who submits a
price tag request on the portal today will work project sales opportunities in the CRM tomorrow.
With two worlds that person needs a second account, a second sign-in, and their history is split
across a contact id and a user id. The audit trail (#1281) cannot say "Aisyah did this" across
both, because the audit log has no actor kind and three different id namespaces for actors.

## 2. The decision in one paragraph

**The person is the existing `users` row; nothing new is introduced as a "principal".** A user
may carry an email (with a password) and/or a phone (verified by a WhatsApp code), and links to
at most one WhatsApp contact through the column that already exists for it
(`users.respond_contact_id`), made unique. Both ways in produce the one session kind that already
exists (`user_sessions`, behind NextAuth). The portal stops holding its own token and derives its
(contact, space) from the signed-in user's linked contact, so every ownership rule, form grant
and company scope the portal has today keeps working unchanged. **Users are created only by the
owner (or an admin) at the backend**: create the user, link it to the contact, set its roles, then
the person signs in. A portal contact the owner has not set up keeps using the portal exactly as
today (its own portal token), so nobody is locked out and nobody is enrolled silently. The
audit log gains an actor kind plus the few fields that say how and on whose behalf. No new
identity table, no new OTP table, no new session table.

Why not a separate `principals` or `identities` table with users and contacts hanging off it:
`users` already has email, password, phone (unique), status, roles, company grants, sessions and
a contact link. A new table would duplicate each of those and migrate every `*_by` FK for no
behaviour the owner asked for. The trigger that would justify it is named in section 11.

## 3. What exists today (measured)

### 3.1 CRM users, roles, permissions, company scope

- **One `users` table, owned by the backend** (`app/models/user.py:18-123`). `email` String,
  unique, NOT NULL (:23); `password` nullable bcrypt (:24); `contact_number` (:29) with
  `uq_users_contact_number` (:121-122, "One phone == one user"), stored as E.164 digits without
  `+` by `normalize_msisdn` (`app/services/phone_utils.py:22`, Malaysia default); `status`
  String INACTIVE / ACTIVE / BLOCKED (:36); `email_verified_at`, `last_sign_in_at`, `is_trashed`,
  `is_protected`, `is_integration` (machine principal, never a password); `respond_user_id`
  (the Respond.io agent id); `last_active_company_id`; `respond_contact_id` FK to
  `respond_contacts` ON DELETE SET NULL (:61-63), **not unique**.
- **Prisma is gone.** There is no `sorento_crm_frontend/prisma/`; CLAUDE.md's Prisma lines are
  stale (a docs fix riding in S0).
- **Roles:** `user_roles` (slug, `is_default`, `is_protected`), `user_role_assignments` (many
  roles per user), `user_permissions`, `user_role_permissions` (`app/models/user.py:172-237`).
  `superadmin` and `admin` pass every check (`app/services/user_service.py:1138,1173,1206`),
  cached 30s. `require_permission` and variants at `app/dependencies.py:313-488`. Signup, create
  and invite assign the `is_default` role when none is given.
- **Module guard:** `require_module_enabled_with_api_key` (`app/modules/runtime/guards.py:62`)
  needs a staff session or an API key; `/public` (portal) and `/auth` are mounted without it
  (`app/api/v1/__init__.py:50,221`).
- **Company scope:** `user_companies` grants per user, roles global; the resolver's decision table
  is `app/services/company_scope_resolver.py:12-32` (staff: one active company; admin: all;
  portal token: the contact's `respond_contact_companies`; no principal: fail closed). For
  `/public/` paths **a Bearer token is checked before the portal token** (:323-328), which matters
  for S2 (AC-32). Two leftovers there: a `jwt.decode(bearer, jwt_secret)` attempt (:81) and an
  env `EXTERNAL_API_KEY` compare (:66-68); real API-key auth moved to `integration_api_keys`
  (`app/dependencies.py:491-560`).
- **Teams** `teams` / `team_members(user_id)` (`app/models/access.py:518-588`).

### 3.2 CRM sign-in and sessions

- **NextAuth, one provider:** `CredentialsProvider` (email, password, rememberMe)
  [Superseded by SIGNIN-ALWAYS-SLIDE, 30 Sep 2026: no Remember me checkbox and no `rememberMe`; every sign-in is the 30-day sliding session. See `PLAN-signin-always-slide-30sep.md`.]
  (`app/api/auth/[...nextauth]/auth-options.ts:64-125`). `authorize()` posts to FastAPI
  `/api/v1/auth/login` and stores the returned opaque `apiToken` (:121) in the NextAuth JWT with
  id, email, name, roles and company grants (:126-185). `/api/auth/token` hands the `apiToken` to
  the browser; a 401 with `session_revoked` / `session_expired` / `session_invalid` signs out
  (`lib/api.ts:195`).
- **FastAPI validates the opaque token, not a JWT:** `get_current_user`
  (`app/dependencies.py:184`) resolves `user_sessions` (`app/models/user_session.py:28-47`:
  token, user_id, expires_at, revoked_at, rolling, user_agent, ip_address, last_seen_at), loads
  the user and first role, rejects non-ACTIVE. Rolling 30 days (slide at 29) or 8 hours when not
  remembered (`app/services/user_session_service.py:25-34`). Impersonation: admin with an open
  `ImpersonationSession` + `X-Impersonate-User-Id` (`app/dependencies.py:114-153`). The archived
  `PLAN-staff-rolling-sessions-fastapi-auth.md` fully shipped (migration `237_user_sessions`,
  revocation, device list at `account/security/current-sessions`).
- **Login throttle:** 8 failures per email+IP lock 15 minutes, Redis, fails open
  (`app/services/login_throttle.py:15-17`).
- **Gaps measured:** an unknown email returns `404 "User not found. Please register first."`
  (`app/api/v1/auth.py:59-65`), which enumerates emails (fixed in S1, AC-26); login and create
  match email case-sensitively (`auth.py:55`, `user_service.py:492,521`) while update lowercases
  (`user_service.py:570-574`), so case-duplicates are possible (AC-03); login does not check
  `is_trashed`; self-signup creates an INACTIVE user and never sends its email (`auth.py:226`,
  and the sign-in page links no signup); session tokens are stored in plain text (section 11).
- **The sign-in page today** (`app/(auth)/signin/page.tsx`, the design Q16 binds S1 to): inside
  `BrandedLayout`'s narrow card (`app/(auth)/layouts/branded.tsx:10-16,61-75`: `max-w-md`, the
  configurable backdrop image, `p-6`), top to bottom: the Sorento wordmark (:91-106), the heading
  "Sign in to Sorento" (:108-112), a destructive `Alert` for errors (:114-121), "Email" `Input`
  (:123-135), "Password" with "Forgot Password?" on the label row and the eye toggle (:137-178),
  "Remember me" `Checkbox` (:180-200), and a full-width "Continue" `Button` with a spinner
  [Superseded by SIGNIN-ALWAYS-SLIDE, 30 Sep 2026: no Remember me checkbox and no `rememberMe`; every sign-in is the 30-day sliding session. See `PLAN-signin-always-slide-30sep.md`.]
  (:202-209). It calls `signIn('credentials')` and honours a same-origin `callbackUrl` (:48-72).
- **No Next.js middleware:** CRM pages are gated client-side in `app/(protected)/layout.tsx:24-58`.
- **Account lifecycle:** admin create (with password) or invite (no password, INACTIVE)
  (`app/api/v1/user_management/users.py:554-588`, resend `:652`); accept-invite and reset both go
  through `/auth/change-password?token=`, which sets ACTIVE and revokes sessions
  (`auth.py:358-411`); forgot password is non-enumerating and rate-limited (`auth.py:243-328`);
  force-logout (`users.py:274`); delete is soft `is_trashed`, hard only once trashed
  (`user_service.py:641-666`).
- **What "Add user" sends today (the Q17 concern, measured).** Yes: by default, creating a user
  with an email sends an invitation email in the same click.
  - The Add user modal (`app/(protected)/user-management/users/components/user-add-dialog.tsx`)
    holds `sendInvitationEmail` **defaulting to true** (:50), shown as a pre-ticked checkbox "Send
    invitation email (they will set their password via the link)" (:372-383). On save it posts to
    `/api/user-management/users/invite` when ticked, else `/api/user-management/users`
    (:137-139); the success toast then reads "Invitation sent to <email>" (:155-157).
  - The Next proxy `app/api/user-management/users/invite/route.ts` forwards to FastAPI
    `POST /api/v1/user-management/users/invite` (`users.py:554-569`), which in one request calls
    `UserService.invite_user` (`app/services/user_service.py:519-543`: INACTIVE, no password,
    `invited_by_user_id`) and then `_send_invitation_link_for_user` (`users.py:161-213`), which
    writes a 7-day verification token and queues "You're invited to join the platform" through
    `NotificationService.create_with_channel_preferences(send_email=True)` (:200-209).
  - Unticked, `POST /users` (`users.py:572-585`, `user_service.py:490-517`) sends nothing.
  - Two other senders exist, both deliberate today: the user record action "Send invitation link"
    (`app/(protected)/user-management/users/actions.tsx:76-96`, pushed at :120-127) posts to
    `/{id}/resend-invite` (`users.py:652-668`) **at once, with no confirmation**; the Users list
    bulk action `resend_invite` (`user-list.tsx:181-185`) does ask first (:469-471) and sends
    through `users.py:248-266`.
  - Editing an existing user's email queues an "account_email_changed" security notice
    (`user_service.py:562-584`, queued at :630-637, sender :224-235); it is skipped when the user
    had no email before (`old_email_for_notification` is None).
  - Linking a user to a WhatsApp contact sends nothing today: the field already exists in the
    user's Edit profile dialog (`app/(protected)/user-management/users/[id]/components/user-profile-edit-dialog.tsx:736-774`,
    TCK-31) and saves through `PUT /users/{id}` (`user_service.py:598`).

### 3.3 Portal sign-in

- **The portal principal is a contact plus a Respond.io space, carried by an opaque token.**
  `portal_tokens` (`app/models/portal.py:26-44`: token, `contact_id` Text no FK, `space_id`,
  `expires_at`, `revoked_at`, `verified_at`, `is_impersonation`). `portal_otp_codes`
  (`portal.py:47-62`, sha256 of a 6-digit code, keyed by contact).
- **Lifetimes** (`app/services/portal_service.py:57-68`): a minted link token lasts 7 days; an
  OTP-verified token 30 days sliding (`resolve_token` :378-409); a code 10 minutes, 60s cooldown,
  5 attempts, 10 sends per contact per day; per-IP limit on `request-otp` 30 per 60s
  (`app/config.py:199-200`). No reCAPTCHA anywhere in the backend.
- **Every token starts unverified** (`mint_token` :247 never sets `verified_at`); `verify_otp`
  (:598-645) verifies all the contact's tokens and mints a fresh 30-day one. The docstring at
  `app/api/v1/external/portal_tokens.py:4` claiming OTP is bypassed is wrong.
- **Cross-device login shipped:** `/portal/c/{slug}` is an identity hint, `GET /slug-info/{slug}`
  and `GET /token-info` return contact id, space id, name and masked phone, and the verify card
  fires `request-otp {contact_id, space_id}` itself (`portal.py:111`,
  FE `PortalVerifyCard.tsx:110,161-200`). The person never types a phone number. The code screen
  is one card "Verify your identity" (:411-483): "We'll send a code to your WhatsApp <masked>",
  "Not your number?", one "Verification code" `Input` (`variant="lg"`, `inputMode="numeric"`,
  `autoComplete="one-time-code"`, placeholder "6-digit code", :446-458), an outline "Send code" /
  "Resend in Ns" button (:461-473), and it verifies on the sixth digit with no submit button
  (:13, :280-285).
- **Code delivery** is an RQ job on the `respond_io` queue (`app/tasks/respond_io_tasks.py:180`,
  template `portal_otp`, variable `otp_code`), so it needs the worker. **There is no SMS provider
  anywhere in the codebase** (no Twilio, no SMS code).
- **Route dependency:** `get_portal_token` (`app/api/v1/public/portal.py:88-105`) reads
  `X-Portal-Token` or `?token=` and stamps the contact into `db.info["actor_contact_id"]` and a
  context var. About 46 route dependencies use it (27 in `portal.py`, 19 in
  `portal_price_tag.py`, plus `ai_extract.py`).
- **Other token mints:** staff (`app/api/v1/user_management/contacts.py:650-681`, permission
  `user_management.contacts.portal_link`); n8n / MCP `POST /external/portal-tokens/`; admin
  "view as contact" (`contact_impersonation.py:109-196`) mints a pre-verified token, and
  `contact_impersonation_sessions.portal_token_id` is a NOT NULL FK to `portal_tokens`.
- **FE:** the portal lives under `app/(auth)/portal/` (legacy tree plus `c/[slug]`), outside
  NextAuth. `portal-client.ts` keeps `sorento.portalToken` in localStorage (impersonation in
  sessionStorage, which wins) and sends `X-Portal-Token` (:268-322, :359).
- **Form grants by market segment** (`app/services/portal_form_visibility_service.py:44-109`):
  base four kinds, plus the union of `market_segments.portal_form_types` over the contact's
  segments, then `contact_portal_form_overrides` rows win (`app/models/price_tag.py:56-79`). All
  keyed on `respond_contacts.id` (plan `documentation/plans/portal/PLAN-portal-forms-market-segment.md`).
- **What a submission records:** `contact_id` + `space_id` + `respond_inbox_url` from the token
  (`portal_service.py:1234-1265`); ownership everywhere is
  `entity.contact_id == token.contact_id AND entity.space_id == token.space_id`, else 404 or 403
  `OWNER_MISMATCH` (:811-835); price tag checks contact only
  (`portal_price_tag.py:831-853`). Portal status messages deep-link through
  `submission_link(contact_id)` to `/portal/c/{slug}/{type}/{id}` (about 10 callers).

### 3.4 Contacts, sales agents, dealer contacts, Respond.io contacts

- **`respond_contacts`** (`app/models/access.py:230-307`) is the WhatsApp identity and the portal
  identity: `id` Text PK, `phone_number` Text NOT NULL UNIQUE (:234, normalised digits),
  `respond_io_id` (:238), `portal_slug` unique (:241), `workspace_id` FK to `respond_workspaces`
  (:242). No email, no user id, no customer id, no type column. Classified only by links:
  access types (`respond_contact_access_types`, seeded codes `end_user`, `dealer`,
  `alembic/versions/094_contact_access_types.py:61-62`), market segments
  (`respond_contact_market_segments`, seeded `retail`, `project`,
  `263_market_segments.py:107-109`, with `is_requestor_selectable` at `access.py:143` feeding the
  "Requested by / Salesperson" picker), companies (`respond_contact_companies`,
  `app/models/company.py:65`).
- **Nothing in the code defines "product salesperson" or "retail salesperson".** The closest
  facts: the `retail` and `project` segments, both requestor-selectable, and `sales_agents`.
  "Product salesperson" is read in this plan as "project salesperson" (the `project` segment),
  which a voice transcription would produce; Q1 asks.
- **`sales_agents`** (`app/models/sales_agent.py:42-134`) is the AutoCount salesperson master,
  one row per agent code, with `person_label`, nullable `company_id`, and `contact_id` Text FK to
  `respond_contacts` (:86-93). `customers.sales_agent_id` (`app/models/order.py:142-146`) gives a
  customer one agent; the portal price tag debtor dropdown reads the agent by its contact
  (`price_tag_request_service.py:2390,2408-2411`).
- **Requested-by routing** stores people as contacts: `purchase_requests.requested_by_contact_id`,
  `stock_inquiries.salesperson_contact_id` (`documentation/plans/PLAN-requested-by-contact-routing.md`),
  CS pins `respond_contact_cs_routing` (`access.py:310-361`).
- **Dealer contacts:** a dealer is a `customers` row (ADR `documentation/adr/0007-a-dealer-is-a-customer-not-a-company.md`);
  its WhatsApp people link through `respond_contact_customers` (`access.py:11-64`, one primary per
  contact per company) and usually carry access type `dealer`. `customer_contacts`
  (`order.py:220-252`) are staff-typed address-book people with no link to contacts or users.
- **Respond.io contacts** are synced in by `respond_sync_handler.py:111` and matched by
  `respond_io_id` or phone (`app/services/respond_identifier.py:18,45`). Every automated send goes
  through `send_text_or_template` (`app/services/respond_messaging_service.py:545`), free text in
  the 24h window, the approved template outside it.
- **The contact-to-user link already exists:** `users.respond_contact_id`, set by an admin
  (`user_service.py:598`) or cached from a unique phone match between `users.contact_number` and
  `respond_contacts.phone_number` (`app/services/respond_link_service.py:25-48`). Used today for
  staff WhatsApp notifications only. Onboarding intake provisions a user and a contact side by
  side but never sets this link (`app/services/onboarding_service.py:784`), and types
  `onboarding_people.respond_contact_id` as UUID against a Text PK (`app/models/onboarding.py:246`).

### 3.5 Every place a "who did this" is recorded

- **About 150 actor columns in three id namespaces plus free text** (full table in the lane's
  research notes, summarised): `created_by` / `updated_by` / `*_by` / `*_by_id` pointing at
  `users.id` (roughly 40 with an FK, 60+ without); six contact-typed actors
  (`requested_by_contact_id` `procurement.py:1018`, `submitted_by_contact_id` `portal.py:114`,
  `uploaded_by_contact_id` `resources.py:170`, `collected_by_contact_id` `price_tag.py:153`,
  `author_contact_id` `price_tag.py:450`, ticket comment `respond_contact_id`), four of them paired
  with a user column; about five holding a Respond.io agent id (`complaints.assigned_to:68`,
  `last_responded_by`, `sla.responded_by:99`); about six free-text names or emails
  (`procurement.requested_by:1014`, `approver_email:1040`, `onboarding.requester_email:138`);
  three that hold "an id or a name" (`complaints.rejected_by/resolved_by`, `sla.resolved_by:103`).
- **`audit_logs`** (`app/models/audit.py:9-45`): entity_type, entity_id, action, `user_id` (UUID,
  no FK), `contact_id` (no FK), old/new values, description, ip_address, trace_id, company_id. No
  actor kind, no user agent, no session, no integration, no impersonator. 44 models opt in with
  `__audit_track__`; the before-flush listener is `app/services/audit_service.py:323`.
- **How the actor is found:** context vars (`app/audit_context.py`); `LoggingMiddleware` resets
  them per request (`app/middleware/logging_middleware.py:20`); JWT dependencies set the user
  (`app/dependencies.py:222,273,306`); the portal sets the contact.
- **Measured gaps** that the section 8 contract closes: (a) no actor kind, so worker, scheduler,
  unauthenticated and lost-context writes all render "System"
  (`app/api/v1/audit/audit_logs.py:110-116`); (b) an API-key write looks exactly like its act-as
  user, and `api_call_log.actor` is always NULL
  (`app/middleware/api_call_log_middleware.py:197`); (c) RQ and scheduler jobs never set the audit
  context although tasks receive a `user_id` argument (`app/tasks/export_tasks.py`,
  `autocount_pull_tasks.py:236`); (d) impersonation rewrites only four column names
  (`audit_service.py:251-274`) and stores no impersonator; (e) **suspected, to confirm by test in
  S0:** admin "view as contact" writes are credited to the contact although the docstring
  (`contact_impersonation.py:7-9`) says the admin stays the actor; (f) **suspected, to confirm by
  test in S0:** `get_current_user_or_api_key` (`app/dependencies.py:531`) is a sync dependency
  running in a copied context, so its `set_audit_context` may be invisible at flush, leaving
  `user_id` NULL on writes through the ~41 route files that use it; (g) no user agent or session
  on audit rows.
- **Actor kinds that exist today:** staff user; impersonating admin; integration acting as a user
  (n8n, MCP, AutoCount ESB); portal contact; admin viewing as a contact; Respond.io agent; Respond.io
  contact (chatbot, webhooks); worker or scheduler; anonymous public-link approver.
- **MCP** sends the shared API key plus `X-Source: mcp`, `X-Tool-Name`, `X-Correlation-Id`
  (`sorento_crm_mcp/sorento_crm_mcp/http_client.py:25,91-95`) and has four write tools
  (`record_actions.py`), all acting as the integration's act-as user.

## 4. Design: one principal, several ways in

### 4.1 The person

- **A person who signs in is a `users` row.** Staff, salespeople and portal contacts alike. A
  user has at most one linked WhatsApp contact (`users.respond_contact_id`, made unique where set,
  AC-01), and a contact has at most one user. The contact stays the WhatsApp and portal identity
  it is today: everything keyed on `respond_contacts.id` (form grants, ownership of submissions,
  requested-by, sales agents, CS pins, `respond_inbox_url`) is untouched. The user is what signs
  in, holds roles, holds a session, and is written into the audit log.
- **Email becomes optional** (AC-02, Q5; owner ruling 27 Sep 2026 00:20 MYT: "identity plan q5 -
  yeah can"). The owner's create flow in section 6 relies on it: a salesperson who has only
  WhatsApp is created with a phone and no email. `users.email` NOT NULL is relaxed; a check constraint
  requires `email IS NOT NULL OR contact_number IS NOT NULL`. A salesperson with only WhatsApp is a
  user with a phone and no email. Placeholder emails (`6012...@phone.local`) are rejected as a
  design: the email outbox, invite, reset and SLA emails all read `users.email`, and a fake
  address turns each of them into a silent bounce. S0 lists every `users.email` reader
  (`grep -rn "\.email" app/`), and each one that would break on NULL gets a guard (skip the email
  channel, never raise) with a test.
- **Email is unique case-insensitively** (AC-03): a unique index on `lower(email)` replaces the
  plain one, and every write lowercases. The S0 pre-flight query lists case-duplicates first; if
  any exist the migration stops and they go to the owner (never a silent merge).
- **Phone stays unique** (the existing `uq_users_contact_number`), normalised by the one
  normaliser `normalize_msisdn`. `phone_verified_at` (new, nullable) is set by a successful code
  and cleared when the number changes. The second normaliser, `utils/phone_normalize.normalize_phone`
  (digits only), is not used by anything in this plan.

### 4.2 The ways in

| Method | Who | Proves | Result |
| --- | --- | --- | --- |
| Email + password | any user with a password (as today) | knows the password | `user_sessions` row, `auth_method = password` |
| Phone + WhatsApp code | any ACTIVE non-integration user whose phone resolves to a linked contact (AC-21, AC-27) | holds the WhatsApp number | `user_sessions` row, `auth_method = phone_otp` |
| Portal link (`/portal/c/{slug}`, `/portal?token=`), contact with a user | the person the owner set up | holds the WhatsApp number (the same code) | `user_sessions` row, `auth_method = portal_link` (AC-34) |
| Portal link, contact with no user | a portal contact the owner has not set up | holds the WhatsApp number | a `portal_tokens` row, exactly as today; no user is created (AC-35) |
| API key | integrations (n8n, MCP, AutoCount) | holds the key | unchanged; acts as the integration's user, now named in audit (AC-09) |
| Admin "view as contact" | admins | an admin session | unchanged token, now audited as the admin (AC-11, Q12) |

- **Phone login reuses the portal's code machinery, not a new table.** The typed number is
  normalised, resolved to one contact (`respond_contacts.phone_number`, unique) and that contact's
  linked user; the code is stored in the existing `portal_otp_codes` keyed by that contact, sent
  by the existing `send_portal_otp_respond_message` task, and checked by the same limits. The
  difference from the portal flow is only the entry (a typed phone instead of a slug) and the
  result (a user session instead of a portal token). A user whose phone has no contact cannot use
  phone sign-in until an admin links one (AC-52 shows it); creating Respond.io contacts from a
  typed number at sign-in is not done (it would let anyone create contacts).
- **No enumeration** (AC-21, AC-26): request-code answers the same body for every number; email
  login answers the same 401 for an unknown email and a wrong password.
- **SMS is not built** (Q6; owner ruling 26 Sep 2026 23:45 MYT: "yeap whatsapp codes"). The codebase has no SMS provider; WhatsApp is how every one of these
  people already talks to Sorento. Named trigger in section 11.

### 4.3 One session model

- **`user_sessions` is the only session.** It gains `auth_method` (AC-07). NextAuth gains a
  second Credentials provider, `phone-otp`, whose `authorize()` posts `{phone, code}` (or
  `{contact_id, space_id, code}` from the portal verify card) to FastAPI and stores the returned
  `apiToken` exactly as email login does. Everything downstream of the token (company context,
  roles, 401 sign-out, device list, force-logout, revocation on password change) works unchanged.
- **Lifetime:** phone and portal sessions are 30-day rolling, the same as "remember me" today
  (Q15: the portal's 30-day sliding token is what these people are used to). Email sessions keep
  their remember-me choice.
- **The portal reads the same session.** Portal pages obtain the `apiToken` from
  `/api/auth/token` and send `Authorization: Bearer`. `get_portal_token` becomes
  `get_portal_principal`, returning the same three things every portal route reads today
  (`contact_id`, `space_id`, and an id for logout), from either (a) a user session whose user has a
  linked contact, space from the contact's workspace, or (b) an `X-Portal-Token`, which stays the
  way in for every contact the owner has not set up a user for (AC-36). The 46 route signatures
  change type, not logic; AC-31 proves each form kind answers identically under both principals.
- **The verify card picks the path before it sends the code.** `slug-info` and `token-info` gain
  one boolean, `has_user` (the contact is linked to an ACTIVE user). True: the card signs in
  through NextAuth `phone-otp` and a `user_sessions` row results. False: the card runs today's
  `request-otp` / `verify-otp` and a portal token results. Server side is authoritative either
  way: `/auth/phone/verify` refuses a contact with no user (same 401 as a wrong code), and
  `/public/portal/verify-otp` for a contact that has a user still works (an old cached page) but
  its token does not slide (AC-36). The bit is visible only to someone already holding the slug,
  who today already sees the name and masked phone; S2's security review checks it.
- **Company scope for portal routes stays the contact's** (AC-32): the resolver's `/public/`
  branch checks for a portal principal before a Bearer user, so a salesperson with CRM grants still
  sees portal data scoped exactly as the portal did.
- **Where a person lands** (AC-28, AC-39, Q9; owner ruling 26 Sep 2026 23:45 MYT: "for now
  salesperson still land at portal first"): a `callbackUrl` the user may open wins; otherwise a
  user holding the `salesperson` role lands on the portal home `/portal/c/{slug}` of the linked
  contact, whatever else it holds; any other user with a CRM permission (or admin) lands on the
  CRM home; a user with none lands on the portal home. The
  `(protected)` layout sends a portal-only user to their portal home instead of rendering an empty
  shell; the user menu carries "Portal" for anyone with a linked contact, and the portal header
  carries "Open CRM" for anyone with a CRM permission.

## 5. Sign-in and recovery flows

Screens are designed at 375px first, then 1280px. No explanatory prose in the UI (PRINCIPLES
design mandates); copy below is the full visible text.

### 5.1 Sign-in (the existing `/signin`, AC-20, AC-25, AC-29)

Owner ruling 27 Sep 2026 00:45 MYT (Q7, Q16): "i just need 1 sign in page which is
https://fe-sorento.foundryx.my/signin with option to sign in by phone number" and "make sure i can
toggle between phone auth and email pw auth in the sign in page while obeying the design element
of the current sign in page". So: no new page and no one-field guesser (the round 1 design is
withdrawn); the existing page gains a two-way toggle, and every other element is the one it
already has (section 3.2).

- **Layout, top to bottom, in the same narrow card** (fix round 1, owner hand test 29 Sep 2026:
  "the UI of togggling between email and phone not so nice, do something like this, 'or Log in
  with' then the phone number icon", replacing the round 4 `Tabs` toggle): the wordmark; "Sign in
  to Sorento"; the error `Alert` slot; the email form; then a divider (`Separator` either side of
  "or Log in with") and one round outline icon `Button` with the `Smartphone` icon (tooltip and
  accessible name "Phone number"). Pressing it swaps the card body to the phone flow, which
  carries a small "Back to email" link under it. No Email | Phone toggle, no Facebook or Google
  button, no Sign Up line. No extra heading, no explanatory copy.
- **Email mode** is today's page exactly: Email, Password with "Forgot Password?", the eye
  toggle, Remember me, Continue. Nothing moves; the divider and phone button sit below it.
  [Superseded by SIGNIN-ALWAYS-SLIDE, 30 Sep 2026: no Remember me checkbox and no `rememberMe`; every sign-in is the 30-day sliding session. See `PLAN-signin-always-slide-30sep.md`.]
- **Phone mode, step 1:** "Phone number" through the shared system `PhoneInput`
  (`components/common/PhoneInput.tsx`; fix round 2, owner ruling 29 Sep 2026: "i think our phone
  number input needs to use a proper phone number input ... default to malaysia so all phone
  number is cleansed"): a flag + dial-code country picker (the standard `SearchableSelect`),
  Malaysia (+60) by default, the number in national format as it is typed, placeholder
  "012-345 6789", then the full-width "Continue" button. "0160000509", "60160000509" and
  "+60 16-000 0509" all become `+60160000509`; the FE sends that E.164 value, and an incomplete
  number stops at the field's error state ("Enter a complete phone number.") without a request.
  The backend still normalises what it receives with `normalize_msisdn` (the second line). No
  Remember me: a phone sign-in is always the 30-day rolling session (Q15).
- **Phone mode, step 2 (same card, "Back to email" stays):** the line "We'll send a code to your
  WhatsApp <masked number>" with "Change number" beside it, one "Verification code" `Input`
  styled exactly as the portal's (`variant="lg"`, numeric, `one-time-code`, placeholder "6-digit
  code", centred, letter-spaced), and the outline "Resend in 60s" / "Resend code" button under it.
  It signs in on the sixth digit, the same as the portal card; there is no separate submit.
- **Reuse, not a new sender** (Q16: "we already got this send otp mechanism so just reuse it"):
  the code is the portal's code (`portal_otp_codes`, the `portal_otp` WhatsApp template, the
  `send_portal_otp_respond_message` job on the `respond_io` queue, the same limits); only the
  entry differs (section 4.2). The FE shares the portal card's code input and countdown, lifted
  into one component both pages import, not copied.
- **The code is readable in the Respond outbox** (owner ruling 29 Sep 2026, "show the code in the
  outbox", overruling security round B1 of #1280): the `integration_logs` row for the sign-in code
  send, and for the portal OTP send that shares `_send_and_log`, keeps the real code in
  `request_payload`, `response_payload` and `error_message`. Why: the operator reads the code from
  the outbox on a test copy. B1 had replaced it with `******` because `GET
  /api/v1/integrations/logs` has no permission gate of its own; that exposure is accepted by the
  owner. Every other read of a contact's WhatsApp messages still masks the code (reviewer B2,
  `app/services/otp_redaction.py`), which this ruling does not change.
- **Errors in words, in the existing `Alert`:** wrong code "That code is not right. 4 tries
  left."; expired "That code has expired. Send a new one."; limit "Too many tries. Try again in 12
  minutes." (from the 429's seconds); no WhatsApp contact or unknown number: nothing distinguishes
  it on screen (the code simply never arrives), by design.
- **Who uses Phone here:** Q16 says "this is for normal user". Staff and admins (Q10) use it. A
  salesperson is not sent to this page: their way in is the portal link (5.2). If one does sign in
  here by phone it works and lands on their portal home (Q9); nothing blocks it, because blocking
  would need a second rule for no gain.
- At 375px with the keyboard open, the code input and the resend button stay above the fold.

### 5.2 Portal link (AC-34, AC-35)

Owner ruling 27 Sep 2026 00:45 MYT (Q16): "for salesperson, they will still access via portal
which will prompt them to enter verification code when needed". So salespeople keep the portal as
their door, and the portal's own verify card is where they are asked for the code, only when the
device holds no live sign-in (first visit, expiry, sign-out). The verify card the contact already knows stays as it is visually. What changes is behind the
button, and only for a contact the owner has set up (`has_user`, section 4.3): `verify` calls
NextAuth `signIn('phone-otp', {contact_id, space_id, code})`, FastAPI finds the contact's linked
user (never creates one), mints a `user_sessions` row with `auth_method = portal_link`, and the
page continues to the portal home with the session in place; `sorento.portalToken` is not written
for that person, and a token they stored earlier works until it expires without sliding (AC-36).
For a contact with no user, nothing changes at all: same code, same portal token, same 30-day
sliding session (AC-35).

### 5.3 Recovery

- **Forgot password:** unchanged (email reset link, 1 hour, non-enumerating). A phone-only user
  has no password to forget: the code IS the sign-in.
- **A phone-only user can add a password** from Account > Security ("Set a password"), and an
  email from Account > Profile (verified by the existing email-verification link before it is
  used for sign-in). Both optional, and both started by the person themselves; an admin adding an
  email to someone's user sends nothing (6.4).
- **Lost access to the WhatsApp number:** an admin edits the phone on the user (AC-54): every
  session ends, `phone_verified_at` clears, and the next phone sign-in to the new number verifies
  it. A user with an email can also recover through the password reset.
- **A contact's number changes in Respond.io** (AC-46): the linked user is NOT changed by the
  sync (the owner sets users up, not a sync). The user's phone and the contact's phone now differ,
  so phone sign-in for that user is refused (the code would go to a number the owner never
  approved), the user's Sign-in section on its existing Administrative Users page shows "Needs
  attention: phone differs from WhatsApp contact" (S3, 6.3), and the owner either takes the new
  number (the user's phone is updated, unverified, sessions end) or unlinks.
- **Blocked or trashed users** cannot sign in by any method, including a code (fixes the measured
  `is_trashed` gap at the same time).

## 6. Counterpart users: the owner creates and sets up every user

Owner ruling 26 Sep 2026 23:45 MYT (Q3, Q4): users are created consciously by the owner at the
backend, never by the system. This section replaces the round 1 design (backfill for salesperson
contacts, lazy creation at first portal sign-in), which is withdrawn in full.

Owner ruling 27 Sep 2026 00:45 MYT (Q16): "i don't need another page called salesperson account,
just need existing Administrative Users and Internal contacts" and "i don't need a brand new page
for user management". So round 3's Salesperson accounts worklist and the separate admin identity
slice (S4) are withdrawn. Everything below is a change to a page, modal or section that exists
today; no route, sidebar entry or listing is added.

### 6.1 Who is a salesperson contact (Q1)

Owner ruling 26 Sep 2026 23:45 MYT (Q1): "yeah". One function, `is_salesperson_contact(contact)`
(AC-40): the contact is in any market segment with `is_requestor_selectable = true` (today
`retail` and `project`, "product" read as "project"), **or** is the `contact_id` of any
`sales_agents` row. Both sources exist and are admin-maintained today; no new flag is added. The
function creates nothing and lists nothing: its only use is which role the Add user modal
suggests (6.2).

### 6.2 Where the owner creates and links (AC-41 to AC-44, AC-48, AC-53, AC-59)

One create form, the existing Add user modal (`user-add-dialog.tsx`), opened from three places on
the two existing pages:

| Existing page | Entry point | Opens the Add user modal with |
| --- | --- | --- |
| Administrative Users (list) | "Add user" in the toolbar, as today | nothing prefilled; the new WhatsApp contact field is optional, and picking a contact fills the rest (the quick create from a Respond contact, from the Users side) |
| Internal Users (list) | row action "Create user", shown only on a contact with no user | that contact locked, everything below prefilled (the quick create from a Respond contact, from the contacts side) |
| Internal Users > a contact > Profile tab | new "User account" section, "Create user" | the same, for that contact |

The modal's fields, in order (the round 4 changes in bold; view and edit of the user show the
same fields in the same order):

1. Name, prefilled from the contact's name.
2. Email, **optional when there is a phone** (Q5), required when there is not.
3. Contact Number, **prefilled from the contact and read-only while a contact is set** (the phone
   IS the contact's phone, so a code can only reach that WhatsApp number); editable only for a
   user with no contact.
4. **WhatsApp contact** (`SearchableSelect`, clearable when opened from the Users list, locked
   when opened from a contact), the same picker the Edit profile dialog already uses
   (`user-profile-edit-dialog.tsx:736-774`, name and phone, never an id). Sets
   `users.respond_contact_id`. Picking one fills Name, Contact Number and Companies and suggests
   the role, overwriting only fields the owner has not typed in.
5. Copy roles from another user (as today).
6. Roles (`SearchableMultiSelect`). Suggested, never forced: `salesperson` when the contact is a
   salesperson contact (6.1), `portal_user` for any other contact, the `is_default` role when
   there is no contact.
7. Companies, prefilled from the contact's `respond_contact_companies`; the first becomes
   `last_active_company_id`.
8. Superior (as today).
9. **The "Send invitation email" checkbox is removed** (6.4). The footer is Cancel and "Add user".

Saving always posts to `POST /api/v1/user-management/users`, never `/invite`. The user is created
ACTIVE when it has a phone (the WhatsApp code will prove the person), or INACTIVE with no password
when it has only an email (it can sign in once the owner sends the invitation, 6.4). The toast
reads "User added"; the contact's User account section and the Internal Users row show the user
with no reload.

**Quick create** is this prefilled modal: from an Internal Users row or a contact, the owner
checks the filled-in fields and clicks "Add user"; one screen, one click when nothing needs
changing. No separate quick-create form (it would be a second copy of the same fields).

**The Internal Users list** gains one column, "User" (the linked user's name, a link to it; blank
when there is none), so the owner can see at a glance who is set up. Standard DataGrid rules
(explicit size, truncate + title). No filter, no state machine: the round 3 worklist states are
withdrawn with the page.

Rules the backend enforces, each with a 409 the modal shows inline (AC-42, AC-43):

- **The contact already has a user:** 409 `CONTACT_ALREADY_LINKED`, naming that user; the modal
  offers "Open user".
- **The phone already belongs to a user** (Q14, still on its recommendation): 409
  `PHONE_BELONGS_TO_USER`, naming that user, and the modal offers "Link this contact to
  <name> instead". One person, one user, but the owner decides; nothing is merged by itself.
- **That existing user is already linked to a different contact:** the link is refused too; the
  owner resolves it by hand (Unlink there first).

**Linking an existing user** (a staff member who is also a salesperson), from either side:

- From the contact: the User account section has "Link existing user" beside "Create user" (a
  `SearchableSelect` of users with no contact; saving sets that user's `respond_contact_id`).
- From the user: the existing Edit profile dialog's WhatsApp contact field
  (`user-profile-edit-dialog.tsx:736-774`), unchanged on screen, now with the same 409 rules.

Linking adds no role and sends nothing; the owner adds `salesperson` in Roles if wanted.

**Then the person signs in.** Nothing is sent automatically. A salesperson opens the portal link
they already have (5.2); staff use `/signin` with the Phone toggle (5.1). To hand someone a link,
the owner uses the existing "Send portal link" contact action (`PortalLinkButton`, backend
`app/api/v1/user_management/contacts.py:650-681`), no new message.

**Backend:** the existing `POST /api/v1/user-management/users` (and `PUT /{id}`) accepts
`respond_contact_id`, an optional `email`, and `company_ids`; no new create endpoint. The FE
prefills from the contact it has already loaded, so no prefill endpoint either. The Internal Users
listing gains the linked user's id and name in its row payload (the "User" column). Permission:
`user_management.users.add` to create (the permission the create route already checks,
`users.py:575`), `user_management.users.edit` to link, unlink or change the phone. Every create
and link writes an audit row with the acting admin and the contact (AC-47).

### 6.3 The user's Sign-in section, on the existing user page (AC-46, AC-52, AC-54)

Round 3 put this in S4 as part of a set of admin screens; with S4 withdrawn it is one read-only
section added to the existing Profile tab of an Administrative Users record, in S3:

- Email (or "No email"), Phone with "verified" or "not verified yet", WhatsApp contact (name and
  masked phone, a link to the contact) or "Not linked", last sign-in time and how (Email,
  Phone, Portal).
- **Needs attention: phone differs from WhatsApp contact**, shown only in that case, with "Use
  new number" (updates the user's phone, clears `phone_verified_at`, ends every session).
- **Unlink**, a 5-second deferred action with Cancel (D7), never a dialog; it ends every session
  of the user.
- **Send invitation email** (6.4), shown only when the user has an email, and "Invitation not
  sent" beside the email while an email-only user has never been invited.
- Editing any of it happens in the existing Edit profile dialog, whose fields sit in the same
  order.

The Users list is not changed (round 3's Kind filter and Sign-in column are withdrawn with S4:
nobody asked for them, and the role column already tells a salesperson apart).

### 6.4 No email is ever sent by creating or linking a user (Q17, AC-56 to AC-58)

Owner ruling 27 Sep 2026 00:50 MYT (Q17): "can this be a manual step ah cause i worry will
accidentally send the email to the salespersons when we create for them". The answer to the
owner's question is yes, today it does (section 3.2: the Add user checkbox is ticked by default
and routes to `/invite`, which creates and emails in one request). After S3:

- **Create sends nothing, whatever the fields.** The Add user modal loses its checkbox and always
  posts to `POST /users`. `POST /users/invite` and its Next proxy
  (`app/api/user-management/users/invite/route.ts`) are removed (the modal is their only caller;
  S3 greps the FE, n8n workflow exports and the MCP catalogue before deleting, and keeps the
  route returning 410 if any other caller turns up). A test asserts that creating a user with an
  email, with a phone, and with both queues zero `email_outbox` rows and zero notifications.
- **Link, unlink, role and company edits send nothing**, and neither does adding a first email to
  a phone-only user (already skipped today, section 3.2). A test covers each.
- **Sending is one deliberate button, off by default.** The existing "Send invitation link" record
  action on the user page becomes "Send invitation email" and now opens a confirmation first:
  title "Send invitation email?", body "An email with a link to set a password goes to
  <email>.", buttons Cancel and "Send email" (Cancel focused, so Enter does not send). Only
  "Send email" calls the existing `POST /{id}/resend-invite`. The action is hidden for a user
  with no email. The Users list bulk "Resend invitation" already confirms first; it now skips
  users with no email and says how many it skipped.
- A confirmation dialog here does not break D7: D7 retires confirmations for destructive actions
  (they get a countdown instead); this one guards an outward message that cannot be recalled,
  which is what the owner asked for.
- **What stays:** the security notice when an admin replaces an email a user already has
  ("account_email_changed", section 3.2) keeps going out; it tells an existing account holder
  their sign-in changed, it is not an invite. Q18 asks the owner to confirm.
- **Verification email:** there is no separate verification email on create today (the invite
  link doubles as it); nothing in this plan adds one. A phone-only user proves their number with
  the WhatsApp code at first sign-in, which is not an email and is started by the person.

### 6.5 What the system never does (AC-44, AC-45, AC-56)

- It never creates a user: no migration backfill, no hook on market segment or sales agent
  changes, no creation at a portal verify step or a phone sign-in.
- It never changes a user's roles: adding a contact to a salesperson segment, or taking it out
  of every one, changes nothing on the user.
- It never changes a user's phone from a Respond.io sync (section 5.3).
- It never sends an email because a user was created or linked (6.4).
- It does link an existing user to the one contact with exactly the same phone, once, in the S0
  migration (AC-04). That is not creation and not new behaviour: `respond_link_service.py:25-48`
  already caches exactly this link at runtime today. The S0 PR lists every link it wrote so the
  owner can see them; any the owner rejects are unlinked with the S3 Unlink.
- It never links at runtime (S3 fix round 2, reviewer B1 at 6a7f0bcd): the runtime phone-match
  cache in `respond_link_service.py` re-linked a contact the owner had just unlinked on the next
  notification, SLA summary, banner or WhatsApp task. It is removed, and the phone fallback with
  it: every sender reaches a user only through `users.respond_contact_id`, so an unlinked user
  gets no WhatsApp until the owner links one (`tests/test_identity_s3_unlink_holds.py`).

### 6.6 Dealer contacts and every other portal contact (Q3, Q4)

A dealer contact is a portal contact like any other (ADR 0007: a dealer is a `customers` row, its
people are contacts). It gets a user only when the owner creates one, from Internal Users, with
the `portal_user` role suggested. Until then it keeps using the portal exactly as today: the same
link, the same WhatsApp code, its own portal token (section 4.3). No bulk creation, no creation at
sign-in. What it does on the portal is recorded against the contact (`actor_type = contact`,
section 8), as today.

### 6.7 The sales agent ruling

Superseded by #1280 (owner ruling 26 Sep 2026 23:45 MYT, Q2). S3 edits the `sales_agent.py`
docstring and adds a one-line supersession note to `PLAN-customer-sales-agent-assignment-24sep.md`.
`sales_agents` gains no `user_id`: the path agent -> contact -> user already exists and is one
join.

## 7. Permissions each kind of user gets

| Kind (derived, never stored) | How it gets a user | Roles at creation | CRM access | Portal access | Lands on |
| --- | --- | --- | --- | --- | --- |
| Staff | the owner or an admin creates it on Administrative Users (no email sent, 6.4) | as chosen, `is_default` suggested | per roles | only if a contact is linked | CRM home |
| Salesperson | the owner creates it from the contact on Internal Users, or links one (6.2) | `salesperson` suggested (protected, no permissions) | none (Q8) | the linked contact's forms (market segment base + overrides) | portal home (Q9) |
| Portal (dealer and others) | the owner creates it from the contact on Internal Users (6.2) | `portal_user` suggested (protected, no permissions) | none | the linked contact's forms | portal home |
| Contact with no user | never automatically | none | none | as today, by portal token | portal home |
| Integration | as today | as today | as today | none | n/a |

- **Portal access is not a permission.** It follows from having a linked contact, exactly as it
  follows from holding a token today, and the forms shown are the contact's (AC-33). No
  `portal.*` permission is added; the market segment grants and per-contact overrides stay the one
  source of truth.
- **The two roles are empty, and stay empty** (AC-45). Owner ruling 26 Sep 2026 23:45 MYT (Q8):
  "yeah now salesperson gets nothing other than portal submission". So `salesperson` carries no
  CRM permission, the grant sweep never adds one, and the "which project sales permissions"
  follow-up is closed: none. When a salesperson grows into project sales, the owner adds a
  Project Sales role to that one person, or permissions to `salesperson` for everyone at once;
  both are data changes, no code.
- **Kind is derived, never stored** (it is only a way to read this table): Integration if
  `is_integration`; else Salesperson if the user holds `salesperson`; else Portal if it holds
  `portal_user` and no other role; else Staff. No `user_type` column and, since round 4, no Kind
  filter (the Users list's existing role column tells them apart).
- **Company grants** are what the owner saves in the create form (prefilled from the contact's
  companies). A user with no grants scopes to zero rows (fail closed, as the portal does today);
  the Add user modal shows Companies as required when a contact is set, so the owner sees it.

## 8. The audit actor contract (for #1281)

This section is the contract #1281's analysis builds on. It says what every audited action
carries once S0 ships; which modules and functions must be audited at all is #1281's scope, not
this plan's.

### 8.1 Fields on every `audit_logs` row (AC-08)

| Field | Type | Meaning |
| --- | --- | --- |
| `actor_type` | text, NOT NULL for new rows (nullable column, backfilled `legacy`) | `user`, `contact` (a portal contact with no user), `integration`, `worker`, `scheduler`, `public_link`, `system` |
| `user_id` | existing | the effective actor (the user the action is attributed to); for a worker job, the user who enqueued it |
| `real_user_id` | new, nullable | the person actually at the keyboard: the impersonating admin, else equal to `user_id` |
| `auth_method` | new, nullable | `password`, `phone_otp`, `portal_link`, `portal_token` (a contact with no user, and a set-up person's old token until it expires, AC-36), `api_key`, `impersonation` |
| `session_id` | new, nullable | `user_sessions.id` (never the token) |
| `integration_id` | new, nullable | the `integrations` row behind an API key |
| `contact_id` | existing | the actor's linked contact (derived from the user when a user acts; set directly for a contact-only action such as a chatbot turn) |
| `job_id` | new, nullable | the RQ job id or scheduler task name |
| `ip_address`, `trace_id`, `company_id` | existing | unchanged |
| `user_agent` | new, nullable | truncated to 512 characters |

How each actor kind fills it:

| Actor | `actor_type` | `user_id` | `real_user_id` | `auth_method` | other |
| --- | --- | --- | --- | --- | --- |
| Staff / salesperson / portal user, signed in | `user` | self | self | session's method | `session_id`, `contact_id` if linked |
| Admin impersonating a user | `user` | target | admin | `impersonation` | `session_id` of the admin |
| Admin "view as contact" | `user` | the contact's user if any, else NULL | admin | `impersonation` | `contact_id` = contact (AC-11) |
| Portal token of a contact with no user (the owner has not set one up) | `contact` | NULL | NULL | `portal_token` | `contact_id` |
| Old portal token of a contact that now has a user (until it expires, AC-36) | `user` | the contact's user | same | `portal_token` | `contact_id` |
| Integration (n8n, MCP, AutoCount) | `integration` | act-as user | act-as user | `api_key` | `integration_id`; MCP's `X-Tool-Name` goes into `description` |
| RQ job | `worker` | enqueuing user or NULL | enqueuing real user | NULL | `job_id` |
| Scheduler tick | `scheduler` | NULL | NULL | NULL | `job_id` = task name |
| Anonymous public link (quotation sign, approval, onboarding intake) | `public_link` | NULL | NULL | NULL | the free-text name/email stays in `description` |
| Migration (the S0 link backfill, AC-04) | `system` | NULL | NULL | NULL | `description` names the migration |

### 8.2 How it is stamped (S0)

- `app/audit_context.py` gains the new fields; the session dependencies set them where they set
  the user today (`app/dependencies.py:222,273,306`), the API-key path sets `integration`, the
  portal principal sets its own.
- **Jobs carry their actor:** `enqueue` helpers pass `actor = {user_id, real_user_id, trace_id}`
  in the job meta, and one RQ job wrapper (the worker's `perform_job` hook) sets the audit context
  from it with `actor_type = worker`; scheduler handlers set `scheduler`. One place each, not per
  task.
- **The two suspected gaps are tested first** (AC-11, AC-12): a red test for each, then the fix
  (for the sync dependency: set the context in an async wrapper, or store the actor in
  `db.info` the way the portal contact already is, whichever the test proves works).
- **Impersonation** stops rewriting four column names by hand: `real_user_id` on the audit row
  says who was at the keyboard, and the `*_by` columns keep the effective user, which is what the
  screen shows today.
- **`api_call_log.actor`** (always NULL today) is filled with `user_id` and `integration_id` from
  the same context, so the API log and the audit log agree.

### 8.3 Rules for new code (added to PR-CHECKLIST in S0)

- A new "who did this" column is `<verb>_by_user_id`, String FK to `users.id` ON DELETE SET NULL.
  Never a Respond.io agent id, never a name or an email. Because contacts with no user keep using
  the portal (Q3, Q4 rulings), a column written from a portal route also gets
  `<verb>_by_contact_id` beside it, filled whenever the actor is a contact; the pair is what the
  six existing contact-typed columns already do.
- An action a contact takes without a user (chatbot turn, WhatsApp ingest) is recorded against the
  contact and, when the contact has a user, that user too; display resolves contact -> user.
- The ~150 existing actor columns are **not** migrated by this plan. #1281 decides, per module,
  which get a `*_by_user_id` counterpart; the contact -> user link this plan creates is what makes
  that backfill a join.
- **Display:** the audit screens render `actor_type` and `auth_method` as words (AC-13): "Aisyah
  (phone)", "Nurain on behalf of Aisyah", "Integration: n8n as Ops Bot", "Background job for
  Aisyah", "Scheduled: daily reorder run", "Portal: Aisyah's WhatsApp contact (no user)",
  "Public link". `legacy` rows render as today.

## 9. Migration path: zero downtime, no re-registration

Deploys are blue/green: the new container runs `alembic upgrade head` while the old image still
serves (`documentation/plans/portal/PLAN-portal-forms-market-segment.md` D4 is the precedent), so
every migration here is **expand only**, and every contract step is its own later release.

### 9.1 Pre-flight queries (S0, run on the prod copy, results pasted into the S0 PR)

1. Case-duplicate emails: `SELECT lower(email), count(*) FROM users GROUP BY 1 HAVING count(*) > 1`.
2. Contacts claimed by more than one user:
   `SELECT respond_contact_id, count(*) FROM users WHERE respond_contact_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1`.
3. Users whose `contact_number` matches exactly one contact but are unlinked (the link backfill
   set, AC-04; listed by name in the PR so the owner sees every link before S0 merges).
4. Salesperson contacts by rule (6.1), split into: already linked, phone matches one user, phone
   matches a user linked elsewhere, no match. This tells the owner how many people there are to
   set up on day one; nothing is created from it and no page lists it.
5. Contacts whose portal tokens span more than one `space_id` (the S2 space-derivation risk,
   section 11).
6. Every reader of `users.email` that would fail on NULL (code grep, listed in the PR).

A non-empty result for 1 or 2 stops S0 and goes to the owner as a list of names; nothing is
merged or unlinked automatically.

### 9.2 Releases

| Release | Expand (migration) | Code | Old image safe because |
| --- | --- | --- | --- |
| S0 | `users.email` DROP NOT NULL + check (email or phone); unique `lower(email)` index created concurrently beside the old one; unique partial index on `users.respond_contact_id`; `users.phone_verified_at`; `user_sessions.auth_method` (nullable, backfilled `password`); audit columns (nullable); roles `salesperson`, `portal_user` seeded, protected, empty; link backfill (9.1 query 3) | audit stamping; email lowercased on write | no row has a NULL email yet; new columns are nullable and ignored by the old image |
| S1 | none | phone request / verify routes; NextAuth `phone-otp` provider; Email / Phone toggle on the existing `/signin` | additive routes |
| S3 | none | Add user modal takes a contact, optional email and companies, and loses the invite checkbox; `POST /users/invite` removed; Internal Users "Create user" row action and User column; contact User account section; Link existing user; user Sign-in section; invite button behind a confirmation | users the owner creates may have a NULL email, which the old image's readers were guarded for in S0; during the swap the old image's modal can still call `/invite` for a few minutes, which is today's behaviour |
| S2 | none | portal principal from the user session; `has_user` on slug-info / token-info; verify card signs in for a set-up person; that person's old tokens stop sliding | the old image still resolves `X-Portal-Token`, and no token is revoked |
| Contract (S0 + one release, its own small PR) | drop the old case-sensitive email index | none | every write has lowercased email since S0 |

- **No re-registration** (AC-06): staff keep email + password; portal contacts keep their link and
  their code whether or not the owner has set them up; a person the owner sets up signs in with
  the same link or number they already use.
- **`X-Portal-Token` is not retired.** Contacts the owner has not set up keep using it
  indefinitely (Q3, Q4 rulings); only tokens of contacts that have a user stop sliding (AC-36).
  Named trigger for retiring it in section 11.
- **No forced sign-out** at any release: existing `user_sessions` rows stay valid; existing portal
  tokens stay valid until natural expiry.
- **Rollback:** every release is additive; rolling back the image leaves unused columns and
  extra `user_sessions` rows (the old image reads them as ordinary sessions, which they are).

## 10. Slices, each with its definition of done and UAC ids

Order is S0, then S1 and S3 in parallel, then S2. S4 (admin identity screens) is withdrawn by the
Q16 ruling of 27 Sep 2026 00:45 MYT ("i don't need a brand new page for user management"); the one
part of it that is still needed, the user's Sign-in section, moved into S3 (6.3). S3 moved ahead
of S2 in round 2:
with no automatic creation, the only users with a linked contact (the people S2 serves) are the
ones the owner creates in S3, plus the staff linked by the S0 backfill. S1 does not need S3
(staff with a phone can sign in by code), and S3 does not need S1 (a created user simply cannot
sign in by phone until S1). One lane per slice, one PR per lane; this plan rides in the S0 PR.
Every slice runs the full track and `security-reviewer` (AC-62).

### S0 Identity model, migration, audit fields

- Scope: section 4.1, 9.1, the S0 row of 9.2, section 8 in full, CLAUDE.md corrections (Prisma
  gone, staff tokens are opaque sessions, the `system` principal is replaced by integrations).
- UAC: AC-01 to AC-14, AC-60, AC-62.
- Done when: pre-flight results in the PR and clean (or owner-ruled), query 3 listed by name;
  migration applies on a clone of the prod copy and the previous image's suite passes against it
  (AC-05); red-then-green tests for AC-11 and AC-12 committed; audit screens show actor words,
  including `contact`, at 375px and 1280px; every `users.email` reader guarded with a test;
  single alembic head. Q5 is ruled (27 Sep 2026 00:20 MYT, phone-only users allowed), so AC-02
  stays and S0 carries no open question.

### S1 Phone sign-in

- Scope: sections 4.2, 4.3 (session part), 5.1, 5.3 (password and lost-phone parts). No new page:
  the Email / Phone toggle goes on the existing `/signin` (Q7, Q16, owner ruling 27 Sep 2026 00:45
  MYT), in its current design, reusing the portal's code sender and code input.
- UAC: AC-20 to AC-29.
- First task: read the approved `portal_otp` WhatsApp template's text; if it names the portal, ask
  the owner whether it may be reused for CRM sign-in or a `login_otp` template must be approved
  first (Meta approval lead time is the slice's longest pole). Answered (owner ruling 27 Sep 2026,
  on PR #1307): reuse `portal_otp`; no `login_otp` template goes to Meta.
- Done when: a staff user, an admin (Q10) and a phone-only user each sign in by code through the
  real worker and a real WhatsApp send on the lane stack; a user whose phone differs from its
  linked contact's is refused; enumeration tests green; Email mode is pixel-for-pixel today's page
  (a before/after screenshot pair at both widths in the PR); browser evidence at 375px (keyboard
  open) and 1280px.

### S3 The owner creates and links users

- Scope: sections 6 and 7, on the existing Administrative Users and Internal Users pages only
  (Q16, owner ruling 27 Sep 2026 00:45 MYT), and the no-email rule (6.4, Q17, owner ruling 27 Sep
  2026 00:50 MYT).
- UAC: AC-40 to AC-49, AC-52 to AC-59.
- Done when: the owner's flow runs end to end on the lane stack from the sidebar: Users & Access >
  People > Internal Users, a salesperson contact's row, Create user (prefilled, `salesperson`
  suggested), Add user, the row's User column shows the name; the same from Administrative Users >
  Add user by picking the WhatsApp contact; the two 409s shown inline with their offered action;
  Link existing user works from both ends; the email outbox is empty after creating a user with an
  email and after linking (AC-56); "Send invitation email" asks first and sends only on "Send
  email" (AC-57); a test proves no code path outside the create and link routes inserts a `users`
  row or changes a user's roles (AC-44); roles verified empty after the grant sweep (AC-45); the
  sales agent docstring and the 24 Sep plan carry the supersession note; no route or sidebar
  entry added (AC-59); evidence at both widths.

### S2 Portal on the unified session

- Scope: sections 4.3 (portal part), 5.2, the S2 row of 9.2.
- UAC: AC-30 to AC-39.
- First task: confirm whether NextAuth's `SessionProvider` wraps the `(auth)` group today; if not,
  mount it for `/portal` only.
- Done when: every portal form kind proven identical under both principals (AC-31); a set-up
  salesperson signs in from an old WhatsApp link and lands on the same portal home under a user
  session; a contact with no user signs in from its link exactly as before and still has no user
  afterwards (AC-35); a salesperson given a CRM role still lands on the portal and reaches the CRM
  by "Open CRM" with one sign-in (Q9); the set-up person's old token no longer slides; evidence at
  both widths.

### S4 Withdrawn

Owner ruling 27 Sep 2026 00:45 MYT (Q16): "i don't need a brand new page for user management".
The Kind filter and Sign-in column are dropped; the user Sign-in section, Unlink and the
phone-differs state moved to S3 (6.3). AC-50 and AC-51 are withdrawn.

## 11. Risks, out of scope, named triggers

### Risks

- **Space derivation.** Portal rows are owned by `(contact_id, space_id)`; a user session derives
  the space from the contact's workspace. A contact whose tokens were minted in a different space
  would lose sight of rows under the old space. Pre-flight query 5 measures it; if any exist, S2
  resolves the space from the contact's most recent verified token instead, and the plan is
  corrected before S2 starts.
- **Two portal ways in, for good.** With no automatic creation, the portal token stays the way in
  for every contact the owner has not set up, so `get_portal_principal` carries both branches
  indefinitely and AC-31 must keep proving them equal. This is the accepted cost of the Q3 / Q4
  rulings; the trigger to retire the token branch is below.
- **Unified sign-in covers only the people the owner sets up.** R1 ("one sign-in") holds for them;
  a salesperson the owner has not reached yet is still a portal-only contact, and what it does is
  audited as the contact, not a user (R4 is partial until the owner has set everyone up). The
  Internal Users "User" column makes the gap visible; the owner's pace closes it.
- **WhatsApp template outside the 24h window.** A code to someone who has not messaged Sorento in
  24 hours needs the approved template; a paused or rejected template stops every phone sign-in.
  Mitigation: email + password stays available to anyone with one; S1 logs every send to
  `integration_log` (AC-22) so a template failure is visible in the outbox the same day.
- **Worker down means no codes** (as it already does for the portal). The system health page
  already watches the worker; no new mechanism.
- **Phone sign-in for admins** (Q10, ruled yes). A WhatsApp code is weaker than a password for
  `superadmin` accounts (SIM swap). The owner accepted it; excluding admins later is one check in
  request-code. **For S1 (from the S3 security review, 27 Sep 2026):** once phone sign-in exists,
  a `user_management.users.edit` holder can set an admin's phone and link their own WhatsApp
  contact, then sign in as that admin by code. Not new privilege (`users.edit` can already assign
  any role), but it needs no email link. Recommended with S1: refuse `contact_number` /
  `respond_contact_id` edits on a protected, `superadmin` or `admin` user unless the caller is a
  superadmin.
- **A Respond.io phone change blocks a set-up person's phone sign-in** until the owner confirms the
  new number (5.3). Chosen over following the sync silently, because the code would otherwise go
  to a number nobody approved. Email + password, where the user has one, still works meanwhile.
- **Enumeration by timing.** request-code must do the same work for unknown numbers (enqueue
  nothing, but answer after the same lookup); the S1 security review checks it.
- **An email-only user can do nothing until the owner sends the invite** (Q17). Created INACTIVE
  with no password and no email sent, it cannot sign in until "Send invitation email". That is the
  owner's ruling working as intended; the Sign-in section shows "Invitation not sent" so it is not
  forgotten.
- **Removing `/users/invite` breaks an unknown caller.** Mitigated by the S3 grep of the FE, n8n
  exports and the MCP catalogue; if anything else calls it, the route stays and answers 410 with a
  message naming the replacement.

### Out of scope (with the trigger that would bring each in)

- **Automatic user creation** (backfill, sync hooks, first sign-in). Withdrawn by the Q3 / Q4
  rulings. Trigger: the owner asks for it, for example when there are more people to set up than
  they can do by hand.
- **Retiring `X-Portal-Token`.** Trigger: every contact that signed in to the portal in the last
  30 days has a user (a query the owner can be sent), or the owner decides to require a user for the portal.
- **SMS delivery.** Trigger: the first salesperson who must sign in and cannot receive WhatsApp,
  or `integration_log` showing a sustained template failure rate. Then: one SMS provider behind
  the same request-code route, chosen by the user's channel.
- **A separate `identities` / `principals` table.** Trigger: a person who legitimately needs two
  WhatsApp contacts (two numbers) under one user, or a login method that is not a user attribute
  (for example a third-party identity provider with several accounts per person).
- **Hashing session tokens at rest** (`user_sessions.token`, `portal_tokens.token` are plain
  text today). A real gap measured in section 3.2, orthogonal to unification; logged to the backlog
  as its own security lane.
- **Migrating the ~150 existing actor columns** to `*_by_user_id`. #1281's per-module decision.
- **Email sign-in by code (passwordless email).** Trigger: a user with email and no phone who
  cannot keep a password.
- **Self-signup.** The signup route exists but sends no email and is not linked from sign-in;
  nothing in #1280 asks for public self-registration, and the Q3 / Q4 rulings point the other way.
  Left as is.
- **A separate Salesperson accounts page, a quick-create form of its own, or any new user
  management page.** Withdrawn by the Q16 ruling (27 Sep 2026 00:45 MYT). Trigger: the owner asks.
- **Sending invitation or verification email automatically on create** (Q17). Trigger: the owner
  asks for an opt-in, and then it is a checkbox that starts unticked.
- **Dealers as tenants** (their own CRM): ADR 0007's flip condition; not this plan.
- **CRM permissions for salespeople.** None, by the Q8 ruling. Trigger: the owner names the
  project sales permissions a salesperson should have; then it is a data change to the
  `salesperson` role (or a second role on the person), no code.

## 12. Grill questions for the owner

Round 1 asked 15 questions with a recommendation each. Round 2 records the owner's answers of
26 Sep 2026 23:45 MYT, round 3 the answer to Q5 of 27 Sep 2026 00:20 MYT, and round 4 the owner's
notes of 27 Sep 2026 00:45 MYT (Q7, Q16) and 00:50 MYT (Q17) (PR #1285, verbatim in quotes); unanswered ones stay on their recommendation and the UAC is written to it until the
owner says otherwise.

1. **Who is a "product salesperson" and a "retail salesperson"?** Recommended: a contact in any
   market segment flagged "Requested by / Salesperson" selectable (today `retail` and `project`,
   "product" read as "project"), plus any contact linked to a sales agent. (AC-40)
   Owner ruling 26 Sep 2026 23:45 MYT: "yeah". Adopted as recommended.
2. **Confirm #1280 overrides the 14 Aug 2026 ruling that salespeople get no user account.**
   Owner ruling 26 Sep 2026 23:45 MYT: "yeah correct". S3 records the supersession (6.7).
3. **Dealer contacts: users up front, or at their first portal sign-in?** Recommended: at first
   sign-in. Owner ruling 26 Sep 2026 23:45 MYT: "hmm i should be able to consciously create and
   setup at the backend first, it should be controlled by me". **Recommendation reversed:**
   neither; the owner creates each user deliberately (6.2, 6.6). (AC-35, AC-41)
4. **Does every portal contact become a user when it next signs in?** Recommended: yes, silently.
   Owner ruling 26 Sep 2026 23:45 MYT: "no it shouldn't, it should be consciously by me".
   **Recommendation reversed:** no user is ever created by a sign-in, a backfill or a sync; a
   contact the owner has not set up keeps its portal token exactly as today. (AC-35, AC-44)
5. **May a user have no email (phone only)?** Recommended: yes, at least one of email or phone
   required; no placeholder emails. (AC-02) Not answered in round 2; re-asked on PR #1285.
   Owner ruling 27 Sep 2026 00:20 MYT: "identity plan q5 - yeah can". Adopted as recommended: a
   user may have no email (phone only), at least one of email or phone is required, and
   placeholder emails are never created. AC-02 stays; a WhatsApp-only salesperson is created in
   the owner's form (6.2) with a phone and no email.
6. **Code channel: WhatsApp only, or SMS too?** Owner ruling 26 Sep 2026 23:45 MYT: "yeap
   whatsapp codes". WhatsApp only; SMS stays behind its trigger (section 11). (AC-22)
7. **Sign-in screen: one "Email or phone number" field, or two tabs?** Recommended: one field.
   (AC-20) The owner's reply under this number, "hmm we can just link the user to respond contact
   right?", is a question about the design rather than the screen; it is answered on PR #1285
   ("Answers to the owner's questions (round 2)"): yes, that link is the design
   (`users.respond_contact_id`, section 4.1), and it is what lets a WhatsApp code sign a user in,
   but the link alone does not sign anyone in.
   Owner ruling 27 Sep 2026 00:45 MYT, on the one-field sketch: "i just need 1 sign in page which
   is https://fe-sorento.foundryx.my/signin with option to sign in by phone number" and "make sure
   i can toggle between phone auth and email pw auth in the sign in page while obeying the design
   element of the current sign in page". **Recommendation reversed:** the existing `/signin` gets
   an Email | Phone toggle in its current design; the one-field guesser and the six code boxes are
   withdrawn (5.1). (AC-20, AC-25, AC-29)
8. **What can a salesperson user do in the CRM on day one?** Owner ruling 26 Sep 2026 23:45 MYT:
   "yeah now salesperson gets nothing other than portal submission". The `salesperson` role is
   protected and empty, and the follow-up ("which project sales permissions") is closed: none.
   (AC-45)
9. **Where does someone land after signing in?** Owner ruling 26 Sep 2026 23:45 MYT: "hmm for now
   salesperson still land at portal first". A user holding `salesperson` lands on its portal home
   even if it later gains CRM permissions (it reaches the CRM by "Open CRM"); other users with a
   CRM permission land on the CRM home; a `callbackUrl` the user may open wins. (AC-28, AC-39)
10. **Phone sign-in for staff and admins too?** Owner ruling 26 Sep 2026 23:45 MYT: "yeah
    correct". Every ACTIVE user with a verified phone that matches its linked contact, admins
    included. (AC-27)
11. **Old portal sessions: keep forever or phase out?** Not answered; recommendation revised for
    the Q3 / Q4 rulings: a contact with no user keeps its sliding portal token indefinitely; a
    contact the owner has set up signs in to the unified session and its old tokens stop sliding,
    so they end within 30 days. Old WhatsApp links keep working for everyone. (AC-36)
12. **Admin "view as contact": keep it as is?** Not answered; recommendation stands: keep it, and
    fix the audit so its actions are recorded as the admin on behalf of the contact. (AC-11)
13. **Lost phone: who fixes it?** Not answered; recommendation stands: an admin changes the
    number on the user, which signs that user out everywhere; the next sign-in verifies the new
    number. (AC-54, section 5.3)
14. **A salesperson contact whose phone already belongs to a staff user: same person?** Not
    answered; recommendation revised for the Q3 / Q4 rulings: the create form refuses a second
    user for that phone and offers "Link this contact to <name> instead"; the owner decides,
    nothing links by itself. (AC-42, AC-43)
15. **How long does a phone or portal sign-in last?** Not answered; recommendation stands: 30
    days, extended while in use. (AC-24)
16. **(Raised by the owner on the alignment page, 27 Sep 2026 00:45 MYT.) New pages?** Owner
    ruling, verbatim: "just use our existing user management page, don't need new page"; "there
    is no need for this, I will just create the user account and link to the contact, i don't even
    want to send email verification to these ppl at this stage, you can maybe help me to quick
    create from respond contact la, but i don't need another page called salesperson account, just
    need existing Administrative Users and Internal contacts"; "i don't need a brand new page for
    user management"; "for salesperson, they will still access via portal which will prompt them
    to enter verification code when needed"; "we already got this send otp mechanism so just reuse
    it". So: the Salesperson accounts worklist and S4 are withdrawn; creating and linking happen in
    the existing Add user modal, Edit profile dialog and a new User account section on the
    existing contact page, with a "Create user" row action on Internal Users as the quick create
    from a Respond contact (6.2); salespeople keep the portal and its code prompt (5.2); phone
    sign-in reuses the portal's code sender (5.1). (AC-48, AC-53, AC-59)
17. **(Raised by the owner, 27 Sep 2026 00:50 MYT.) Does creating a user send an email?** Owner
    ruling, verbatim: "cause i think now if we create the user with email, it will send email
    verification right? can this be a manual step ah cause i worry will accidentally send the
    email to the salespersons when we create for them, this one you also need to update in the
    lavish". Answer: yes, today it does by default (section 3.2). So: creating or linking never
    sends any email; the Add user checkbox and `POST /users/invite` are removed; sending is the
    "Send invitation email" button on the user, behind a confirmation, off by default (6.4).
    (AC-41, AC-56 to AC-58)
18. **When an admin replaces a user's existing email, keep the security notice to that user?**
    Not asked yet. **Recommended: keep it.** It goes only to someone who already has an account and
    tells them their sign-in email changed; it is not an invite and it never fires on create, on
    link, or when a phone-only user gets a first email. If the owner says no, the notice is removed
    too and nothing about a user's email is ever mailed without the invite button. (6.4)
