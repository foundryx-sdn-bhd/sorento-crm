"""The credentials PROMPT-DIET embeds with, resolved while the engine's config session is open."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings


@dataclass(frozen=True)
class EmbeddingAccess:
    """The key never reaches a log or the trace (`repr=False`)."""

    api_key: str = field(repr=False)
    model: str


def resolve_embedding_access(db: Session) -> EmbeddingAccess:
    """The OpenAI key from `ai_assistant_configs`, and nothing else: no environment key, and a
    key stored for another provider is not an OpenAI key. An empty key is carried, and
    `prompt_diet.embed_text` refuses it."""
    from app.services.ai_assistant_service import AIAssistantConfigService

    config = AIAssistantConfigService(db).get()
    openai = (config.provider or "").strip().casefold() == "openai"
    return EmbeddingAccess(
        api_key=(config.api_key_ciphertext or "") if openai else "", model=settings.embedding_model_name
    )
