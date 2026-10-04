# S3 browser-verification evidence (identity, #1280)

Date: 2026-09-26 (UTC). Branch `claude/identity-s3-create-link-users-6hibzg`, walked on
`645c5f9c` (the browser-pass fixes below are in it); S0 fix round 2 merged in afterwards at
`62065d06` (backend only, no screen change; the S3 and S0 suites re-run green after it).
Scope: AC-48, AC-52, AC-53, AC-57, AC-59 and the S3 done-when (plan section 10), against the
round 4 mockups 5 to 13 of `alignment-unified-identity-27sep.html`.

## Environment

- Cloud VM Postgres, a private database `sorento_s3_browser` bootstrapped by
  `scripts.bootstrap_env` (schema at `identity_0001_s0_model`), so the walk never shared rows
  with a test run (the S0 walk lost its seed to one).
- Backend `venv/bin/uvicorn app.main:app --reload` on :8000; frontend `npm run dev` on :3000,
  `.env.local` (gitignored) pointing `FASTAPI_INTERNAL_URL` at the backend.
- agent-browser 0.27.0, isolated `--session s3`, Playwright Chromium at
  `/opt/pw-browsers/chromium-1194`.
- Seed (scratch script, not committed): owner login (superadmin); companies Sorento and
  Sorento Penang; workspace Sorento Main; market segments `retail` and `project`, both
  requestor-selectable; contacts Aisyah Rahman (retail, two companies, no user), Daniel Lim
  (retail, linked to salesperson user Daniel with an email and no password), Farah Aziz (whose
  phone equals staff user Farah's), Kumar Raj (contact of sales agent KUMAR, no segment, no
  user), Siti Noor (project, linked to a phone-only user whose phone differs from the
  contact's); email-only user Nurain Hashim, never invited; module `base` installed for the
  default tenant.

## Navigation

Every screen reached from `/` by sidebar: Users & Access > People > Internal Users
(`/user-management/contact-access-agents`) or > Administrative Users
(`/user-management/users`); records opened by row click. No route or sidebar entry was added
(AC-59): the sidebar shows the same four People entries as before.

## Checks and results

| # | Width | Check | Result | Evidence |
| --- | --- | --- | --- | --- |
| 1 | 1280 | Internal Users shows the User column (name as a link, `-` when none) | PASS | `iu-after-1280.png` |
| 2 | 1280 | Aisyah's row menu shows Create user first; Daniel's (linked) has none | PASS | snapshot: `Create user`, `Impersonate in portal`, `Delete contact` |
| 3 | 1280 | Create user opens the Add user modal: contact locked, name, phone (read-only), both companies filled, Salesperson suggested, no invitation checkbox | PASS | `add-from-contact-1280.png` |
| 4 | 1280 | Add user with nothing typed: `POST /api/v1/user-management/users` 201, toast "User added", the row's User column shows Aisyah with no reload | PASS after fix A | `iu-after-1280.png`; DB: ACTIVE, no email, no password, role salesperson, 2 companies |
| 5 | 1280 | Nothing sent by the create: `email_outbox` 0, `notifications` 0; audit row UPDATE "Created from WhatsApp contact Aisyah Rahman" with the owner as `real_user_id` | PASS | DB queries |
| 6 | 1280 | Siti's Sign-in section: No email, masked phone "not verified yet", WhatsApp contact link, Last sign-in Never, "Needs attention: phone differs from WhatsApp contact." with Use new number, Unlink | PASS | `signin-siti-1280.png` (mockup 13) |
| 7 | 1280 | Use new number: `PUT` 200, phone becomes the contact's, the warning goes | PASS | network + snapshot |
| 8 | 1280 | Contact with no user: "No user yet", Create user, Link existing user | PASS | `contact-nouser-1280.png` (mockup 9) |
| 9 | 1280 | Link existing user: the picker lists only active users with no contact (Farah, Jayson, Siti; not Daniel, Aisyah or inactive Nurain); Link sets it; outbox and notifications stay 0 | PASS after fix B | snapshot + DB |
| 10 | 1280 | Unlink shows "Unlinking in Ns" with Cancel, no dialog; Cancel inside the window leaves the link | PASS | `unlink-countdown-1280.png` (mockup 10); DB still linked |
| 11 | 1280 | Unlink left to run commits after the window; the section returns to "No user yet"; audit row "Unlinked WhatsApp contact Siti Noor" names the owner | PASS | DB |
| 12 | 1280 | Administrative Users > Add user, pick Farah's contact: name, phone, companies fill, Portal suggested; Add user gives the inline 409 "This phone already belongs to Farah Aziz" with "Link this contact to Farah Aziz instead" | PASS | `add-409-1280.png` (mockup 6) |
| 13 | 1280 | Link instead: Farah's user is linked, still one Farah, her roles unchanged (no Portal added), outbox 0 | PASS | DB |
| 14 | 1280 | Nurain (email only, never invited): "Invitation not sent" and the Send invitation email button | PASS | `signin-nurain-1280.png` (mockup 11) |
| 15 | 1280 | The confirmation reads "Send invitation email?" / "An email with a link to set a password goes to person30@example.com."; Cancel has focus; Cancel and Escape send nothing (outbox, notifications, tokens 0/0/0); Send email sends once (1/1/1), toast "Invitation link sent to person30@example.com." | PASS | `invite-confirm-1280.png` (mockup 12) |
| 16 | 375 | Internal Users: no page-level horizontal scroll (`scrollWidth` 360 = `clientWidth` 360); the grid scrolls inside itself | PASS | `iu-375.png` |
| 17 | 375 | Create user from Kumar's row: Salesperson suggested (sales agent contact, AC-40 second branch); Add user reachable (button at 678 to 712 of 812); created ACTIVE with no email; outbox unchanged | PASS | `add-375.png` |
| 18 | 375 | Kumar's contact: User account with name link, Roles, Signs in by, Unlink; no page overflow | PASS | `contact-linked-375.png` |
| 19 | 375 | Kumar's and Daniel's Sign-in sections wrap without overflow | PASS after fix D | `signin-375.png`, `signin-daniel-375.png` |
| 20 | 375 | Invitation confirmation at phone width, Cancel focused, Cancel sends nothing | PASS | `invite-375.png` |
| 21 | 375 | Add user, pick Aisyah's contact (already linked): inline "WhatsApp contact already linked to Aisyah Rahman" with Open user; Open user lands on her record | PASS after fix C | `add-409-375.png` |

Console: no errors from the S3 screens. The one dev overlay issue throughout is a pre-existing
React key warning in `Demo1Layout`, present on every page.

## Defects found by the walk and fixed in this lane

- **A. The quick create was not one click.** Add user stayed disabled until a field was
  typed, because the contact prefill does not dirty the form. It is now enabled whenever a
  contact is set, with a spec (`plan 6.2 quick create`) that fails without the fix.
- **B. A failed request left the link picker empty until reload.** `listUnlinkedUsers`
  turned an error into `[]`, which the query cached as "No unlinked user found". It now
  throws.
- **C. The WhatsApp contact picker needed two clicks.** The first click blurred the
  auto-focused empty Name, "onTouched" validation inserted "Name is required." above the
  picker, and the moved trigger missed the click. Add user now validates on submit and then
  on change; the empty-submit specs still pass. It is a layout effect, so this run is its
  evidence.
- **D. The profile card overflowed at 375** by 3px next to an email and its badge, and
  showed a blank email with "Not verified" for a phone-only user. The row now wraps and reads
  "No email".

## Notes on the walk itself

Two early Unlink/Cancel attempts were invalid runs, not product failures: one clicked a
stale snapshot ref (refs renumber on every snapshot), and one raced a backend reload while
the coder was editing. The check was re-run cleanly (row 10). One browser session wedged
(CDP timeout) while the machine was busy and was closed by name and reopened.
