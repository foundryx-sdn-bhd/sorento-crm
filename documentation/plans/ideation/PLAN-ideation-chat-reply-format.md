# PLAN - Ideation chat reply format (issue #1277)

**Status:** built, PR open (branch `fix/ideation-chat-reply-format`); reviewer round 1 folded
(label parser keeps a value's own leading `*`, accepts `**Label:**` / `_Label:_`; full-width
quoted titles; user's language kept in the extractor); fix lane round 2 folded (owner console
test 26 Sep 14:09Z: W1 clean values from turn one, W2 no typed punctuation or typo in a value,
W3 confirm before create; migration `ideation_confirm_prompts`); fix lane round 3 folded (main
`dc10a1afb` merged, `ideation_reply_fmt_prompts` re-parented onto `sales_0002_team_leader`,
single head `ideation_confirm_prompts`). Pre-merge: confirm prod's
`production` label for `ideate_extractor` / `ideate_reply` is not an admin edit the migrations
would replace. Track: small fix, with two
data-only migrations (each publishes two prompt versions, no schema change); no auth/RBAC change, no
new external ingest surface.
**UAC:** `ideation-chat-reply-format-acceptance-criteria.md` (alongside).
**Evidence:** the owner's console test of 26 Sep 2026 quoted in #1277 (prompt v27).
**Rulings in force:** everything in `PLAN-ideation-intake-redesign-24sep.md` (R1 to R18) except
where #1277 amends it: R10 as amended by R16 put the title on line 1 of every recap; #1277 finding
3 drops it from the recaps and keeps it only in the final (`complete`) message. That amendment is
ASSUMED (the issue says "ruling assumed unless the owner overrules") and is flagged in the PR.

## What is wrong today (verified)

1. Labels are plain. `_ideate_reply_fallback` tells the model `'Problem: ...'`, and the
   shared-service template fallback is plain too. Nothing in sorento bolds them.
2. The Problem value is the user's raw words. `_ideate_extractor_fallback` never asks for a clean
   statement, and the context block the extractor reads joins the captured fields with `"; "`
   (`ideation_extractor.py`, `captured_lines = "; ".join(...)`), which is the same glue the owner
   saw inside the Problem line.
3. The recap's line 1 is the quoted title (R10/R16 as written), so it shows on every recap with no
   label.
4. The media offer is text only: `build_menu_text` lists file names; nothing sends the images.

## Design (the simplest thing that covers both reply paths)

- **W1 + W3, one deterministic pass.** `compose_ideate_reply` runs every reply, the LLM's or the
  shared-service template it falls back to, through `_format_ideate_reply(text, facts)`:
  - a line whose label (wrapped in `*`, `**` or `_`, or plain) is `Problem`, `Solution`,
    `Impact` or `Department` followed by `:` is rewritten `*Label:* value` (idempotent);
  - for every status except `complete`, a line that is only the draft title (quotes, `*` and an
    optional `Title:` prefix ignored, case and spacing ignored) is dropped. `complete` keeps the
    title on line 1 with the idea number and the link. `duplicate_candidate`'s
    `Similar idea exists: <candidate title>` line is not the draft title and stays.
  - `_passes_reply_checks` reads a field line through the same label parser, so a composed reply
    that already bolds its labels is accepted instead of falling back.
  - `_ideate_reply_fallback` is reworded to match (bold labels, no title line in a recap).
- **W2, prompt only.** `_ideate_extractor_fallback` gains a CLEAN VALUES rule for problem,
  proposed_solution and impact (strip conversational preamble such as "i have an idea", "i think
  the problem is", "i guess"; correct spelling; when extending, rewrite the old value and the new
  detail as one readable sentence, never a semicolon join; keep the user's meaning, add nothing).
  The captured context is listed one field per line instead of `"; "`-joined. `raw_text` is
  untouched: `raw_transcript` is still the user's own words, turn by turn.
- **Prompt versions.** Migration `ideation_reply_fmt_prompts` calls `seed_prompt_registry` then
  `bump_prompt_to_fallback` for `ideate_extractor` and `ideate_reply` (the 272 precedent for an
  ideation prompt): each publishes the new text as the next version and moves `production` to it,
  a no-op when production already carries it.
- **W4, the existing outbound media path.** `handle_turn` returns `offered_media`
  (`[{position, kind, url, filename}]`, the menu's own numbering) on the turn that builds a media
  menu, `[]` otherwise; `IdeationTurnResponse` declares it (response_model drops undeclared
  fields). The ideate lane (`lanes/ideate.py::run`) turns the images among them into
  `reply_extras["attachments_src"]`, one entry per image, `{url, filename, mimeType,
  attachmentType: "image", caption: "<position>"}`, `None` when there are none. The engine's
  existing `_send_actions` already emits `send_attachments` after `send_message` (flagged
  `dry_run` on a console turn) and n8n's `sub-send-attachments` does the Respond.io send. No new
  mechanism.
  - 24h window: same rule as every other chatbot `send_attachments`: the reply answers an inbound
    message that just arrived, so the window is open by construction; the CRM does not send on
    the turn path (D9).
  - Console: `TurnAttachments` shows an image entry as a thumbnail with its caption; bot bubbles
    render WhatsApp `*bold*` as bold (`components/chatbot/WhatsAppText.tsx`).

## Not in this change

- n8n's `sub-send-attachments` lives outside this repo. Whether it forwards the entry's
  `caption` to Respond.io's `attachment.caption` cannot be checked here; flagged in the PR.
  Trigger to change n8n: the first live turn whose images arrive without their number.
- Videos, audio and files in the menu are still listed only; the issue asks for images.

## Tests

Tester first (red), then the fix (green), each kill-tested:

- W1, W3: `tests/test_ideation_reply.py` and `tests/test_ideation_turn.py` (turn harness, stubbed
  create_idea and provider).
- W2: `tests/test_ideation_extractor.py` (prompt carries the rule; context one field per line) +
  `scripts/replay_ideate_extractor.py` (live replay of the #1277 transcript, needs an LLM key).
- W4: `tests/test_ideation_turn.py` (`offered_media`), `tests/chatbot/test_ideation_offered_media.py`
  (engine `run_turn` with the tool stubbed: actions, captions, dry run), vitest for
  `TurnAttachments` and `WhatsAppText`.

## Round 2: owner console test 26 Sep 2026 14:09Z (PR #1279 comment)

**Owner rulings, 26 Sep 2026 (PR #1279, "Owner console test of #1279"):**

> "I expect when the AI answers, there's no typo. Why you factor in my typo into the answer? And
> then why the last message when asked for department, there's a question mark behind the
> department. What does that mean? I thought you want to confirm whether to submit this or not."

- **R-W1 (26 Sep 2026):** the bot never echoes a typo or a preamble in any field, on any turn.
  The extractor output is what the recap shows from turn one; when there is none for a field,
  the recap says it is still being worked out rather than echoing the message.
- **R-W2 (26 Sep 2026):** a question mark or other punctuation typed by the owner is never
  stored as part of a value ("the manufactuirng?" becomes "Manufacturing").
- **R-W3 (26 Sep 2026):** before creating the idea the bot shows the four fields and asks
  "Submit this idea? Reply yes to submit, or tell me what to change." Only a yes (yes, ok, ya,
  boleh, 好, 可以) creates it; anything else is an edit or a question and re-enters the recap.

### Root causes (from the code; the 14:09Z trace lives on the owner's stack DB)

- **W1, raw Problem on turns 1 to 3.** Not the template fallback alone and not a stale prompt
  label: the extractor ran every turn, but on turn 1 it emitted `proposed_solution` ("Implement
  a production line.", cleaned, as the owner saw) and **no `problem`**, because "i think we
  should implement X" reads as a solution and the prompt said "only include a field the user
  actually stated". `problem` is the intake's one required field, so the shared service seeded
  it from `message_text`, the raw message. Sorento then echoed `result.captured.problem`
  unchecked on turns 1, 2 and 3 (the reply facts and the template both read it). At the
  Department turn the extractor, reading the raw value in "Already captured", rewrote it, and
  only then did the Problem line change. The same shape is in the #1277 transcript
  ("Problem: i ahve an idea, i want sale sorder report to track KKPI" next to a cleaned
  Solution). Trace check for the owner's DB: `select created_at, trace from chatbot_turns
  where contact_respond_id = '900000008' and created_at between '2026-09-26 14:09Z' and
  '2026-09-26 14:11Z' order by created_at` - the ideate lane's pointer (`ideation.captured`)
  shows `problem` equal to the raw message while `proposed_solution` is already clean.
- **W2.** The extractor copied the department answer verbatim; nothing normalised it.
- **W3.** The confirm step exists (R3, `review` status, `confirm` only in review) but the reply
  never asked it: in `review` the composer had no `next_field`, so on reply 3 it made up a
  department question (R15 says department is never asked), and on reply 4 the only `?` was
  the one typed inside the department value, which satisfied the "exactly one trailing `?`"
  gate, so no question was asked at all. The owner's next message then submitted.

### Design (round 2)

- **W1:** the pointer carries `clean_fields`, the values the extractor produced for this draft.
  `_display_captured` shows a captured value only when it is one of those; anything else (the
  intake's seed) reads "still being worked out", on the LLM path and the template path alike
  (`_format_ideate_reply` writes each field line's value from the facts). A draft opened before
  this change (no `clean_fields`) trusts its captured values. Prompt: problem is ALWAYS emitted
  on the first message (the need behind a stated solution), and a raw captured value is
  re-emitted cleaned on the next turn.
- **W2:** `normalise_field_value` / `normalise_title` in `ideation_extractor.py`, applied in
  `handle_turn` to every extracted value before the payload: no quotes or typed `?`/`!` at
  either end, first letter capitalised, department in Title Case without "the"/"our". Spelling
  is the prompt's job (CLEAN VALUES gains the department and punctuation rules). There is no
  known department list in sorento (R14: department is free text), so no list lookup.
- **W3:** a `review` reply is rebuilt deterministically: any short prose line the composer
  wrote, then the recap in fixed order, then the confirm line, always last. `derive_confirm`
  decides `confirm` in one place: review only, no field edit or removal, not change/cancel,
  and either a bare yes or a model `submit` that carries a yes word. A bare yes also submits
  when the extraction came back empty. The confirm line is fixed English (the owner's wording).
- **Prompts:** migration `ideation_confirm_prompts` (revises `ideation_reply_fmt_prompts`)
  publishes both keys' fallbacks as the next version and moves `production`.

### Tests (round 2, red first)

`tests/test_ideation_turn.py` (turn harness with the intake's seeding in the fake, the
14:09Z replay), `tests/test_ideation_reply.py` (review shape, fact values),
`tests/test_ideation_extractor.py` (normaliser, confirm gate, prompt rules, migration),
`scripts/replay_ideate_extractor.py 1409z` (live), console case
`tests/chatbot/console_cases/2026-09-26-ideation-confirm-before-create.yaml` (live stack).
