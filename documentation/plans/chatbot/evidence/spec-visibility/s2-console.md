# AC-20 chatbot console check - spec visibility policy (S2, re-run 13/14 Sep 2026)

Lane: `.claude/worktrees/spec-visibility`, feat/spec-visibility-policy @ `26a07f1b4`
(fix commit "spec visibility fails closed..."). Backend `http://localhost:8082`,
DB `sorento_ai_automation_0907` (shared prod-copy). Runner: primary venv
`sorento_crm_backend/venv/bin/python`, `scripts/chatbot_console_check.py --say`,
`--api-key test` (worktree `.env` `EXTERNAL_API_KEY=test`). Every turn is a dry run
(`is_test`, `test_run_id`); nothing outside `chatbot.turns` was written and no WhatsApp
message left.

Product: `SRTKS2007B` (SORENTO S/STEEL KITCHEN SINK, `product_specifications.values`
carries `thickness: 0.8mm` plus `material`, `dim_width/height/length`, `brand`, `class` -
confirmed `board_thickness` is not a registry key at all yet, sibling lane
`chore/sink-thickness-loader`, so it is not usable for this check).

Contacts (both carry `chatbot.turns` history so the script's borrowed-envelope path has a
real `contact.custom_fields` array and does not hit the known
`KeyError: 'custom_fields'` at `route.py:220`):

- **Sean**, `respond_contacts.id=04ddf73e-3a0e-4914-8b24-18ed8aac06b8`,
  `respond_io_id=900000053`, `respond_contact_market_segments.segment_code=retail`.
- **CONTACT V**, `respond_contacts.id=6ea667f4-0a66-4607-93ec-366e4d96dfc9`,
  `respond_io_id=900000110`, `segment_code=project`.

Neither contact carries a contact-tier override, so both resolve via `default_policy` /
`project` segment respectively - confirmed via
`GET /api/v1/user-management/spec-visibility/contacts/{id}` before the run.

## Finding: the LIVE (labelled) parser prompt does not know the word "thickness" at all

`ai_prompt_labels` pins `chatbot_semantic_parser` `production` at **version 1** on this
database, while 22 versions exist (`ai_prompt_versions`, none promoted past v1 here). A
direct SQL `grep` for `thickness` against v1's template AND against v22's template (the
latest) returns **zero matches in both** - the word has never been vocabulary in any
version stored on this DB, unlike `material` (1 occurrence) or `steel grade` (2, only in
v22). Consequently, under the labelled prompt, `"thickness of SRTKS2007B"` and
`"what is the thickness of SRTKS2007B"` do not extract `thickness` as a requested
attribute at all - the reply is the bare base-fields block (Product Code / Description /
List Price / Dimensions), identical for both the retail and the project contact, with no
`Specs:` line and no per-key note either way. This is **not** a spec-visibility defect:
it is a pre-existing parser-vocabulary gap (same category as `growth-r1`'s own
`# NEW VOCABULARY` cases for `steel grade` and the bare `spec` word, which likewise fail
under the labelled v1 prompt and are documented there as gated on a label move). Nothing
in this lane's diff touches `ai_prompt_versions` (`git diff a584207a1^..26a07f1b4 --stat`
shows only the migration, `references.py`, chatbot `access.py`/`fetch.py`/`resolve_gate.py`,
`record_actions.py`, `spec_visibility.py`, and tests).

Confirming the labelled-prompt gap is pre-existing, unrelated to spec visibility: running
the FULL unrelated `tests/chatbot/console_cases/2026-09-07-growth-r1.yaml` file against
this same backend under the labelled prompt reproduces its own documented
`# NEW VOCABULARY` reds (`A1 spec ask shows the compact Specs line`,
`A1 one-key spec ask answers that key only`) plus a further batch of fails that are the
documented `OPENAI_API_KEY` MCP-tool-search limit (`branch_kind: None`,
"Sorry, I ran into a problem understanding that") - `OPENAI_API_KEY=` is empty in this
worktree's `.env`, exactly the documented local limit
(`documentation/agents/chatbot-verification.md`): **16 passed, 13 failed**, none of the 13
touching spec visibility.

**Grading therefore uses `--prompt-version` to pin the LATEST unlabelled version
(v22, `id=0770b509-d2ea-4d70-85c8-53ac65880f64`)**, exactly as the doc prescribes for
vocabulary the label has not yet picked up - a dry-run-only override
(`app.services.chatbot...engine._prompt_override` returns `None` on a live turn), nothing
promoted, nothing written. v22 DOES resolve `thickness` as a requested attribute (though
the word is likewise absent from its own template text - the extraction is evidently
registry-driven rather than a literal keyword match). This isolates the spec-visibility
projection/gating logic from the separate, already-documented parser-vocabulary gap.

## Case 1 - Specs line drop (AC-14/AC-15), no attribute forcing needed

`"material of SRTKS2007B"` **against the LABELLED (v1) prompt** - passes without any
`--prompt-version` pin, because `material` already is vocabulary:

- **Sean (retail):** `*Specs:* Product class: Kitchen Sink, Length: 820 mm, Material:
  stainless_steel, Width: 450 mm, Brand: SORENTO, Height: 220 mm` - **no Thickness**,
  though the row's `product_specifications.values` carries it.
- **CONTACT V (project):** identical line plus `, Thickness: 0.8 mm` at the end -
  confirming the two contacts get materially different pages for the SAME product/turn
  shape, differing exactly by the one key each policy hides.

PASS - AC-15 (drop hidden keys from the "Specs:" summary before it is built).

## Case 2 - direct ask on the hidden key (AC-16), pinned to v22

`"thickness of SRTKS2007B"`, `--prompt-version 0770b509-d2ea-4d70-85c8-53ac65880f64`:

- **Sean (retail):** `*Thickness:* not available` - never "not recorded for ...", matches
  AC-16 exactly.
- **CONTACT V (project):** `*Thickness:* 0.8 mm` - the real value.

PASS - AC-16.

## Case 3 - bare "spec" ask (Specs line again, confirms Case 1 under the pinned prompt too)

`"SRTKS2007B spec"`, same pinned version:

- **Sean (retail):** Specs line omits Thickness (same 6 keys as Case 1).
- **CONTACT V (project):** Specs line includes `Thickness: 0.8 mm` (7 keys).

PASS - consistent with Case 1, rules out the pin itself changing the projection.

## Case 4 - turn trace (AC-17)

Read directly off `chatbot.turns.trace` (jsonb array) for each contact's latest dry-run
turn:

- **Sean (retail)**, turn for Case 3's "SRTKS2007B spec": trace's last entry is
  ```json
  {"at": "2026-09-13T16:23:44.059281+00:00", "kind": "spec_visibility",
   "hidden": ["thickness"], "dropped": ["thickness"]}
  ```
  PASS - matches AC-17 exactly (`spec_visibility: {hidden, dropped}` beside the other
  stage entries, naming the keys actually removed).
- **CONTACT V (project)**, same case: **no `spec_visibility` entry appears anywhere in
  the trace array at all** (checked every element's `kind`). Read together with Case 1/3
  (project's envelope keeps every key, nothing was dropped), this is the entry being
  genuinely conditional on the projection having removed something - not a missing
  feature. Documenting as observed rather than assuming; if a reviewer wants an
  always-present `{"hidden": [], "dropped": []}` marker even on the empty case, that is a
  polish call, not a correctness one - AC-17's own wording is "listing the keys actually
  removed", which for the project contact is nothing.

## Summary table

| # | Scenario | Contact | Expected | Observed | Result |
|---|---|---|---|---|---|
| 1a | Specs line, material ask | Sean (retail) | Thickness absent | Absent | PASS |
| 1b | Specs line, material ask | CK (project) | Thickness present | `0.8 mm` | PASS |
| 2a | Direct thickness ask | Sean (retail) | "not available" | `*Thickness:* not available` | PASS |
| 2b | Direct thickness ask | CK (project) | real value | `*Thickness:* 0.8 mm` | PASS |
| 3a | Bare spec ask | Sean (retail) | Thickness absent | Absent | PASS |
| 3b | Bare spec ask | CK (project) | Thickness present | `0.8 mm` | PASS |
| 4a | Trace | Sean (retail) | `spec_visibility: {hidden, dropped}` | present, `["thickness"]`/`["thickness"]` | PASS |
| 4b | Trace | CK (project) | no drop | entry absent (nothing dropped) | PASS (see note) |

## Caveats / what did not run

- **AC-16/Case 2-3 required `--prompt-version` because the live labelled prompt (v1) has
  never had "thickness" as vocabulary**, on this database - a pre-existing gap, not part
  of this lane's diff. Reported so the captain does not read "needed a pin" as a lane
  defect.
- The unrelated `2026-09-07-growth-r1.yaml` full-file run (used only to confirm the
  labelled-prompt gap is pre-existing) is NOT a spec-visibility regression signal; its
  13 fails are documented pre-existing conditions (vocabulary label, `OPENAI_API_KEY`
  empty locally) unrelated to this PR.
- MCP / n8n were not exercised - not required for this check; the script talks directly to
  the FastAPI backend.
- `EXTERNAL_API_KEY` used was the worktree's own (`test`), acting-as-user per the
  worktree `.env`'s `EXTERNAL_API_KEY_ACT_AS_USER_ID` (already configured, unmodified).
