"""The chatbot's door to `app.services.prompt_gates` (core, so the Field reveals route can
read the same table without importing this package)."""
from app.services.prompt_gates import (  # noqa: F401
    PROMPT_GATES,
    PromptGate,
    gate_for,
    grant_for_domain,
    grant_for_intent,
    grant_for_order_status,
    prompt_blocks_for,
    render_for_audience,
)
