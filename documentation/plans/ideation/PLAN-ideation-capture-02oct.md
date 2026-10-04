# PLAN: Ideation capture from one message (IDEATION-CAPTURE)

Status: Hand test (Phase 3 done: review + security review fixed and re-reviewed, kill tests
killed). Q1-Q5 answered, built on the final SS-IDEATION-OWN contract (ss#111). H1 (#1445 helper
wired into the ideate lane) done and reviewed, kill test killed. Hand-test script:
`laneboard/scripts/1444.md`. Open owner asks: verified phone for chatbot senders; ss to also match
the raw message for similar ideas. Track: full (L). Base `main` (PR #1438 merged).
Lane branch `claude/ideation-capture-0hctm7` (the sandbox only pushes this name).

## 0. Owner intent (2 Oct 2026, verbatim summary)

- Capture the idea from ONE message. Do not try to link other photos or messages to it.
- When a user states an idea: first find similar EXISTING ideas of their own. If similar ones
  exist, list them with their links and ask: edit one of these (link), or create a new idea from
  what was just said. If none are similar, create it directly.
- On create: create it up front with what was captured, give the link, and say the user can open
  it to fill the other fields that were not captured, LISTING those missing fields.
- A bare "want to submit idea" with no content: ask back for the minimal required information only.
- Link target: the CRM ideation page (#1438), NOT the public portal. The ideation page gets a
  "My ideas | All ideas" toggle.
- No auto name-matching of people or records.

## 1. What exists today (measured)

Paths: `BE` = `sorento_crm_backend/`, `FE` = `sorento_crm_frontend/`, `ss:` = the shared-service
repo (not readable from this sandbox; facts about ss below are quoted from the CRM plans that read
it, never re-verified here).

### 1.1 The chatbot ideate turn is a multi-turn draft today

- One entry: `handle_turn` (`BE/app/services/ideation_turn_service.py:761`), reached from the
  chatbot `ideate` lane through the MCP tool `crm_ideation_turn`
  (`BE/app/services/chatbot/lanes/ideate.py:33-71`).
- Each turn POSTs ss `/ideation/intake/create-idea` (`ideation_turn_service.py:66,660-689`) with a
  `draft_id` carried on `respond_contacts.session_vars.ideation` (`:800-838`, `:1041-1093`). ss
  answers `collecting` -> `review` -> (`confirm: true`) `complete`, plus `duplicate_candidate` /
  `voted` / `cancelled` (contract table, `PLAN-ideation-intake-redesign-24sep.md:88-101`).
- Even a first message carrying every field goes through `review`; there is no auto-complete
  (`ideation-ideate-turn-simulation-transcript.md:208`).
- Media lookback: every turn without an open menu pulls the contact's last 50 Respond messages and
  offers a "which of these files relate?" menu (`ideation_turn_service.py:890-931`, `:1036-1039`).
  This is exactly the "link other photos/messages" the owner wants gone.
- A 24h idle reminder + close sweep exists only because drafts exist (`:1106-1380`).
- The only required field is `problem`; `proposed_solution`, `impact`, `department` are optional;
  department is never asked (R1, R15, `PLAN-ideation-intake-redesign-24sep.md:186-200`). The
  extractor also produces a 1-8 word `title` (`ideation_turn_service.py:958`).
- Similarity today: ss runs a pg_trgm check against ALL non-test ideas of the product (not the
  submitter's own) and returns ONE `duplicate_candidate {idea_number, title}`
  (`PLAN-ideation-intake-redesign-24sep.md:201-204`). The threshold value lives in ss.
- The link ss returns is the public track page `{frontend_url}/public/ideas/{status_token}`
  (`PLAN-ideation-intake-redesign-24sep.md:101`), being moved to `/portal/ideas/{token}` on the
  CRM domain by SS-PUBLIC-LINK-BASE (`PLAN-ideation-in-crm.md:459-466`). ss does NOT return the
  idea's id on the intake response (`ideation_turn_service.py:174-190` reads every field it gets).

### 1.2 The CRM ideation page (#1438)

- Native list `/ideas` and detail `/ideas/{id}` over the gateway `/api/v1/ideation/*`
  (`BE/app/api/v1/ideation/ideas.py`). The list forwards only `filter` and `search` to ss
  `GET /embed/ideas` (`ideas.py:72`). There is no "mine" filter.
- `Idea` carries `submitterName` only, no submitter id, phone or email (`FE/types/ideas.ts:38-71`),
  so the CRM cannot tell whose idea it is without name-matching, which is ruled out.
- Edit (`PATCH /ideas/{id}`) needs `ideation.ideas.manage` (`ideas.py:162-163`, PLAN-ideation-in-crm
  section 0, 2nd ask Q2). A submitter with only `ideation.board.view` cannot fill the missing
  fields on their own idea today.
- The embed assertion identifies the CRM user by `sub`, `email`, `name` only
  (`BE/app/services/ideation_embed_service.py:138-149`); no phone.

### 1.3 Who the WhatsApp sender is in CRM terms

- A CRM user is linked to a WhatsApp contact by `users.respond_contact_id`
  (`BE/app/services/user_contact_link.py:109`, identity S3). That link is the only identity join;
  no name-matching. A contact with no linked user has no CRM login.

### 1.4 Toggle component

- `FE/components/ui/toggle-group.tsx`, already used as an "All | ..." segmented filter in
  `FE/app/(protected)/procurement-management/spo-allocations/components/SPOAllocationsList.tsx:472`.
  #1438's own `FE/components/ideas/IdeasViewToggle.tsx` is the List | Board twin. The page is
  #1438's, so the My | All toggle needs no Lavish mock (owner exception).

## 2. Behaviour card (posted as the crew-ask; answers land in section 3)

### 2.1 Conversation flow (examples)

The examples use messages from the 24 Sep ideation UX review
(`REVIEW-ideation-flow-ux-24sep.md:75-166`). This sandbox cannot read the dev chat or idea data
(no shared DB access), so these are the closest real messages on record, not a fresh pull.

E1, no similar idea of their own: create at once.
```
User: i have an idea, the price tag should show promo price in red
Bot:  Idea IDEA-0184 is in: Promo price in red on price tags
      Open it to add what I didn't catch: Proposed solution, Impact, Department, Photos or files.
      https://<crm>/ideas/<id>
```

E2, similar ideas of their own exist: ask first, nothing is created yet.
```
User: chatbot should remember what the dealer already asked before
Bot:  You already have ideas like this:
      1. IDEA-0151 Chatbot remembers past dealer questions - https://<crm>/ideas/<id>
      2. IDEA-0097 Dealer chat history in replies - https://<crm>/ideas/<id>
      Reply 1 or 2 to edit that one, or NEW to log this as a new idea.
User: new
Bot:  (E1's created reply, built from the ORIGINAL message, not from "new")
User: 1   (instead of new)
Bot:  Open IDEA-0151 to add this: https://<crm>/ideas/<id>
```

E3, no content.
```
User: want to submit idea
Bot:  Sure - what's your idea? Tell me what you'd like changed, in one message.
User: sales team keeps getting asked why the price is different from the sticker
Bot:  (E1 or E2, from this message)
```

E4, everything in one message: nothing is missing, so no missing-field line.
```
User: idea for sales team: show promo price in red so customers stop asking why the sticker
      is different, saves the sales team repeating themselves
Bot:  Idea IDEA-0185 is in: Promo price in red on price tags
      Open it to add what I didn't catch: Photos or files.
      https://<crm>/ideas/<id>
```

### 2.2 What "similar" means (proposal)

- Matched by ss's existing pg_trgm check (the one behind `duplicate_candidate` today) on the
  extracted title + problem, at ss's current threshold, so no new tuning knob.
- Own ideas only: ss ideas whose submitter is this sender (WhatsApp phone = the contact's phone,
  or captured in the CRM by the CRM user linked to this contact). Never by name.
- Live ideas only: not test, not archived, not merged into another.
- At most 3, most similar first. More than 3: the top 3 plus "See all your ideas:
  https://<crm>/ideas?view=mine".

### 2.3 Minimal required fields

- Only the idea itself (ss `problem`). The title is generated, never asked.
- A message with no idea content (E3) gets the one ask-back; a second content-free reply gets the
  same ask-back (no loop counter, no draft).

### 2.4 Missing-field list

The fields the one message did not give, in this order: Proposed solution, Impact, Department,
Photos or files (always listed, since photos are no longer picked up from the chat).

### 2.5 Edge cases

| Case | Proposal |
|---|---|
| Sender has no CRM login (contact not linked to a user) | Idea still created; reply gives the idea number and the portal track link instead of the CRM link (Q2). |
| Linked user lacks `ideation.board.view` | Same as no login (Q2). |
| Many similar | Top 3 + "See all your ideas" (2.2). |
| User replies to the similar-list with something else (not 1-3 / NEW) | Treated as a fresh idea message: the flow runs again on it; the held message is dropped. |
| User never answers the similar-list | The held message is dropped after 24h; no reminder, no close call (nothing was created). |
| ss down | Same graceful reply as today; nothing held. |
| Open old-style drafts at deploy | Left to the existing 24h sweep to remind and close; no new draft is ever opened. |

### 2.6 Defaults this lane takes without asking (simplest thing that works)

- Replies are fixed templates (the owner's wording from Q5), not the LLM composer: one message,
  one create, so there is no recap or review step left for the composer to phrase.
- The media lookback and media menu are removed from the ideate turn.
- No CRM-side copy of ideas: ss stays the system of record.

### 2.7 Questions (the crew-ask)

1. Where do "own ideas", the one-shot create and the idea id come from? (a) A small ss lane
   (SS-IDEATION-OWN, like SS-PUBLIC-LINK-BASE): intake gains a one-shot create that returns the
   idea `id`, plus a "similar own ideas" lookup by submitter (2.2); the embed API gains a
   `mine=true` list filter and an `isMine` flag; the CRM assertion gains a `phone` claim (the
   linked contact's phone) so ss can match WhatsApp-submitted ideas to the CRM user. This lane
   builds the CRM half against that contract and ships after ss merges. (b) CRM-only: the CRM
   keeps its own ledger of ideas it created and does the similarity search in CRM Postgres.
   Recommendation (a): ss already holds the submitter and the pg_trgm check; (b) misses every
   idea from before today and anything captured in ss, and adds a table and a migration.
2. Sender with no CRM login, or no Ideas permission? (a) Still create; reply with the idea number
   and the portal track link, (b) do not create; tell them to ask for a CRM login. Recommendation
   (a): the idea is not lost, and the track page is the only page they can open.
3. May a submitter edit their OWN idea's fields (problem, solution, impact, department,
   attachments) with only `ideation.board.view`? Today edit needs `ideation.ideas.manage`, so
   "open it to fill the other fields" fails for most dealers. (a) Yes, own ideas only (ss decides
   ownership via `isMine`, the CRM never guesses), status/merge/archive stay manage-only, (b) no.
   Recommendation (a).
4. Similar-list reply: (a) as E2 (numbered, up to 3, "Reply 1 or 2 to edit that one, or NEW to
   log this as a new idea"; a number replies with that idea's link), (b) links only, "Reply NEW
   to log this as a new idea" (editing is just opening a link). Recommendation (b): one fewer
   branch, and the link already is the edit path.
5. Customer-visible wording (owner's call), please approve or rewrite:
   - created: "Idea {IDEA-0184} is in: {title}" / "Open it to add what I didn't catch:
     {missing list}." / "{link}"
   - nothing missing: the middle line is dropped except "Photos or files" (E4).
   - ask-back: "Sure - what's your idea? Tell me what you'd like changed, in one message."
   - similar: "You already have ideas like this:" ... "Reply NEW to log this as a new idea."
   - no CRM login: "Idea {IDEA-0184} is in: {title}" / "Track it here: {portal link}".
   Recommendation: as written, English only (the LLM composer's language-matching is dropped
   with the composer, 2.6).

## 3. Owner answers

| Q | Answer |
|---|---|
| Q1 | **(a)**, crew 2 Oct: crew spawned ss lane SS-IDEATION-OWN (one-shot create returning id + idea number, own-similar lookup by phone / CRM user id and never by name, embed `mine=true` + `isMine`, assertion `phone` claim). This lane builds the CRM side against that contract; SS-IDEATION-OWN posts the final API contract and crew relays it. |
| Q2 | **(b)**, owner 2 Oct: the sender MUST have a CRM login (a user linked by `users.respond_contact_id`, active) AND Ideas permission (`ideation.board.view`). Otherwise nothing is created; the reply says they need a CRM login with Ideas access. A clear message, never a stuck turn (no pointer is written). |
| Q3 | **Yes**, owner 2 Oct: a submitter may edit the fields of their OWN idea with `ideation.board.view`. They can NOT edit other people's ideas unless they hold the existing manage permission `ideation.ideas.manage` (admin / secretary type; today the gate on `PATCH /ideas/{id}`, `BE/app/api/v1/ideation/ideas.py:162-163`). Ownership comes from ss `isMine`, never from the CRM guessing. Status, merge, archive, delete stay manage-only. |
| Q4 | **(a) variant**, owner 2 Oct: a numbered list showing each similar idea's TITLE + link; the user replies a number to edit (the bot replies with that idea's link) or NEW to create from the ORIGINAL message. |
| Q5 | **Owner 2 Oct:** replies follow the USER'S language (the owner wants this for every chatbot ask). The idea number, title, links and the missing-field list stay exact and code-filled; only the surrounding sentence follows the user's language. Every reply is built by one function, `ideation_capture_replies.render_reply(kind, facts, *, user_message)`, so the language mechanism plugs in there; crew is scouting the existing translation module and relays the mechanism. Until then `render_reply` renders the card's English wording. This reverses default 2.6's "English only". |

Consequences:

- Q2 drops the "no login: portal track link" branch of 2.5; the no-access reply replaces it.
- Q3 adds slice E1: the gateway `PATCH /ideas/{id}` allows a `board.view` holder when ss reports
  `isMine` for that idea as that user, else 403; manage holders keep editing any idea. The detail
  page shows Edit when `isMine` or manage. ss should enforce the same (contract ask to
  SS-IDEATION-OWN, defence in depth).
- Q4 replaces 2.1 E2's reply: `1. {title} - {link}` lines, then "Reply a number to edit that one,
  or NEW to log this as a new idea." A number replies with that idea's link and drops the held
  message; NEW creates from the held original message.

## 4. CRM side of the SS-IDEATION-OWN contract (final: ss PR #111 head 8bcf160, `documentation/ideation/own-ideas-contract.md`; text relayed by crew 2 Oct; sections 4a.5-6b hold the exact names)

ss trust assumptions and how the CRM meets them:

- One CRM user-id namespace per tenant: `crm_user_id` / `sub` is `users.id` of this install.
- The `phone` claim must be a VERIFIED number. The CRM sends it only when `users.phone_verified_at`
  is set (proved by a WhatsApp sign-in code, `phone_signin_service.py:432`; cleared on a number
  change, `user_service.py:894,923`) AND `normalize_msisdn(users.contact_number)` equals
  `normalize_msisdn(` the linked contact's `phone_number)` (identity S3's "signs in by WhatsApp
  code" rule, `user_contact_link.py:126-130`). Otherwise no claim.

- Assertion: `mint_embed_assertion` gains a `phone` claim = `respond_contacts.phone_number` of the
  contact linked by `users.respond_contact_id`, under the verified rule above; the claim is absent
  when the user has no linked contact, the contact has no phone, or the phone is not verified. That is the same value the chatbot sends ss as
  `submitter_contact_id` (`ideation_turn_service.py:980`), so the join is exact, never by name.
- Gateway list: `GET /api/v1/ideation/ideas?mine=true` forwards `mine=true` to ss
  `GET /embed/ideas`; any other `mine` value is dropped. `isMine` on each idea is relayed as ss
  sends it (the gateway already relays bodies unchanged).
- FE: `IdeaListParams.mine`, `Idea.isMine`; the list toolbar gets a My ideas | All ideas toggle
  (`components/ui/toggle-group.tsx`), default All ideas (today's behaviour), state in the URL as
  `?view=mine` so the chatbot's "See all your ideas" link opens the right view.

## 4a. C1 design: the chatbot turn (ss shapes per the final SS-IDEATION-OWN contract)

New service `BE/app/services/ideation_capture_service.py`, `handle_capture_turn(db, *, respond_io_id,
message_text, session_vars_in=None, is_test=False)`; `POST /external/ideation/turn`
(`BE/app/api/v1/external/ideation.py:48`) calls it instead of `handle_turn`. Request fields
`media_selection` and `is_new_idea` are accepted and ignored (old n8n callers keep working).
Response keeps `{status, reply_text, link?, session_vars, offered_media: []}`.

Order per turn:

1. Contact by `respond_io_id` (404 as today). ss config not ready: today's "isn't set up" reply,
   status `unconfigured`.
2. Access gate (Q2): user with `users.respond_contact_id == contact.id`, `status == active`, not
   trashed, and `UserPermissionService.check_user_has_permission(user.id, "ideation.board.view")`
   (`BE/app/services/user_service.py:1538`). Else: status `no_access`, the no-access reply, no ss
   call, the `ideation` pointer cleared.
3. Held similar-list (`ideation.status == "similar_offered"`, same `is_test`, under 24h old):
   - a bare number 1..N: status `similar_picked`, reply that idea's link, pointer cleared;
   - `new` (any case, surrounding spaces and punctuation ignored): create from the HELD message;
   - anything else: pointer dropped, the message runs as a fresh idea message.
   An old draft-shaped pointer (`draft_id`, from before this lane) is dropped; its ss draft is
   left as is (ss drafts are not shown on the board).
4. Fresh message: the existing extractor (`extract_ideate_turn`, `ideation_extractor.py`) with no
   prior draft context; fields normalised as today. No `problem` extracted: status `ask_idea`,
   the ask-back reply, no pointer, no ss call.
   **Owner rule 2 Oct:** required-field collection goes through ONE shared helper, being built
   by LOWSTOCK-FILTER-ASK (#1445; config per ask type: required fields, take from the message if
   valid, ask only the missing ones). This lane builds no ask-back logic of its own: the check
   is one adapter, `_missing_required(fields) -> list[str]`, with ideation's required set
   `["problem"]`; when crew relays the helper API, the adapter's body becomes the helper call
   and the ask-back sentence comes from the helper's ask for the missing field.
   **Helper relayed 2 Oct:** `app/services/chatbot/required_fields.py` on #1445 (head 57af6b3e).
   It lives in the chatbot package and works through the engine's slot: `collect()` returns the
   question + a one-turn `slot`; on the next message `reply_verdict()` reroutes a reply back to
   the ask's lane with `required_ask` / `required_ask_reply`. Core (this service, behind the MCP
   tool and the external endpoint) may not import the package (AC-002), so the wiring sits in the
   ideate LANE (`app/services/chatbot/lanes/ideate.py`, inside the package):
   - the lane registers `AskType("ideation", fields=(FieldSpec("problem", noun="idea",
     question=<shipped English ask-back>, resolve=<any non-empty text is "ok">, allow_all=False),),
     reroute={ideate intent}, cancelled=..., give_up=...)`;
   - the core turn still DECIDES (its `_missing_required` over the extractor's reading, since
     "want to submit idea" is non-empty text the helper alone would accept) and returns
     `ask_idea`; on `ask_idea` the lane calls `collect(db, ask, given={})` and puts the returned
     slot on its envelope (`required_ask`), keeping the core's reply text, which is already in the
     user's language (Q5), instead of the helper's English `question`;
   - on the answering turn the lane receives `required_ask_reply` and calls the tool with that
     text as `message_text`; the core turn runs it as a fresh idea message.
   - Helper rules that now apply to ideation: a reply containing "?" or an empty message drops
     the question (an idea phrased as a question is then routed by the parser as usual); "cancel"
     ends it with the ask's `cancelled` text.
   Order: #1445 merges first; this lane then brings it in (crew coordinates) and wires the lane.
   **Crew answers 2 Oct:** (1) = (a): the lane registers `AskType("ideation",
   FieldSpec("problem"))` and uses `collect` / the engine's `reply_verdict`; core only checks
   presence (AC-002 holds). (2) = (a): the core's localised ask-back text is what the user sees;
   **`FieldSpec.question` is unused for ideation** (kept as the English fallback the helper
   requires). CHAT-LANGUAGE (#1448) moves localisation into the helper later (option b).
   **Vague reply never loops silently (crew):** "want to submit idea" twice must end in a clear
   message. Flow: message 1 with no idea -> core `ask_idea` (localised ask-back), lane opens the
   slot. The reply arrives via `required_ask_reply`; the lane calls the tool with
   `ask_reply: true`. If the core still finds no idea in it -> status `ask_idea_gave_up`, a
   localised closing reply (`ideation_capture_give_up`: en "I still couldn't find an idea in that.
   When you're ready, send your idea in one message, for example what you'd like changed."), and
   the lane opens NO new slot. So the second vague message always ends the ask with a clear
   message, never a third ask. A reply that does carry an idea runs the normal flow. The core
   response carries `language` so the lane never guesses it.
5. Similar own ideas (FINAL contract, ss#111 section 2): `POST /ideation/intake/ideas/similar-own`
   `{product_id, text (= the extracted problem), submitter_crm_user_id (the linked user's id),
   submitter_phone (the contact phone), is_test}` -> `{matches: [{idea_id, idea_number, title,
   problem, status, status_label, similarity, created_at, link}]}`, at most 3, best first, own
   ideas only, live only, pg_trgm >= 0.3. Any match: status `similar_offered`, pointer
   `{status, message_text, fields, title, language, similar: [{idea_id, idea_number, title}],
   updated_at, is_test}`, the numbered reply (Q4). ss returns no total, so the "See all your
   ideas" line (`{FRONTEND_BASE_URL}/ideas?view=mine`) shows when ss returned the full 3 (there
   may be more).
6. Create (FINAL, ss#111 section 1): `POST /ideation/intake/ideas` -> 201, flat fields:
   `product_id`, `problem`, `submitter_crm_user_id`, `submitter_phone`, `submitter_name`,
   `title`, `proposed_solution`, `impact`, `department` (each only when extracted),
   `submitter_tier`, `raw_transcript` (= the one message), `is_test`, `intake_ref` -> `{idea_id,
   idea_number, status: "captured", title, link}`. `intake_ref` = a uuid minted when the create
   is first decided and kept on the held pointer, so a NEW retried after an ss timeout returns
   the same idea instead of a second one (a fresh one-message create gets a fresh uuid; the
   turn request carries no Respond.io message id to use instead). Reply: created, CRM link
   `{FRONTEND_BASE_URL}/ideas/{idea_id}`; ss returns no `captured`, so the missing list is
   computed from the fields the CRM sent: Proposed solution, Impact, Department not sent, then
   Photos or files. Pointer cleared. The ss public `link` is not used for the reply.
6a. Errors: ss errors are `{"error": {"code", "message"}}`; any 4xx/5xx or transport failure is
   the graceful `error` reply (codes are logged, never shown).
6b. Embed assertion (ss#111 section 4): `ideas_manage` = a JSON boolean, true when the user
   holds `ideation.ideas.manage` (`UserPermissionService.check_user_has_permission`, which
   honours superadmin/admin), so ss itself refuses a non-manager's edit / status / delete /
   reorder / merge / unmerge / attach on someone else's idea (403 `not_owner`). Part of the
   token cache key, as the phone is. Consequence for #1438's page: upload on someone else's
   idea is refused by ss for a non-manager, so the detail page hides the attachment upload
   unless `canManage || isMine`.
7. ss failure anywhere: today's graceful "couldn't save" reply, status `error`, pointer untouched.
8. `is_test`: pointer never persisted (as today); `is_test` sent to ss.

Wording and language (Q5, mechanism relayed by crew 2 Oct):

- The copy lives in ONE place, `BE/app/services/chatbot_reply_copy.py` `FALLBACK_REPLY_COPY`,
  as `ideation_capture_*` entries with `en` / `ms` / `zh` texts and declared `{{tokens}}`, the
  same shape as the fallback copy there (`:247-345`), so CHAT-LANGUAGE can migrate it with the
  rest. Values (idea number, title, links, the missing-field names) are tokens filled by code;
  the missing-field names stay in English in every language (owner: "exact").
- `render_reply(kind, facts, *, user_message, language)` in `ideation_capture_replies.py` is the
  one function every reply goes through. It picks the `.ms` / `.zh` key the way
  `chatbot/copy.py:57` `render_in` does and resolves the text through `ai_prompt_registry` (an
  owner edit wins, the shipped text is the fallback).
- Why not call `copy.render_in` / `fallback.pick_language` directly: core must never import
  `app.services.chatbot` (AC-002, `tests/chatbot/test_import_boundary.py`), and this service is
  core (the external endpoint calls it). So `ideation_capture_replies.py` carries a ~10 line twin
  of `render_in` over the same core tables (`FALLBACK_LANGUAGES`, `language_suffix`), and the
  language rule of `pick_language` (first known of en/ms/zh, else en). CHAT-LANGUAGE should lift
  both into core and delete the twin.
- Which language: the extractor already reads the message once per turn; its schema gains
  `language` (`en` / `ms` / `zh` / null). A held similar-list stores the language of the
  ORIGINAL message, so a bare "2" or "NEW" (no language signal) answers in that language. The
  contact's saved profile language is not on this path (the engine reads it, the external
  endpoint does not); trigger to add it: CHAT-LANGUAGE.
- No-access and unconfigured replies also follow the language, so the extractor runs before
  those gates (one LLM call, no ss call).
- No seed migration for the new keys in this lane: the registry falls back to the shipped text
  when a key has no row (`515_chatbot_offer_declined_copy.py` docstring), so the bot speaks
  either way. Owner editing in Settings > AI Prompts needs the seed; one-line migration when the
  owner asks for it. The CRM link is built only from `settings.frontend_base_url` and
the ss `id` validated as a UUID; no ss-supplied URL is relayed for the CRM link.

C2 (after C1 green): remove the dead draft path (`handle_turn`, media lookback, the composer's
draft statuses) and the tests that only covered it; the idle sweep stays until old pointers drain.

## 4a-bis. Live-LLM findings (cloud pass at 15042c21) and fixes (lead decision 3 Oct, owner wants hand tests now)

- **C5** "want to submit idea" was created as an idea: the live extractor invents `problem: "We need
  a way to submit ideas."`, so `_missing_required` never sees a missing problem. Fix: an extractor
  prompt rule (an intent-only message such as "want to submit idea" / "I have an idea" with no idea in
  it has NO problem; never write a problem the user did not state), shipped as the fallback text of
  `ideate_extractor` in `ai_prompt_registry.py`, plus ONE migration that runs
  `bump_prompt_to_fallback(op.get_bind(), "ideate_extractor")` (production reads the registry row)
  and `seed_prompt_registry(op.get_bind())` (seeds the `ideation_capture_*` copy keys, so the owner
  can edit them). This answers the earlier open migration ask.
- **C4a** the same message matched once and not the next time: similar-own got the model's rewrite
  of the problem as `text` (0.432 vs 0.280 against 0.3). Fix: send the user's raw message as `text`
  (stable input). Follow-up ask to ss: also match against the stored `raw_text`.

Review of that fix (7db57216), lead decisions:
- **B1** the new rule competed with the older "every draft has one from its very first message ...
  ALWAYS emit problem" line, and the capture turn always sends status 'new'. The problem field's own
  description is rewritten: a stated idea still gets the need behind it as its problem; an
  intent-only message gets none. One instruction for `problem`, not two.
- **S1** the migration no longer overwrites an owner edit: it freezes the previous stock text
  (`OLD_IDEATE_EXTRACTOR`) and publishes the new fallback only when production still equals it
  (precedent `393_po_schedule_extractor_page_text.py`); an owner-edited production version is left
  alone with a warning, and the owner adds the rule by hand in Settings > AI Prompts.
- **S2/S3** migration tests find the revision by id (survives the pre-merge re-parent) and cover an
  existing old version being upgraded once.

Owner hand test at 9b242232 (3 Oct): "I have an idea" got "couldn't save". The dev DB serves an
owner-edited `ideate_extractor` (v56) that the migration rightly leaves alone, so the prompt rule
never reached it; the model invented a problem and the similar-own call hit a down ss. Decision: the
"is there an idea?" signal moves into the extractor's structured-output SCHEMA (code, applies under
any prompt version): required boolean `has_idea`; `has_idea == false` means the idea is missing,
whatever `problem` says. The prompt rule stays as guidance.

## 4b. Lane process (owner rule 3 Oct, LEAD PATTERN)

From 3 Oct the lane runs on the lead pattern: the lead (this session) edits plans, docs and merge
conflicts only; a TESTER subagent writes red tests, a CODER subagent implements, a separate REVIEWER
subagent reviews. Each subagent run is capped at 60 minutes, with progress every 20. Work finished
before the rule is not redone. Remaining slice under it: H1 (#1445 `required_fields` wiring).

## 5. Slices

| # | Slice | Depends on | State |
|---|---|---|---|
| P1 | Plumbing: phone claim (verified only), `ideas_manage` claim, `mine` forward, My/All toggle (section 4) | Q1 | done |
| C1 | Chatbot one-message flow (section 2.1-2.5, Q2 access gate, Q4 numbered list, Q5 language) | SS-IDEATION-OWN contract | done |
| C2 | Remove the dead multi-turn draft path and media lookback | C1 | done |
| E1 | Own-idea edit: gateway PATCH allows `isMine` or manage; Edit and upload shown on own ideas | Q3 | done |
| R1 | Review fixes: sweep ignores held lists, chatbot keeps a held list in the ideate lane, embed-session route claims (security H1), DB-only held list for live turns (L1), no-link replies, URL-derived My/All state, merged-child upload, vague-reply end (core), re-read before write | Phase 3 | done |
| H1 | Wire #1445 `required_fields` into the ideate lane (AskType, slot, `ask_reply: true` on the answer) | #1445 merged | done |
