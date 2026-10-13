# UAC: idea capture collects the canny-style fields (IDEA-CAPTURE, 13 Oct)

Plan: `PLAN-idea-capture-fields-13oct.md`. Fixtures are fake but realistic (public repo): never real
conversation text.

## Extractor (`ideation_extractor.py`)

- AC-01 The extraction schema's `fields[].key` is an enum of exactly `problem`, `desired_outcome`,
  `current_workaround`. `proposed_solution`, `impact` and `department` are not in it.
- AC-02 The schema has a required `use_cases` property: an array of strings, described as the
  concrete situations the user named (at most 5, never suggested).
- AC-03 `IdeateExtraction.use_cases` is a list of cleaned, non-empty strings, capped at 5 in code
  whatever the model emitted, and deduplicated in order.
- AC-04 A `fields` pair whose key is not one of the three is dropped (an owner-edited prompt that
  still names the old keys cannot leak them).
- AC-05 `skip` only keeps `desired_outcome`, `use_cases`, `current_workaround`, never `problem`.
- AC-06 Each schema description carries the field's meaning, so it holds under any prompt version.
  `problem` is described as the Business problem.
- AC-07 The fallback `ideate_extractor` prompt names the four fields and none of the dropped ones.
  It says that an unstated field stays empty and that use cases are never suggested.
- AC-08 The department normaliser is gone. `normalise_field_value` cleans the remaining keys the
  same way as today.

## Capture turn (`ideation_capture_service.py`)

- AC-10 The create payload carries `problem`, plus `desired_outcome`, `current_workaround` (strings)
  and `use_cases` (a JSON list of strings), each only when non-empty.
- AC-11 The create payload never carries `proposed_solution`, `impact` or `department`, even when
  the extraction somehow holds them.
- AC-12 With every new field present against an ss that ignores unknown keys (today's ss), the idea
  is created and the reply is `complete`. The unchanged `raw_transcript` still carries the user's
  whole message.
- AC-13 A similar-list hold stores `use_cases` on the pointer. NEW creates from the held message
  with the same `use_cases`, `desired_outcome` and `current_workaround`.
- AC-14 The `missing` facts are, in this order and only when not sent: `Desired outcome`,
  `Use cases`, `Current workaround`, then always `Photos or files`.

## Reply (`chatbot_reply_copy.py`, `ideation_capture_replies.py`)

- AC-20 `ideation_capture_complete` (en, ms, zh) lists each missing field on its own line as
  `- <name>`, after the "Open it to add what I didn't catch:" line. Nothing is comma-joined and
  nothing uses " · ".
- AC-21 The field names stay English in every language (subject to owner Q3).
- AC-22 The no-link variant is unchanged.

## Migration

- AC-30 One migration publishes the new `ideate_extractor` fallback and the new
  `ideation_capture_complete`, `.ms` and `.zh` texts as new versions and moves `production`, but
  ONLY where production still equals a known stock text. An owner-edited version is left alone with
  a warning. A re-run is a no-op.
- AC-31 The migration test finds the revision by id, and covers stock -> upgraded once, edited ->
  untouched, and a fresh DB.

## Live (before the hand test)

- AC-40 A golden set of at least 8 varied fake ideate messages (en, ms, zh, mixed, intent-only,
  solution-only, all four fields, workaround-only) runs through the live extractor. The result is
  recorded in the PR: no invented field, and use cases only where named.
- AC-41 Message routing for a solution-only message: PENDING owner Q1.
