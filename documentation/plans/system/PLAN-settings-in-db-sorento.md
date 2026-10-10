# PLAN: prod env switches move to DB settings (SETTINGS-IN-DB-SORENTO)

Status: Plan (behaviour card pending owner answers). Track: full lane (touches admin permission + secrets).

Owner ask (10 Oct 11:20): "for these prod steps, can we make it UI configurable and store in DB,
I am reluctant to set .env variables".

Scope: RESPOND_WEBHOOK_SECRET (#1402), CHAT_TOPIC_LABELLER_ENABLED (#1551), connector settings
(#1547), plus any other env-only switch the 9-10 Oct releases added. Behaviour card to follow.
