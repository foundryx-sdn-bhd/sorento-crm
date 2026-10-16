# PLAN: NUMBER-FIELDS

Status: Plan (size M, full track: frontend only, no migration, no auth change). Lane NUMBER-FIELDS, branch
`crew/number-fields`. Behaviour card + mockup v1 filed to crew; build waits for the answers.

UAC: `number-fields-acceptance-criteria.md` alongside. Mockup: `documentation/mockups/number-fields/index.html`.

## Journey

See the UAC. One line: on OI-2610-0057 / CB6638 the "Request CS to reserve" Requested field snaps back
to 1 when cleared (`RequestReserveDialog.tsx:216`, `Math.min(Math.max(raw || 1, 1), remainingQty)` on
every keystroke). The owner wants every number field in the app to accept an empty draft and to check
only the final value.

## Audit

Pending (three read-only audit passes over `sorento_crm_frontend`, ~252 `type=number` hits in 111
files). The full table lands in `number-fields-audit.md` alongside this plan.

## Design

Pending the audit and the behaviour-card answers.

## Slices

Pending.
