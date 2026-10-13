# UAC: PARSER-GEMINI-LITE

- AC-1: The real `PARSE_OUTPUT_JSON_SCHEMA`, sent through `GeminiProvider.chat`, produces a
  `responseSchema` with one string `type` per node, `items` only on arrays, `properties`
  only on objects, and no null in any enum.
  (`tests/chatbot/test_parser_gemini_lite.py`)
- AC-2: With `reasoning_effort` unset, the OpenAI request carries no `reasoning_effort`
  and the Gemini thinking budget is unchanged for every model. With it set, OpenAI receives
  it verbatim, and Gemini gets the mapped budget plus the same added amount on
  `maxOutputTokens`. (`tests/test_llm_provider.py`)
- AC-3: `agent_reasoning_effort` returns None for an unset label and the label's own value
  when one is set, and `resolve_config` carries that value. (`tests/test_ai_prompt_registry.py`,
  `tests/chatbot/test_parser_gemini_lite.py`)
- AC-4: The `understood` facts carry provider, model, model_answered, reasoning_effort,
  latency_ms, prompt / cached / completion / reasoning tokens. A failed parse carries
  provider, model and latency_ms.
- AC-5: A contact whose respond.io id is on two `respond_contacts` rows still gets its
  `ai_assistant_usage_logs` row.
