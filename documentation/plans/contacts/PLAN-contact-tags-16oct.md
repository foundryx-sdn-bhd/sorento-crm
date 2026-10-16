# PLAN: contact tags + tag access settings (CONTACT-TAGS, 16 Oct)

Status: Plan - owner answered card 16 Oct; mock v2 filed (filters tidy, every setting on a tag), awaiting owner OK; UAC v2 drafted. Track: full (size L: migration, access writes, new list surface). No code yet.

Lane: `crew/contact-tags`. Rebases on CONTACT-ACTIONS-COPY (branch `claude/contact-actions-copy-ufwe9b`) once it lands.

## Journey

Owner, 16 Oct: cannot tell dealers, retail salespersons, project salespersons and internal staff
apart in Users & Access > People; cannot mass-configure access (e.g. "all retail dealers: no stock
inquiry, no purchase request"); cannot cross-check who has what.

## What exists (measured, origin/main 68e0e036)

- List: `sorento_crm_frontend/app/(protected)/user-management/contacts/components/ContactsList.tsx`
  (DataGrid :580, toolbar :598, filters `ContactAccessFilters.tsx:108-213`, bulk actions :626-676).
- Contact row `respond_contacts` (`app/models/access.py:230`); switches on the row (:266-305).
- Access types `contact_access_types` M2M (`access.py:69,92`): brand x dealer/office entitlements,
  carry no switch defaults. Dev: 48/104 contacts hold none, 24 hold all 7.
- Market segments `market_segments` M2M (`access.py:126,161`): retail 34 / project 30 on dev;
  drive CS routing + portal forms (segment `portal_form_types` :150).
- Bulk copy access + "Access differs from <contact>" (`app/services/contact_access_copy_service.py:666,753`;
  route `app/api/v1/user_management/contacts.py:208`, filter :112): whole access set, REPLACE, preview.
- No tags, no preset/role concept on main. ACCESS-MODEL PR #1434 (draft, roles -> domains -> fields +
  overrides) is open and stalled since 11 Oct.

## Proposed (pending card answers)

1. Tags: `contact_tags` catalog + `respond_contact_tags` M2M; admin list cloned from
   `contact-access-types/`; list column (PillOverflow), filter, bulk Tag / Untag. Explicit only.
2. Tag access settings: each tag may store a PARTIAL set of access values (only ticked settings).
   "Apply to N contacts" = preview + write through the copy service's diff/write, limited to those
   settings. No live inheritance.
3. Cross-check: column "Tag settings" (Matches / Differs n), filter "Differs from its tag settings",
   export carries tags.

## Decision log

- 16 Oct owner: Q1a, Q2a, Q3 everything (flag nonsense items), Q4a, Q5a with apply / don't apply
  choice + later per-contact and bulk "Apply tag settings". Filters: primary + More filters. See card.

## Phases

0. Card + mock approved (owner).
1. FE mock against fixtures.
2. Tester red tests from UAC -> coder green; migration on dev copy.
3. Reviewer + security-reviewer + browser 1280/375; hand test.
