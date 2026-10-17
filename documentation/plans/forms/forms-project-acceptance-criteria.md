# UAC: link every form to a project (FORMS-PROJECT)

Behaviour card: `forms-project-behaviour-card.md`. Answers 17 Oct: Q2-Q5 (a); Q1 with the owner,
(a) scope built (workflow + form-builder submissions out).

## Link storage

- **AC-FP-1** `stock_inquiries`, `price_tag_requests`, `sales.opportunities` carry a nullable
  `project_id` FK to `projects.projects` (ON DELETE SET NULL, indexed). No backfill.
- **AC-FP-2** Deleting a project leaves every linked form in place with no link.

## Staff saves (create, edit, update-and-reply)

- **AC-FP-10** The staff PR and SF forms send the picked project; a saved link shows again on edit
  and on the detail page (#1689).
- **AC-FP-11** A staff save naming a project outside the editor's companies, a missing project or
  free text is refused with 422 "That project was not found." on the project field; the old link
  is kept. Applies to PR, SF, complaint, stock inquiry, price tag request, sales opportunity.
- **AC-FP-12** A staff save that does not send `project_id` keeps the link; sending null clears it.
- **AC-FP-13** Stock inquiry, price tag request and sales opportunity staff forms show the same
  clearable "Project (optional)" picker as PR/SF; no project is required.
- **AC-FP-14** When a stock inquiry has a free-text project name and no link, the picker opens with
  that name in its search box; nothing is linked until a person picks. The free-text fields stay.

## Portal saves

- **AC-FP-20** The portal complaint, PR, stock inquiry, price tag request and sales opportunity
  forms offer the contact's own companies' projects (existing lookup), optional.
- **AC-FP-21** A portal save or submit naming a project outside the contact's companies (or any
  project, for a contact with no company) is refused with 422 "Pick one of your own projects.".
- **AC-FP-22** The existing SF "requires registered project" contact flag and the sample request
  "require project" setting behave exactly as before.

## Reading the link

- **AC-FP-30** Each form's staff response carries `project_id` and `project_code`; the detail page
  shows the project as `CODE - Title` linked to the project, or "No project". No UUID on screen.
- **AC-FP-31** Each form's staff list endpoint accepts `project_id` and returns only that project's
  forms, under the list's existing row rules.
- **AC-FP-32** The project page has a "Forms" tab with one section per form type (Complaints,
  Purchase requests, Sponsorship forms, Stock inquiries, Price tag requests, Sales
  opportunities). A section shows only for a user with that form type's view permission; rows
  open the form's detail page; an empty section shows a heading and hint.
- **AC-FP-33** Usable and not clipped at 375px and 1280px; every load or save failure ends in a
  visible error, never a stuck spinner.
