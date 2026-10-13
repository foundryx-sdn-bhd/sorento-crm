# PLAN: per-turn token + cost dataset in the Claude analytics connector (CHAT-TURN-COST)

Status: Plan (behaviour card in progress). Track: standard (migration: additive view + price table).

Owner request 13 Oct: make per-chatbot-turn token consumption and cost queryable through the
analytics connector (`chat_list_datasets` / `chat_describe_dataset` / `chat_query`), so the owner can
analyse cost per turn / conversation / contact / category and latency in claude.ai.

Behaviour card and decisions follow in the next commit.
