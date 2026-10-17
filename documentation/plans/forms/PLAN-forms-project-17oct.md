# PLAN: link every form to a project (FORMS-PROJECT)

Status: Build (Q2-Q5 answered 17 Oct; Q1 scope with the owner, (a) built meanwhile). Track: full
(migration + multi-company write check).

Owner 15 Oct: "Forms not connected to projects: sponsorship form, complaint, stock inquiry, all the
forms -> link to project." Fixes #1689 (staff PR/SF form drops project_id).

Owner rules: explicit links only, never auto-match by name (at most suggest, a person picks); forms
follow the PR/SF/Stock Inquiry layout; number fields clearable; never a stuck UI.

Behaviour card: `forms-project-behaviour-card.md`. UAC: `forms-project-acceptance-criteria.md`.

## Slices

1. **#1689** (done): staff check `app/services/project_link.py` (`check_project_link`,
   `apply_project_link`) on PR/SF + complaint create / update / update-and-reply; FE
   `toRequestBody` sends `project_id`.
2. **Schema** (done): migration `fp_0001_form_project_links` + model columns on
   `stock_inquiries`, `price_tag_requests`, `sales.opportunities`.
3. **Staff API**: schemas accept/return `project_id` + `project_code` for stock inquiry, price tag
   request, sales opportunity; services call `apply_project_link`; list endpoints of all six
   form types accept a `project_id` filter.
4. **Portal API**: complaint, PR, stock inquiry, price tag request, sales opportunity accept
   `project_id`, checked with `sample_request_service.assert_portal_project_allowed`'s rule
   (contact's companies) on save and submit.
5. **Staff FE**: picker (`components/common/SearchableSelect.tsx` + `searchProjectsForLink`) on the
   stock inquiry, price tag request and opportunity forms; `initialQuery` from the free-text
   project name; detail pages show `CODE - Title`.
6. **Portal FE**: `AsyncCombobox` + `lookupProjects` on the five portal forms.
7. **Project page**: "Forms" tab, one section per form type, each section the form's own list
   service with `project_id`, gated by that form's view permission.

## Not doing

- Workflow submissions and form-builder submissions (Q1 (a), pending owner).
- External n8n API (`schemas/external/*`): unchanged, never set a link before.
- Any backfill or name matching.
