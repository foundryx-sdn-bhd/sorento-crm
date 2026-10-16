# PLAN: Project Sales owner hand-test polish (13 Oct)

Status: Merge-ready, owner hand-test PASS 13 Oct (medium lane: one data-only migration plus the lead create/qualify permission change; PR #1618)

Lane: PROJECT-SALES-POLISH. Source: the owner's hand-test of Project Sales on main, 13 Oct 2026, 21 points.
Acceptance criteria: `project-sales-polish-13oct-acceptance-criteria.md`.

## Decisions (owner answers to the behaviour card, 13 Oct)

- Q1 (a): the Register project form requires a lead (a form rule only: the API still accepts a project without one), and the Lead picker offers Create lead when
  nothing matches. The server stays nullable for Qualify, integrations and older projects.
- Q2 (a): clicking Spec In moves the stage and then opens New quotation. A Back to registered edge
  is added.
- Q3 (a): the customer is the linked lead's buyer, shown as Customer on the Overview. A new
  quotation's Name, Phone and Attn default from that customer's master data.
- Q4 (owner's version): the Edit scope popup stays (name and series) and opens from a pen icon
  beside the scope name. Series is not shown in the scope header.
- Q5 (a): a shared numbered stepper, `components/common/WizardSteps.tsx`.

## Reused components

SearchableSelect `createOption` (create-on-no-match), PillOverflow (brands), DetailActionsMenu with
getUserLookup (Reassign), PageHeader and BackToList (naming), the Button `destructive` variant
(Remove scope), and QuotationDialog (Edit scope).

## Backend

- `alembic/versions/spec_in_0001_stage_label.py`: a data-only migration. The specified rung is
  labelled Spec In, the edge into it is labelled Spec In, and a Back to registered edge is added on
  the default graph. The seed in `project_seed_service.py` matches.
- `project_service.serialize_projects`: adds `customer_id`, `customer_name`, `customer_phone` and
  `customer_attn`, all read through the lead.
