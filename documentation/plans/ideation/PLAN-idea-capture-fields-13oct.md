# PLAN: idea capture collects the canny-style fields (IDEA-CAPTURE, 13 Oct)

Status: Build (Q2, Q4, Q5 answered by crew 13 Oct; Q1, Q3 with the owner).

Decisions (13 Oct): Q2 one per line. Q4 use cases only as named, max 5, never suggested.
Q5 always send. Contract fact from IDEA-CORE: ss origin/main 04b48d5c `OneShotIdeaIn`
(schemas.py:706) has pydantic default `extra='ignore'`, so an older ss drops unknown keys.
Names agreed with IDEA-CORE #124: `problem`, `desired_outcome` (str), `use_cases` (list[str]),
`current_workaround` (str). The CRM FE label rename is a separate lane after ss lands; this lane
leaves the FE alone. Track: full lane (prompt-registry migration). Lane IDEA-CAPTURE, branch `claude/idea-capture-chatbot-1ozsz5`, pair lane IDEA-CORE (shared service).

## Owner ask (13 Oct)

Ideas collect **Business problem** (renamed from Problem statement), **Desired outcome**,
**Use cases**, **Current workaround** (canny.io style). Proposed solution, Impact and Department
are dropped from display; their stored data is kept. IDEA-CORE owns the ss model + intake API;
this lane owns the CRM chatbot side.

## Facts read from the code (not guessed)

- The LIVE ideate turn is `ideation_capture_service.handle_capture_turn`
  (`sorento_crm_backend/app/services/ideation_capture_service.py:207`). It creates through
  `POST /ideation/intake/ideas` (`:56`, `:384-405`), not `/ideation/intake/create-idea`.
  `ideation_turn_service.call_create_idea` (`ideation_turn_service.py:267`) and its
  `create-idea` path now serve ONLY the S4 idle sweep's `cancel: true` close of old draft
  pointers (`:376-449`); that path carries no idea fields and is not changed here.
- Which fields get sent and which get listed as missing is one tuple:
  `_OPTIONAL_FIELDS` (`ideation_capture_service.py:63-67`), read by the payload loop
  (`:393-395`) and `_missing_list` (`:201-204`). `problem` is the one required field (`:68`).
- The extractor: `extract_ideate_turn` (`ideation_extractor.py:302`), schema
  `IDEATE_EXTRACTION_JSON_SCHEMA` (`:77`), skippable keys (`:68`), department normaliser
  (`:270-286`). Its system prompt is the `ideate_extractor` registry key
  (`ai_prompt_registry.py:200`, `:1106`). Production reads the registry ROW, and the dev DB
  already serves an owner-edited version (`PLAN-ideation-capture-02oct.md` 4a-bis), so a
  fallback-only change does not reach an edited install. Anything that must hold under every
  prompt version goes into the structured-output SCHEMA (precedent: `has_idea`).
- The reply: `IDEATION_CAPTURE_COMPLETE` (`chatbot_reply_copy.py:318`), rendered by
  `ideation_capture_replies.render_reply` (`missing` joined with ", "). The copy keys are
  registry keys too (seeded by `ideation_capture_0001`).

## Behaviour card (one ask to the owner)

### Rules

1. One WhatsApp message is still one idea (no draft, no follow-up questions). The LLM reads the
   message and fills, only from what the user said:
   - **Business problem** (required): what is wrong or missing today, including the cost the
     user names ("wastes an hour a day"). Sent to ss as `problem` (field renamed in display
     only; IDEA-CORE keeps the column).
   - **Desired outcome**: what the user wants to be true afterwards.
   - **Use cases**: each concrete situation the user names, one entry each (a list).
   - **Current workaround**: how they cope today.
2. Truthful: a field the user did not give stays EMPTY and is named as missing. The bot never
   writes a desired outcome, use case or workaround the user did not state.
3. No longer extracted or sent: Proposed solution, Impact, Department.
4. Understanding is the LLM only (no regex or keyword rules). The field keys and what each one
   means sit in the extractor's output schema, so they hold under an owner-edited prompt too.
5. Reply: one fact per line. The missing fields are listed one per line, not comma-joined.
   Field names stay in English in every language (owner ruling "exact", 2 Oct).
6. Similar-own check, ask-back on "I have an idea", held list, NEW, links: unchanged.

### Examples (fake, realistic)

E1 (en)
> Sales reps retype WhatsApp orders into the CRM by hand, about an hour a day each. Right now we copy into Excel first then key in.

- Business problem: Sales reps retype WhatsApp orders into the CRM by hand, about an hour a day each.
- Current workaround: Orders are copied into Excel first, then keyed into the CRM.
- Desired outcome, Use cases: empty.

Reply:
```
Idea IDEA-0123 is in: Retyping WhatsApp orders into CRM
Open it to add what I didn't catch:
- Desired outcome
- Use cases
- Photos or files
https://crm.example/ideas/...
```

E2 (ms)
> Stok gudang selalu tak sama dengan sistem. Nak sistem update sendiri bila barang keluar supaya sales nampak stok sebenar.

- Business problem: Stok gudang selalu tidak sama dengan sistem.
- Desired outcome: Sistem dikemas kini sendiri apabila barang keluar supaya sales nampak stok sebenar.
- Missing: Use cases, Current workaround, Photos or files (one per line, English names, Malay sentence).

E3 (en, all four)
> Customers keep asking us where their delivery is. Want them to check it themselves. E.g. a dealer checking if his PO shipped, a project customer checking the site delivery date. Now we WhatsApp logistics and wait for them to reply.

- Business problem: Customers keep asking staff where their delivery is.
- Desired outcome: Customers can check their delivery status themselves.
- Use cases: A dealer checks whether his PO has shipped. / A project customer checks the site delivery date.
- Current workaround: Staff WhatsApp the logistics team and wait for a reply.
- Missing: Photos or files.

E4 (zh)
> 报价单每次都要手动算折扣，经常算错。

- Business problem: 报价单的折扣每次都要手动计算，经常算错。
- Everything else empty and listed as missing.

E5 (intent only)
> I have an idea

- No idea: the existing ask-back, unchanged.

### Questions (answer by number; each has my recommendation)

1. A message that only names a SOLUTION ("we should build an app to scan orders"): where does
   that go now that Proposed solution is gone? **Recommendation:** Business problem gets the
   need behind it, as today ("We need a way to scan orders."), never an invented cause; the
   stated solution goes to Desired outcome in the user's words ("An app to scan orders."). It
   is what they said they want, so nothing is invented.
2. Missing fields one per line ("- Desired outcome") instead of "Desired outcome, Use cases".
   **Recommendation:** one per line (the one-fact-per-line rule).
3. Field names in Malay / Chinese replies: keep English (your 2 Oct ruling) or translate?
   **Recommendation:** keep English, so the names match the CRM page exactly.
4. Use cases: only situations the user actually names, up to 5, each one line.
   **Recommendation:** yes; a restatement of the problem is not a use case.
5. While ss is not yet deployed with the new fields: **Recommendation:** send the new fields
   only once ss accepts them (fact asked of IDEA-CORE in the PR); until then the idea is created
   with Business problem + title + the raw message, so nothing the user said is lost.

## Implementation (after the card is answered)

1. Extractor (`ideation_extractor.py`): schema `fields.items.key` becomes an enum
   `problem | desired_outcome | current_workaround` with each meaning in the description; a new
   required `use_cases: string[]` property; `_SKIPPABLE_KEYS` becomes
   `{desired_outcome, use_cases, current_workaround}`; the department normaliser is removed.
   Fallback prompt rewritten for the four fields; old keys removed.
2. Capture turn (`ideation_capture_service.py`): `_OPTIONAL_FIELDS` becomes
   Desired outcome / Use cases / Current workaround; `use_cases` sent as a list; held pointer
   carries `use_cases`; compatibility per Q5.
3. Reply copy: `ideation_capture_complete` (en/ms/zh) lists `{{missing}}` on its own lines;
   `render_reply` joins with "\n- ".
4. Migration: publish the new `ideate_extractor` fallback and the three
   `ideation_capture_complete*` texts ONLY where production still equals a stock text (the
   `ideation_capture_0001` precedent); an owner-edited version is left alone with a warning
   (the schema still carries the new keys).
5. Tests red-first (tester), then coder, then kill tests + reviewer.
6. Golden set: at least 8 varied fake ideate messages run through the live extractor (fallback
   prompt AND, when crew supplies it, the production prompt text) before the hand test.

## UAC

`idea-capture-fields-13oct-acceptance-criteria.md` (written after the card is answered).
