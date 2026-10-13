# PLAN PV2-IDEAS (13 Oct night)

Status: in progress (size L lane per crew brief; contract scope S/M)

Contract: `documentation/plans/chatbot/CONTRACT-PV2-13oct.md` section 5 "Ideation" and section 9 lane PV2-IDEAS-dialogue.

Owns `sorento_crm_backend/app/services/chatbot/pv2/ideas.py`; additive kwargs only in
`ideation_capture_service.py` and `ideation_capture_replies.py`. Tests `tests/chatbot/pv2/ideas_*`.

1. (a) similar-ideas list becomes PendingQuestion(kind="idea_similar", options 1..N + new); a pick arrives as
   `answers_bot_question` + `option_key` and reaches `handle_capture_turn` as `choice_in`.
2. (b) reply to the missing-field question (kind="idea_missing_field") updates the held draft (`update_held=True`).
3. (c) `ideation.idea_count >= 2` -> PendingQuestion(kind="idea_multi", "N ideas: save both?").
4. Missing optional fields are listed in the reply, not asked.
5. Without the new kwargs, v1 behaviour is byte-identical (regression tests on the existing ideation suites).

## Design (binding for tester + coder)

### A. `ideation_capture_service.handle_capture_turn`: two additive kwargs

`choice_in: int | Literal["new"] | None = None`, `update_held: bool = False`. With both at their defaults every
path is byte-identical to today (same statuses, replies, pointers, ss payloads).

- `choice_in` (s.5a): when not None, the digit/"new" TEXT check (`:270-277`) is skipped. With a valid held similar
  pointer: an int in `1..len(similar)` is that pick, `"new"` is NEW; anything else (out of range, wrong type) is
  no choice and the turn runs as a fresh message (today's fallback). No held pointer: fresh message.
- `update_held` (s.5b), v2 draft mechanics on:
  - `ask_idea` (not give-up) persists pointer `{"status": "draft_held", "message_text", "fields", "title",
    "updated_at", "is_test"}` (+ `"language"` when the extractor's language is in FALLBACK_LANGUAGES). `fields` are
    the cleaned fields WITHOUT `problem` when `has_idea is False`.
  - `ask_reply=True` with a valid `draft_held` pointer (same `is_test`, under 24 h, same read rules as the similar
    pointer, both shapes for a test turn): fields = `{**held.fields, **new_fields}`; title = new title or held title;
    the required check runs on the MERGED fields; the similar-own lookup `text` and `raw_transcript` are
    `held.message_text + "\n" + text_in`. One idea, never two. Give-up drops the pointer.
  - `complete` persists `{"status": "captured", "idea_id", "idea_number", "title", "updated_at", "is_test"}`.
  - `similar_picked` persists the same `captured` shape for the PICKED idea.
  - a test turn never persists (unchanged); the pointer still rides in the returned `session_vars`.

### B. `pv2/ideas.py` (pure mapping + calls into the capture service; no text matching)

```python
IDEA_PENDING_KINDS = frozenset({"idea_similar", "idea_missing_field", "idea_multi"})
@dataclass
class IdeaTurnResult:
    status: str; reply_text: str; language: str; session_vars: dict
    pending: dict | None          # PendingQuestion shape (contract s.3) as a plain dict
    idea_draft_id: str | None     # for Held.idea_draft_id
    link: str | None = None
def run_idea_turn(db, *, ask, language, respond_io_id, message_text, session_vars, pending,
                  turn_no, is_test=False, submitter_name=None) -> IdeaTurnResult | None
```
`ask` / `pending` are the contract models or plain dicts (read by attribute or key). `None` = not an idea turn
(an `answers_bot_question` whose `option_key` is not EXACTLY one of the pending option keys: CARRY's miss path).

1. `act == "answers_bot_question"` and `pending.kind` in IDEA_PENDING_KINDS:
   - `idea_similar`: matched option payload `{"choice": n | "new"}` -> capture with `choice_in=<choice>, update_held=True`.
   - `idea_multi`: option key `"1"` (payload `{"choice": "one", "message_text": <original>}`) -> capture the ORIGINAL
     message with `update_held=True`; option key `"2"` (payload `{"choice": "separate"}`) -> no ss call, reply = `render_reply("ask_idea")`, status
     `ask_idea`, pending `idea_missing_field`.
   - `idea_missing_field`: capture `message_text` with `ask_reply=True, update_held=True` (option_key not needed).
2. Otherwise:
   - `ask.ideation.adds_to_held_idea` and `session_vars["ideation"]` is a `captured` pointer (same is_test, < 24 h):
     no ss call, reply = `render_reply("similar_picked", {idea_number, link})` (the idea's "open it to add this"
     line; there is no ss update API), status `held_idea_open`, `idea_draft_id` = that idea.
   - `ask.ideation.idea_count >= 2`: no ss call; pending `idea_multi` (crew answer 13 Oct: option (a); a
     multi-idea extractor that saves both is a follow-up, out of scope tonight; option keys `"1"` = "Save as one
     idea", `"2"` = "Send them one at a time", the numbers the reply prints); reply = asked line + `1. <label>` lines, `asked` = the asked line only; status
     `idea_multi_offered`. Copy en/ms/zh lives in ideas.py tonight.
   - else capture `message_text` with `update_held=True`.
3. Result mapping: `similar_offered` -> pending `idea_similar`, options `"1".."N"` (label = idea title, payload
   `{"choice": n}`) + `"new"` (label localized "New idea", payload `{"choice": "new"}`), `asked` = the reply's lead line (the numbered lines carry CRM links with ids);
   `ask_idea` -> pending `idea_missing_field` (no options); `complete` / `similar_picked` -> `idea_draft_id` from
   the `captured` pointer; anything else -> no pending. `asked_turn = turn_no`, `misses = 0`, `v1_kind = None`.
   Option keys and labels never carry ids/UUIDs. Language: the capture result's; `mixed` -> `en`.
