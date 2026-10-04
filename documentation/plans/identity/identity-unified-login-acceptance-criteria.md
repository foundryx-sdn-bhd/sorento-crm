# UAC: Unified identity, one login for the portal and the CRM (#1280)

Plan: `PLAN-unified-identity-26sep.md` (same folder).
Status: draft, round 4 (27 Sep 2026). Rewritten for the owner rulings of 26 Sep 2026 23:45 MYT on
Q1, Q2, Q3, Q4, Q6, Q8, Q9, Q10, 27 Sep 2026 00:20 MYT on Q5, 27 Sep 2026 00:45 MYT on Q7 and Q16
(no new pages; the existing `/signin` with an Email / Phone toggle) and 27 Sep 2026 00:50 MYT on
Q17 (creating or linking never sends email) (plan section 12). Round 4 withdraws AC-50 (the
Salesperson accounts worklist) and AC-51 (the Users list Kind filter and Sign-in column), moves
AC-52 and AC-54 from S4 to S3 (S4 is withdrawn), and adds AC-29 and AC-56 to AC-59. Q11 to Q15
and Q18 are unanswered; ACs that depend on them still follow the recommendation and name it as `(Qn)`, to be
rewritten before their slice starts if the owner answers differently.

Owner ruling 26 Sep 2026 23:45 MYT (Q3, Q4), binding on every AC below: no user is ever created
automatically. The owner creates and sets up every user at the backend (create, link to the
contact, set roles), then the person signs in. A contact the owner has not set up keeps using the
portal exactly as today.

Owner rulings 27 Sep 2026 00:45 MYT (Q7, Q16) and 00:50 MYT (Q17), binding on every AC below: no
new page (creating and linking happen in the existing Administrative Users and Internal Users
pages, the owner's "Internal contacts"); one sign-in page, the existing `/signin`, with a toggle
between phone code and email plus password in its current design, reusing the existing code
sender; salespeople keep the portal and its code prompt; creating or linking a user never sends
any email, and sending the invitation email is a separate button with a confirmation.

Owner words, 26 Sep 2026 ~13:50Z, issue #1280 (verbatim, binding):

> "I think eventually all our portal and user sign in needs to be unified. Because the one who use
> the portal for basic portal submission function will grow to use our project sales modules, use
> our sales opportunities module. So I think we need to find a way to unify. I'm thinking that we
> should support like user login, user email and password login, or also a phone number login. We
> should unify this. So for all the contacts that are like for product salesperson, retail
> salesperson, we also need to create like a counterpart user for them so in the future all of
> them can use our system and be audit trailed by our system."

Tags: `[BE]` backend, `[FE]` frontend, `[E2E]` browser pass (agent-browser, sidebar navigation
from `/`, 375px AND 1280px), `[T]` a named automated test (pytest on Postgres or vitest).

## Journey

Screens are designed at 375px first (a salesperson's phone), then checked at 1280px.

### J-A A retail salesperson signs in on her phone (WhatsApp only, no email)

1. Aisyah is a retail salesperson contact. She already exists as a WhatsApp contact. The owner
   has created her user from her contact (J-E): linked to her contact, role Salesperson, her
   companies. She did nothing.
2. She taps the portal link she already has on WhatsApp (salespeople keep the portal, Q16). The
   portal's verify card she knows asks for a code only when her phone holds no live sign-in.
3. She receives the code on WhatsApp, enters it in the one "Verification code" box, and lands on
   the portal home she already knows (a salesperson lands on the portal first, Q9), with the forms
   her market segment grants. She never chose a password, never registered, never got an email.
4. What she submits is recorded against her user (and her contact, as today).

### J-B A staff member signs in on the existing sign-in page

1. Staff open the same `/signin` they use today. The form is exactly today's (Email, Password,
   Forgot Password?, Remember me, Continue); under it a divider reads "or Log in with" above one
   [Superseded by SIGNIN-ALWAYS-SLIDE, 30 Sep 2026: the Remember me checkbox is removed; every sign-in is the 30-day sliding session. See `signin-always-slide-30sep-acceptance-criteria.md`.]
   round phone button.
2. A staff member who prefers the phone taps the phone button, types their number (any common form:
   `012-345 6789`, `+60123456789`, `60123456789`), taps Continue, gets a WhatsApp code, types it in
   the one "Verification code" box, and is in on the sixth digit.

### J-C An existing portal contact taps an old WhatsApp link

1. A contact taps the `/portal/c/{slug}` link they received months ago.
2. The verify card they know appears (their name, masked number); the code is sent to WhatsApp.
3. They enter the code and land on the same portal home, same forms, same submissions.
4. Behind it: if the owner has set up a user for this contact, a normal signed-in user session
   starts (the same one the CRM uses). If not (most dealer contacts), it is today's portal
   session, unchanged, and no user is created (Q3, Q4).
5. No re-registration, no new link, no password, either way.

### J-D A project salesperson grows into the CRM (later, when the owner decides)

1. Today a salesperson gets nothing beyond portal submission (Q8). When the owner decides one
   should, the owner adds a CRM role (for example Project Sales) to that salesperson's user.
2. The salesperson still lands on the portal first (Q9), now with an "Open CRM" link in the portal
   header; the CRM shows Project Sales in the sidebar and a "Portal" entry back. Same sign-in,
   same session.

### J-E The owner sets a person up on the existing pages, then the person signs in

1. The owner opens Users & Access > People > Internal Users (the WhatsApp contact list they
   already use). Aisyah's row has an empty "User" column and a row action "Create user".
2. Create user opens the existing Add user modal with her WhatsApp contact locked, her name and
   phone filled in, role Salesperson suggested and her companies filled in. There is no "send
   invitation" tick box any more. The owner reviews, changes anything, and clicks Add user.
3. Her row's User column now shows her name; her contact's Profile tab shows her user in a "User
   account" section. No email and no WhatsApp message went out.
4. The same works from Administrative Users > Add user: picking her in the new WhatsApp contact
   field fills the rest.
5. The owner may send her the portal link with the existing "Send portal link" action, or simply
   let her use the link she has. Nothing is sent by itself.
6. For a dealer contact, the owner does the same, with role Portal suggested. For a staff member
   who is also a salesperson, the owner uses Link existing user on the contact, or the WhatsApp
   contact field in the staff member's Edit profile, instead of creating a second user.

### J-E2 The owner sends an invitation email, on purpose

1. Daniel, a staff member, was created with an email and no phone. He cannot sign in yet, and his
   Sign-in section says "Invitation not sent".
2. The owner opens Daniel on Administrative Users and clicks "Send invitation email". A
   confirmation asks "Send invitation email?" and names the address. Cancel closes it with nothing
   sent; "Send email" sends the existing invitation link.
3. The same Sign-in section shows email, phone (verified or not), linked WhatsApp contact, last
   sign-in and how, and "Needs attention" when the contact's phone has changed.

### J-F Recovery

1. Forgot password: the existing email reset, unchanged.
2. Lost access to WhatsApp: an admin changes the phone on the user; every session of that user
   ends; the next phone sign-in verifies the new number by its own code.

## S0 Identity model, migration and audit fields

- **AC-01 [BE][T]** One principal per person: a person who signs in to the CRM or the portal is a
  `users` row. A WhatsApp contact links to at most one user (`users.respond_contact_id` unique
  where set). A second user linking the same contact is rejected with 409 `CONTACT_ALREADY_LINKED`
  naming the other user's name (never its id).
- **AC-02 [BE][T]** A user may have no email when it has a phone (Q5, owner ruling 27 Sep 2026
  00:20 MYT: "identity plan q5 - yeah can"): `users.email` is nullable, and a database check
  requires at least one of `email` or `contact_number`. No placeholder email is ever generated.
- **AC-03 [BE][T]** Email uniqueness is case-insensitive: creating or updating a user whose email
  differs only by case from another's is rejected with 409; sign-in with any casing finds the
  user. The migration's pre-flight lists any existing case-duplicates and stops (does not merge
  them silently).
- **AC-04 [BE][T]** Backfill links existing users to contacts: every existing user with no
  `respond_contact_id` whose normalised `contact_number` equals exactly one contact's
  `phone_number` gets linked (the link `respond_link_service` already caches at runtime today);
  ambiguous or already-claimed matches are left unlinked and listed. The S0 PR lists every link by
  name. It creates no user and changes no role. Re-running the backfill changes nothing.
- **AC-05 [BE][T]** Zero downtime: the S0 migration is expand-only (new nullable columns, new
  indexes created concurrently, relaxed NOT NULL), and the previous image's test suite still
  passes against the migrated schema.
- **AC-06 [BE][T]** Existing sign-in is unchanged: every existing user signs in with the same
  email and password after the migration; existing CRM sessions and existing portal tokens keep
  working without a new sign-in.
- **AC-07 [BE][T]** Every session row records how it was created: `user_sessions.auth_method` is
  one of `password`, `phone_otp`, `portal_link` (a portal verify card), `impersonation`.
- **AC-08 [BE][T]** Audit actor contract: every `audit_logs` row written after S0 carries
  `actor_type` (`user`, `contact`, `integration`, `worker`, `scheduler`, `public_link`, `system`), and, where
  they exist, `user_id` (the effective actor), `real_user_id` (the impersonating admin, else equal
  to `user_id`), `auth_method`, `session_id`, `integration_id`, `contact_id` (the actor's linked
  contact), `ip_address`, `user_agent`, `trace_id`, `company_id`.
- **AC-09 [BE][T]** An integration write names the integration: a write through an API key has
  `actor_type = integration`, `integration_id` set, and `user_id` = the integration's act-as user.
- **AC-10 [BE][T]** A background job keeps its user: an RQ job enqueued by a signed-in user writes
  audit rows with `actor_type = worker` and `user_id` = the enqueuing user; a scheduler tick
  writes `actor_type = scheduler` and no user.
- **AC-11 [BE][T]** Admin "view as contact" is credited to the admin: a portal write made through
  an impersonation token has `real_user_id` = the admin and `contact_id` = the contact (confirms,
  then fixes, the gap measured in plan section 3.5).
- **AC-12 [BE][T]** The actor survives a synchronous dependency: a write through a route using
  `get_current_user_or_api_key` records the caller's `user_id` (confirms, then fixes, the
  suspected context loss in plan section 3.5).
- **AC-13 [FE][T]** The audit screens render the new fields as words: actor kind ("Staff",
  "Integration: n8n", "Background job for Aisyah", "Scheduled", "Portal: <contact name> (no
  user)"), "on behalf of" for impersonation, and the sign-in method; no UUID is visible.
- **AC-14 [BE][T]** A portal write by a contact with no user records `actor_type = contact`,
  `auth_method = portal_token`, `contact_id` = the contact and no `user_id` (Q3, Q4: such
  contacts keep using the portal).

## S1 Phone sign-in

- **AC-20 [FE][E2E]** One sign-in page, the existing `/signin` (Q7, Q16, owner ruling 27 Sep 2026
  00:45 MYT). The email form shows first, today's fields in today's order, unchanged; under its
  Continue a divider reads "or Log in with" above one round phone icon button (tooltip "Phone
  number"), with no Email | Phone toggle and no Facebook, Google or Sign Up entry (fix round 1,
  owner hand test 29 Sep 2026). The phone button swaps the card body to the phone flow, which
  carries a "Back to email" link. The phone flow shows "Phone number" and Continue; Continue requests the code and swaps in the code step in the same
  card. No new route is added for sign-in. Usable at 375px with the keyboard open (code input and
  resend button reachable without scrolling) and at 1280px.
- **AC-21 [BE][T]** `POST /api/v1/auth/phone/request-code` with a phone number always answers
  200 with the same body, whether or not the number belongs to anyone (no enumeration). A code is
  sent only when the number resolves to exactly one ACTIVE, non-integration user with a linked
  WhatsApp contact whose phone equals the user's phone. A contact with no user never gets a
  sign-in code from this route, and no user is created (Q4).
- **AC-22 [BE][T]** The code goes to WhatsApp only (Q6, owner ruling 26 Sep 2026 23:45 MYT) through the existing `respond_io` queue and the
  portal OTP template, and every send writes an `integration_log` row on success and failure.
- **AC-23 [BE][T]** Limits: 10-minute expiry, 60-second resend cooldown, 5 wrong attempts per
  code, 10 sends per number per day, and the existing per-IP limit, all enforced server-side;
  hitting one answers 429 with the seconds to wait.
- **AC-24 [BE][T]** `POST /api/v1/auth/phone/verify` with the right code creates a `user_sessions`
  row (`auth_method = phone_otp`, 30-day rolling, Q15) and returns the same shape as email login,
  so NextAuth stores it the same way. It stamps `users.phone_verified_at`.
- **AC-25 [FE][E2E]** The code step reuses the portal's code input (Q16: "we already got this send
  otp mechanism so just reuse it"): one "Verification code" input (numeric, `one-time-code`,
  placeholder "6-digit code"), signs in on the sixth digit with no submit button, an outline
  resend button counting down from 60, and a "Change number" link. The portal verify card and
  `/signin` import the same component (a vitest renders both and finds it).
- **AC-26 [BE][T]** Email login no longer reveals whether an email exists: an unknown email and a
  wrong password return the same 401 body.
- **AC-27 [BE][T]** Phone sign-in works for staff and admins too (Q10, owner ruling 26 Sep 2026
  23:45 MYT): a staff user and a `superadmin` with a linked phone each sign in by code and get
  their normal CRM session and permissions.
- **AC-28 [FE][E2E]** After sign-in the user lands where they work (Q9, owner ruling 26 Sep 2026
  23:45 MYT): a `callbackUrl` the user may open wins; otherwise a user holding the `salesperson`
  role lands on its portal home even if it also holds CRM permissions; any other user with a CRM
  permission (or admin) lands on the CRM home; a user with none lands on its portal home.
- **AC-29 [FE][E2E]** The phone entry obeys the current sign-in design (Q16): the same card, wordmark,
  heading, `Input`, `Button`, `Checkbox` and destructive `Alert` as today, no new colours, fonts
  or illustrations, no explanatory copy. The S1 PR carries a before/after screenshot pair of Email
  mode at 375px and 1280px showing no change beyond the divider and phone button below the form.

## S2 Portal on the unified session

- **AC-30 [BE][T]** Every portal route accepts a signed-in user session: the portal principal
  (contact, space) is derived from the user's linked contact and that contact's workspace. A user
  with no linked contact gets 403 `NO_PORTAL_CONTACT`.
- **AC-31 [BE][T]** Ownership is unchanged: a user session sees exactly the submissions its
  linked contact saw under a portal token (same list, same 404 / 403 `OWNER_MISMATCH` answers),
  proven by one test per portal form kind comparing the two principals.
- **AC-32 [BE][T]** Company scope is unchanged for portal routes: a user session on a `/public/`
  portal route is scoped to the linked contact's companies, not the user's CRM company grants.
- **AC-33 [BE][T]** Form visibility is unchanged: the forms a user sees on the portal are the
  forms its linked contact sees today (market segment base plus per-contact overrides).
- **AC-34 [FE][E2E]** For a contact the owner has set up (linked to an ACTIVE user), the verify
  card on `/portal/c/{slug}` and `/portal?token=` signs the person in to the unified session:
  after the code, the NextAuth session exists, and opening a CRM page they have permission for
  needs no second sign-in. `slug-info` and `token-info` carry `has_user` so the card picks the
  path; the server refuses the unified path for a contact with no user (same 401 as a wrong code).
- **AC-35 [BE][T]** A portal contact with no user is never given one (Q3, Q4, owner ruling 26 Sep
  2026 23:45 MYT): its verify step mints a portal token exactly as today (7-day link token,
  30-day sliding verified token, same limits), and the `users` row count is unchanged after it
  signs in, re-verifies, or submits.
- **AC-36 [BE][T]** Portal tokens (Q11): a token of a contact with no user keeps sliding as today,
  with no end date. From the S2 deploy, a token of a contact that has a user no longer slides, so
  it ends within 30 days, and that person's new verifications mint a user session, not a token.
  Admin "view as contact" tokens are unchanged (Q12).
- **AC-37 [BE][T]** Submissions made on a user session store the same `contact_id`, `space_id` and
  `respond_inbox_url` as before, and the audit row carries the user (AC-08).
- **AC-38 [FE][E2E]** Signing out from the portal ends the user session everywhere that session
  was used (portal and CRM); the user's own device list (`account/security/current-sessions`)
  shows portal sign-ins with their method.
- **AC-39 [FE][E2E]** A user with both CRM and portal access has a "Portal" entry in the user menu
  and an "Open CRM" link in the portal header; a portal-only user who opens a CRM URL lands on
  their portal home, not an empty CRM shell.

## S3 The owner creates and links users, on the existing pages

- **AC-40 [BE][T]** A salesperson contact is (Q1, owner ruling 26 Sep 2026 23:45 MYT): a contact in
  any market segment with `is_requestor_selectable = true` (today `retail` and `project`), or a
  contact linked from `sales_agents.contact_id`. One function; it only decides which role the Add
  user modal suggests. It creates nothing and lists nothing.
- **AC-41 [BE][T]** The owner creates a user from a contact: `POST
  /api/v1/user-management/users` accepts `respond_contact_id`, an optional `email` (Q5) and
  `company_ids`; the saved user is linked to the contact, its phone is the contact's phone, its
  roles and companies are exactly what the owner submitted, status ACTIVE when it has a phone,
  INACTIVE with no password when it has only an email. No email is sent in either case (Q17,
  AC-56).
- **AC-42 [BE][T]** One person, one user, decided by the owner (Q14): creating a user whose phone
  equals an existing user's answers 409 `PHONE_BELONGS_TO_USER` naming that user (never its id),
  and creates nothing; linking the contact to that existing user instead succeeds and adds no
  role.
- **AC-43 [BE][T]** A contact already linked answers 409 `CONTACT_ALREADY_LINKED` naming its user;
  linking a user already linked to a different contact is refused the same way. Nothing is
  unlinked or merged by itself.
- **AC-44 [BE][T]** Nothing creates a user or changes its roles except the owner's create, edit and
  link actions: adding a contact to a salesperson segment, linking it to a sales agent, removing
  it from every salesperson source, a Respond.io sync, a portal verify and a phone sign-in each
  leave the `users` and `user_role_assignments` row counts unchanged (one test per source).
- **AC-45 [BE][T]** A salesperson gets nothing beyond portal submission (Q8, owner ruling 26 Sep
  2026 23:45 MYT): the `salesperson` and `portal_user` roles are protected, seeded with no CRM
  permissions, never the `is_default` role, and the grant sweep leaves them empty. A user holding
  only `salesperson` gets 403 on every CRM route and sees the portal forms of its contact.
- **AC-46 [BE][T]** A contact's phone change (Respond.io sync or admin edit of the contact) does not
  change the linked user. While the two phones differ, phone sign-in for that user is refused and
  the user is flagged "Needs attention" (AC-52); the owner's "Use new number" updates the user's
  phone, clears `phone_verified_at` and ends its sessions (AC-54).
- **AC-47 [BE][T]** Every create and link is audited with the acting admin (`real_user_id`) and
  names the contact it came from.
- **AC-48 [FE][E2E]** The existing Add user modal (Q16, owner ruling 27 Sep 2026 00:45 MYT) gains
  a WhatsApp contact field (`SearchableSelect`, name and phone, never an id): clearable when opened
  from Administrative Users > Add user, locked when opened from a contact. Picking a contact fills
  Name, Contact Number (then read-only) and Companies, and suggests `salesperson` for a
  salesperson contact or `portal_user` for any other, without overwriting a field the owner has
  typed in. Email is optional when there is a phone (Q5). The "Send invitation email" checkbox is
  gone (AC-58). Each 409 shows inline with its action ("Open user", "Link this contact to <name>
  instead"). Usable at 375px and 1280px.
- **AC-49 [BE]** S3 records the supersession of the 14 Aug 2026 "no user account for salespeople"
  ruling (Q2, owner ruling 26 Sep 2026 23:45 MYT) in the `sales_agent.py` docstring and
  `PLAN-customer-sales-agent-assignment-24sep.md`.
- **AC-50** Withdrawn, round 4 (owner ruling 27 Sep 2026 00:45 MYT, Q16: "i don't need another
  page called salesperson account"). No Salesperson accounts page.
- **AC-51** Withdrawn, round 4 (Q16: "i don't need a brand new page for user management"). The
  Users list is not changed: no Kind filter, no Sign-in column.
- **AC-52 [FE][E2E]** Administrative Users > a user > Profile tab: a read-only "Sign-in" section
  (moved from S4, which is withdrawn), with email or "No email", phone and its verified state,
  linked WhatsApp contact (name and masked phone, a link to the contact) or "Not linked", last
  sign-in time and method, "Invitation not sent" for an email-only user that has never been
  invited, and "Needs attention: phone differs from WhatsApp contact" with "Use new number"
  (AC-46); explicit empty states ("No phone yet. Add one to allow phone sign-in."). Editing stays
  in the existing Edit profile dialog, fields in the same order.
- **AC-53 [FE][E2E]** Internal Users > a contact > Profile tab: a "User account" section showing
  the linked user, or "No user yet" with Create user (AC-48, contact locked) and Link existing user
  for an admin; Unlink is a 5-second deferred action with Cancel (D7), never a dialog. The user's
  Edit profile WhatsApp contact field (it exists today) links from the other side with the same
  409 rules.
- **AC-54 [BE][T]** Unlinking, or changing a user's phone or linked contact, revokes every session of
  that user (the same path as force-logout) and writes an audit row. None of them sends an email.
- **AC-55 [BE][T]** Every new or extended admin route is permission-gated with the permissions that
  exist today: `user_management.users.add` to create (as `POST /users` checks now),
  `user_management.users.edit` for link, unlink, phone edits and sending the invitation; the
  Internal Users "User" column needs `user_management.users.view` and is hidden without it. Each
  has an auth-denial test. No new permission is added.
- **AC-56 [BE][T]** Creating or linking a user never sends an email (Q17, owner ruling 27 Sep 2026
  00:50 MYT): creating a user with an email, with a phone, and with both; linking and unlinking a
  contact; editing roles or companies; and adding a first email to a phone-only user each leave
  the `email_outbox` and notification row counts unchanged (one test per action). Nothing sends a
  WhatsApp message either.
- **AC-57 [FE][BE][E2E][T]** Sending the invitation is one deliberate button with a confirmation,
  off by default (Q17): the user record action reads "Send invitation email", is hidden for a user
  with no email, and opens a dialog "Send invitation email?" naming the address, with Cancel
  (focused) and "Send email". Cancel, Escape or closing sends nothing; only "Send email" calls
  `POST /users/{id}/resend-invite`. The Users list bulk "Resend invitation" keeps its confirmation,
  skips users with no email and reports how many it skipped.
- **AC-58 [FE][BE][T]** The create-and-email path is gone: the Add user modal has no "Send
  invitation email" checkbox and posts only to `POST /users`; `POST /users/invite` and its Next
  proxy are removed after a grep of the FE, n8n exports and the MCP catalogue finds no other caller
  (if one is found the route answers 410 instead). A test asserts `/invite` no longer creates a
  user.
- **AC-59 [FE][E2E]** No new page (Q16): the lane adds no route under `app/(protected)` and no
  sidebar entry. The quick create from a Respond contact is the Internal Users row action "Create
  user" (shown only on a contact with no user) opening the prefilled Add user modal, plus the
  WhatsApp contact field in Administrative Users > Add user. Internal Users gains one column,
  "User" (the linked user's name, a link; blank when none), with an explicit size and truncate +
  title.

## Cross-cutting

- **AC-60 [T]** No em-dash or en-dash in any file this lane adds or edits.
- **AC-61 [E2E]** Every changed screen (the existing `/signin`, Administrative Users, Internal
  Users, the contact and user pages, the portal verify card) is verified by agent-browser at 375px and 1280px, reached by
  sidebar clicks from `/` (the portal from its WhatsApp link), with evidence under
  `documentation/plans/identity/evidence/<slice>/`.
- **AC-62 [BE]** `security-reviewer` runs on every slice of this plan (auth, RBAC and portal ingest
  are all touched).
