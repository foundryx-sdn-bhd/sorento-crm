# PLAN: parser on Gemini, per-label reasoning effort, parser model trace (PARSER-GEMINI-LITE)

Status: implemented on PR #1624, awaiting review and the crew's dev eval with the label on
gemini-3.5-flash-lite. Track: small diff (about 250 lines of code), but it carries one
additive migration, so it is not strictly the small fix track; the lane brief allowed the column.

## Why

Owner, 13 Oct: try gemini-3.5-flash-lite as the chatbot semantic parser model. The crew compared
two hard cases (one was the photo loop); flash-lite broke the loop at about 3.1-3.3 s per parse,
and gpt-5.4-mini, the current model, kept looping. This lane does not switch the label. The model
stays in `ai_prompt_labels`, and the owner decides the switch after the eval.

## Changes (no behaviour change while the label leaves the new field unset)

1. `llm_provider._gemini_schema`: a multi-type field (`["string","array"]`) becomes one
   `anyOf` branch per type, each keeping only its own keys. A null member sets `nullable` on
   every branch. Before this, `profile_statements[].value` went out as a string that still
   carried `items`, and Google rejected every parser call with a 400.
2. `ai_prompt_labels.reasoning_effort` (nullable VARCHAR(16), additive migration
   `pgl_0001_label_reasoning_effort`). `agent_reasoning_effort` reads it, `ParserConfig`
   carries it, and `parse` sends it only when it is set. OpenAI receives it as
   `reasoning_effort`. On Gemini, `low` / `medium` / `high` map to a thinking budget of
   1024 / 8192 / 24576; anything else keeps the old default.
3. `ChatResult.model` (the id the provider says answered) and `ChatResult.reasoning_tokens`.
   The parser's `usage` and the `understood` facts carry provider, model, model_answered,
   reasoning_effort, latency_ms and the input / cached / output / reasoning token split.
4. `chatbot/usage.record_parser_usage`: `.scalar()` on the non-unique
   `respond_contacts.respond_io_id` raised `MultipleResultsFound`. The `except` swallowed it,
   so the usage row was lost. The oldest row now stands for the contact.

## Storage choice

The label row, not `ai_prompt_versions.config_json`. Effort tunes the model, and the model
lives on the label. A version's config would be lost on the next prompt publish, and it would
apply to every label that points at that version.

## Setting it (no UI in this lane)

`UPDATE ai_prompt_labels SET provider='gemini', model='gemini-3.5-flash-lite',
reasoning_effort=NULL WHERE name='chatbot_semantic_parser' AND label='production';`
Add a Prompts-screen field only if the eval leads to a switch.
