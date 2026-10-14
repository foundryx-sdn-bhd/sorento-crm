# PV2 scenario battery

Multi-turn conversations the owner-style hand test expects PV2 to handle smoothly. Contract:
`documentation/plans/chatbot/pv2-eval-battery-14oct-acceptance-criteria.md` (UAC-1 to UAC-3); runner:
`sorento_crm_backend/scripts/pv2_battery/` (`python -m scripts.pv2_battery check | calibrate | run`).

- `<domain>.yaml`: conversations, each tagged `source` (real | improvised), `domain`, `audience`, `shapes`; every
  turn has an `expect` block.
- `contacts.yaml`: the dev contact per audience (ids live only here).
- `calibration.yaml`: hand-labelled real turns for the judge's agreement check.

## Dev preconditions (shared dev DB `sorento_cagent_stack`)

- The `dealer` contact (`contacts.yaml`) must keep its 5 customer links (HANLIM TRADING SDN BHD A/C I to IV and
  MODERNMED SDN BHD). A UI unlink removed them on 14 Oct 2026; crew restores them from the audit log with the
  insert-only, idempotent lane SQL `crew/state/migrations/PV2-EVAL.sql` (`crew migrate PV2-EVAL`). Without the links
  the dealer conversations exercise the unlinked path instead and their results do not count.
- Expectations name subjects and behaviour, not dev figures, so stock and order numbers may drift. Documents named
  in the battery (for example DO 202610-0151 with one line, DO 202609-3750 with three or more) must still exist.
