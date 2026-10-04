# Ideation flow UX review - 24 Sep 2026

Status: review complete, no code changed

Part of #1172. Walked the chatbot ideation flow as a first-time business user would, against a
LOCAL foundryx-shared-service backend, never prod. Code map for the flow is in the issue
comments (`gh issue view 1172 --comments`).

## Setup

- Local `foundryx-shared-service` backend booted from `~/Documents/foundryx/foundryx-shared-service/service_backend`
  on `:8001`: `DATABASE_URL=postgresql://foundryx:<password>@localhost:5432/fx_shared_local
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001`. DB `fx_shared_local` created fresh
  (`createdb -O foundryx fx_shared_local`) and migrated/seeded with the repo's own tooling:
  `DATABASE_URL=postgresql://foundryx:<password>@localhost:5432/fx_shared_local
  POSTGRES_ADMIN_URL=postgresql://tehjayson@localhost:5432/postgres .venv/bin/python -m
  scripts.bootstrap_db`. This creates the default tenant, roles, demo admin, and installs the
  `omnichannel` module.
- Installed the `ideation` module for the default tenant, created a software Product ("Sorento
  CRM", product_domain_base `http://localhost:3000`), and minted a workspace API key via
  `modules.omnichannel.services.api_key_service.ApiKeyService.mint(...)` run as a one-off script
  against the same `fx_shared_local` database the running `:8001` process reads (not
  `TestClient`, not SQLite) - script at
  `/private/tmp/.../scratchpad/seed_ideation.py` (not part of this PR).
- Snag found and fixed during setup: the first `bootstrap_db` run left `fx_shared_local` without
  the `pg_trgm` Postgres extension even though its own Alembic migration
  (`0002_ideation_dedup_trgm.py`, `CREATE EXTENSION IF NOT EXISTS pg_trgm`) recorded as applied
  (`alembic_version_ideation` = `0008_ideation_business_reqs`). The `foundryx` app role has no
  elevated attributes locally, so the extension silently did not materialize; the very first real
  `create_idea` call 500'd on `function similarity(text, unknown) does not exist` (the module's
  own dedup query). Fixed by running `CREATE EXTENSION pg_trgm` as the Postgres superuser and
  recreating the GIN index by hand. This is a local dev-environment role-permission wrinkle, not
  a shared-service code defect, so it is not carried forward as a review finding - noted here only
  so the setup step is reproducible.
- Repointed the sorento side: `sorento_crm_backend` on `:8000` (cwd
  `~/Documents/foundryx/sorento_crm-r7-spo/sorento_crm_backend`, DB `sorento_ai_automation_0923`),
  `respond_workspaces` default row (`id=9b8c61df-aec2-4095-8d2f-aa3d27274721`) updated:
  `ideation_shared_service_url=http://localhost:8001` (no `/be` prefix - that prefix only exists
  behind the deployed Caddy `handle_path`, a bare local `uvicorn` serves at root),
  `ideation_product_id` = the local Product id, `ideation_intake_api_key_ciphertext` = the minted
  key, Fernet-encrypted with the SAME scheme `app/utils/field_encryption.py` uses (key derived
  from sorento's own `JWT_SECRET`, read from `.env` with `grep`, never edited). Previous values
  saved to a restore SQL file before any change.
- Verified the wiring with `app.services.ideation_turn_service.call_create_idea(base_url, key,
  payload)` called directly (not through the chatbot engine, not a WhatsApp turn) - it reached
  `:8001` and returned a real `collecting` response. That throwaway draft was deleted from
  `fx_shared_local` before the real walk so it would not skew the record check.
- Snag found and fixed to run the console script at all: the prod-copy DB
  `sorento_ai_automation_0923` had no `integration_api_keys` row for the local placeholder
  `EXTERNAL_API_KEY=test` in this worktree's `.env` (a known class of gap - `get_external_api_user`
  resolves keys through `integration_api_keys`, not the env var at runtime, and a prod snapshot
  only carries the real prod keys). Inserted a local-only placeholder row hashing `test` against
  the existing `n8n` integration, the same recipe already documented for this exact situation.
  Local-DB-only, reversible, does not touch prod.
- Ran the sanctioned dry-run tool exactly as instructed:
  `sorento_crm_backend/scripts/chatbot_console_check.py --say "..." --base-url http://localhost:8000`.
  Three separate invocations = three separate conversations (the script does not persist state
  server-side across processes on a dry run).

## Transcripts

### Run 1 - build up an idea, hesitate, try to skip, then confirm

```
--say "i have an idea, the price tag should show promo price in red"
--say "so people know it's on sale, sales team keeps getting asked why the price is different from the sticker"
--say "what do you mean impact?"
--say "it would save the sales team from repeating themselves and stop customers thinking we overcharged"
--say "can you just submit it already"
--say "confirm"
```

```
--- turn 1 ---
> i have an idea, the price tag should show promo price in red
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]

--- turn 2 ---
> so people know it's on sale, sales team keeps getting asked why the price is different from the sticker
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]

--- turn 3 ---
> what do you mean impact?
  branch_kind: clarify_menu
  reply.text: I see you're trying to ask what "impact" means, Let me understand more.

Are you asking about any of these?

- Product (List Price, Dimension)
- Photos, Technical Specs, Cert
- Promotion
- Forms
- Stock
- Delivery order
- Incoming
- Catalogue, Warranty

I can help with the topics listed above.

--- turn 4 ---
> it would save the sales team from repeating themselves and stop customers thinking we overcharged
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]

--- turn 5 ---
> can you just submit it already
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]

--- turn 6 ---
> confirm
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]
```

### Run 2 - the same idea again, to see the duplicate branch

```
--say "i have an idea, the price tag should show promo price in red"
--say "sales team, promo price in red so people don't ask why sticker price is wrong"
--say "confirm"
```

```
--- turn 1 ---
> i have an idea, the price tag should show promo price in red
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]

--- turn 2 ---
> sales team, promo price in red so people don't ask why sticker price is wrong
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]

--- turn 3 ---
> confirm
  branch_kind: low_signal
  reply.text: Hi! How can I help today?
```

(First attempt at turn 3 hit a shared OpenAI TPM 429 and was re-run after a pause; the 429 body
is a known, unrelated environment limit, not a finding - see `LESSONS-LEARNT.md`.)

### Run 3 - send an idea, never confirm

```
--say "i have an idea for the chatbot, it should remember what dealer already asked before"
--say "dunno lah, can skip this one?"
--say "ok fine, it's for the whatsapp team, saves them re-asking dealers the same thing"
```

```
--- turn 1 ---
> i have an idea for the chatbot, it should remember what dealer already asked before
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]

--- turn 2 ---
> dunno lah, can skip this one?
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]

--- turn 3 ---
> ok fine, it's for the whatsapp team, saves them re-asking dealers the same thing
  branch_kind: ideate
  reply.text: [dry-run: ideation reply not generated]
```

### Supplementary - what the templates actually say

The three runs above are what the sanctioned dry-run tooling shows: never the real reply (see
Finding 1). To see the real Conversational-Intake reply templates, the local shared service's
`create_idea` endpoint was called directly and repeatedly (`call_create_idea`, the same function
sorento's brain calls - NOT the chatbot engine, NOT a WhatsApp turn, NOT the console script),
with `fields`/`confirm` supplied by hand standing in for what the sorento brain would have
extracted from each message. This is the only way this review could observe the real templated
language; it is not itself a chatbot turn and is kept separate from the three runs above.

```
turn 1  "i have an idea, the price tag should show promo price in red"
  -> Here's what I've got so far:
     - Problem statement: i have an idea, the price tag should show promo price in red

     Still need: Proposed solution, Impact, Department.

turn 2  "so people know it's on sale"  (extracted: department=Sales)
  -> Here's what I've got so far:
     - Problem statement: i have an idea, the price tag should show promo price in red
     - Department: Sales

     Still need: Proposed solution, Impact.

turn 3  "what do you mean impact?"  (nothing extracted)
  -> (same reply repeated verbatim - the bot does not acknowledge the question)

turn 4  "can i skip that"  (nothing extracted, impact stays required)
  -> (same reply repeated verbatim - the bot does not acknowledge the skip request)

turn 5  "ok it saves the sales team from repeating themselves and stops customers thinking we
         overcharged"  (extracted: proposed_solution, impact)
  -> Here's your idea:
     - Problem statement: i have an idea, the price tag should show promo price in red
     - Proposed solution: Show the promo price in red on the price tag
     - Impact: Saves the sales team from repeating themselves and stops customers thinking they
       were overcharged
     - Department: Sales

     Reply 'confirm' to submit it, or tell me what to change.

turn 6  "confirm"
  -> Your idea has been captured. Track it here: http://localhost:3000/ideas/5e52c0e0-9a4f-4153-ba39-d88884bdcadb
```

```
Conversation B (duplicate): "i have an idea, the price tag should show promo price in red" (new
conversation, same wording)
  -> status=duplicate, duplicate_of=5e52c0e0-9a4f-4153-ba39-d88884bdcadb
  -> This is similar to an existing idea - I've upvoted it for you.
```

```
Conversation C (never confirms): built up to a full "review" state ("Reply 'confirm' to submit
it, or tell me what to change.") and then stopped - draft left open, matching Run 3's intent.
```

## Records

`app_ideation.ideas` in the local shared service, checked after every run:

- After Run 1, Run 2, Run 3 (the sanctioned `--say` walks): **zero rows**. No idea was ever
  created, updated, or upvoted by any of the three sanctioned walks.
- After the supplementary direct `create_idea` calls: three rows -
  - `5e52c0e0-9a4f-4153-ba39-d88884bdcadb` - status `captured`, problem/solution/impact/department
    all filled, `source=whatsapp`, `submitter_name=Ainul (Dealer Staff)`.
  - `5b890f26-28bf-4633-bd52-9821f11ed09d` - status `draft`, problem filled, solution/impact/
    department all blank. This is the ORPHAN row Conversation B's "duplicate" turn created before
    it matched and returned `duplicate_of` - it never advances past `draft` and nothing in the
    flow ever revisits it.
  - `dc777237-59c3-40fd-b4bf-b10b3291b834` - status `draft`, problem/solution/impact/department
    all filled (full "review" state), left `draft` because Conversation C never confirmed.

## Findings

1. **A first-time business user testing this flow through either sanctioned tool (the console
   script's `--say`, or the `/system-management/chatbot-console` page) never sees a real
   ideation reply, ever** - both always mark the turn `is_test=True`, and the `ideate` lane's own
   code (`app/services/chatbot/lanes/ideate.py::run`) checks `dry_run` FIRST and returns a fixed
   placeholder (`"[dry-run: ideation reply not generated]"`) instead of calling the tool, by
   design (D14 - the tool "mints or mutates a REAL idea record ... none of that rolls back with
   the session"). Every ideate turn in Run 1, Run 2 and Run 3 shows this placeholder verbatim.
   Evidence: `app/services/chatbot/lanes/ideate.py` docstring on `run()`, and every `branch_kind:
   ideate` line in the three transcripts above.

2. **A mid-conversation clarification question breaks out of the ideation topic into an unrelated
   generic menu.** In Run 1, turn 3 ("what do you mean impact?") was routed to `clarify_menu`
   instead of staying in `ideate`, and answered with a canned list of eight unrelated topics
   (Product, Photos, Promotion, Forms, Stock, Delivery order, Incoming, Catalogue) that has
   nothing to do with the word "impact" in an idea intake. The conversation only returned to
   `ideate` on the next message. Evidence: Run 1, turn 3 transcript above.

3. **The bare word "confirm" alone does not reliably continue the pending idea; it can fall
   through to a generic greeting.** In Run 2, turn 3, sending only "confirm" (as the real
   templated reply itself instructs: "Reply 'confirm' to submit it") routed to `low_signal` and
   replied "Hi! How can I help today?" - not an acknowledgement of the idea, not a re-ask, just a
   generic opener, as if the conversation had reset. Evidence: Run 2, turn 3 transcript above.

4. **The real reply templates never acknowledge a hesitation or a skip request** - they repeat
   the exact same "Still need: ..." sentence verbatim regardless of what the user just said.
   Asking "what do you mean impact?" or "can I skip that" gets back the identical missing-fields
   list, word for word, with no sign the bot registered the question. Evidence: Supplementary
   transcript, turns 3 and 4 (Conversation A).

5. **Four mandatory fields collected one at a time over WhatsApp, with a jargon field name
   ("impact") that got no explanation when asked about it.** The real flow requires problem,
   proposed solution, impact, and department before it will even offer a review, and turn 3 of
   the supplementary transcript shows that asking what "impact" means gets no answer at all.
   Evidence: Supplementary transcript, `Still need: Proposed solution, Impact, Department.` and
   the repeated line after the hesitation.

6. **The `confirm` keyword is a hard gate with no synonym recognized by the deterministic
   engine.** Even a full "review" state with every field captured ("Reply 'confirm' to submit it,
   or tell me what to change.") will sit there until the literal word arrives - phrases like "can
   you just submit it already" (Run 1, turn 5) are not treated as confirmation by the tool itself
   (`confirm: bool` on the request), and in Run 1 that same phrase never reached the tool at all
   (Finding 1) so a real user saying it would have gotten the frozen placeholder rather than
   either a submit or a "please say confirm" nudge. Evidence: Run 1 turn 5, and
   `modules/ideation/routers/intake.py` docstring: "`confirm` is the only path to `complete`".

7. **A duplicate report leaves behind a permanent, unreachable orphan draft.** Conversation B's
   "duplicate" turn still inserted its own `Idea` row (`5b890f26-...`, status `draft`, only the
   problem field filled) before matching and returning `duplicate_of` - that row never advances,
   is never shown to anyone, and nothing in the flow revisits or cleans it up. Evidence: Records
   section, row `5b890f26-28bf-4633-bd52-9821f11ed09d`.

8. **The duplicate report gives the user no way to disagree.** "This is similar to an existing
   idea - I've upvoted it for you" is stated as fact with no path to say "no, this is different"
   - the only follow-up the deterministic reply itself offers is silence. Evidence: Supplementary
   transcript, Conversation B reply.

9. **The tracking link handed to the user on completion needs an SSO session the WhatsApp
   submitter has never been asked to create.** The `complete` reply is `Your idea has been
   captured. Track it here: <link>` where `<link>` is `{product_domain_base}/ideas/{idea_id}` -
   a page gated by `ideation.board.view` and an SSO embed per the code map, not a public URL.
   Evidence: Supplementary transcript, Conversation A turn 6 link, cross-referenced against the
   code map's "Afterwards" section (`sorento /ideas` iframes the shared-service pages "gated by
   `ideation.board.view`").

10. **No title or category is ever asked for or shown**, so the only thing distinguishing one
    idea from another in every reply and in the `ideas` table is the free-text "problem" sentence
    (a full user message, not a short label). Evidence: Records section - every `problem` value
    above is a full sentence, and the code map states "No title, no category."

## Open questions for the owner

- Is there a sanctioned way to see the real ideate reply in a dry run at all, or is a live
  WhatsApp message (or a unit test against `IntakeService` directly) the only way anyone
  ever checks this flow's actual customer-facing language before it ships?
- Should "what do you mean X" / "can I skip Y" turns get a real, field-aware answer instead of
  the same missing-fields sentence repeated verbatim?
- Should "confirm" have any synonym, or a nudge back toward it when the parser cannot place a
  reply while a draft is in `review`?
- Should a duplicate report let the user say "no, this is a different idea" instead of a flat
  auto-upvote?
- Should the orphan `draft` row a duplicate turn leaves behind be cleaned up, or reused instead of
  discarded?
- Should the tracking link differ for a WhatsApp submitter with no CRM/SSO session, or is the
  link only ever meant for staff who already have one?
