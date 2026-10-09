"""Environment configuration for the MCP server."""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """CRM MCP settings (env-prefixed CRM_MCP_ or plain names where noted)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    crm_base_url: str = Field(
        ...,
        validation_alias=AliasChoices("CRM_BASE_URL", "crm_base_url"),
        description="Base URL of Sorento CRM API, e.g. https://api.example.com",
    )
    external_api_key: str = Field(
        ...,
        validation_alias=AliasChoices("EXTERNAL_API_KEY", "external_api_key"),
        description="Same X-API-Key as FastAPI EXTERNAL_API_KEY",
    )
    request_timeout_seconds: float = Field(
        60.0,
        validation_alias=AliasChoices("CRM_MCP_TIMEOUT", "request_timeout_seconds"),
    )
    max_response_bytes: int = Field(
        8_000_000,
        validation_alias=AliasChoices("CRM_MCP_MAX_RESPONSE_BYTES", "max_response_bytes"),
    )
    mcp_host: str = Field("0.0.0.0", validation_alias=AliasChoices("CRM_MCP_HOST", "mcp_host"))
    mcp_port: int = Field(8765, validation_alias=AliasChoices("CRM_MCP_PORT", "mcp_port"))
    log_level: str = Field("INFO", validation_alias=AliasChoices("CRM_MCP_LOG_LEVEL", "log_level"))
    mcp_auth_token: str | None = Field(
        None,
        validation_alias=AliasChoices("MCP_AUTH_TOKEN", "mcp_auth_token"),
        description=(
            "Shared bearer token every MCP caller sends as `Authorization: Bearer <token>`. "
            "Required to serve HTTP: the server refuses to start without one (32+ chars)."
        ),
    )
    mcp_allowed_hosts: str = Field(
        "",
        validation_alias=AliasChoices("CRM_MCP_ALLOWED_HOSTS", "mcp_allowed_hosts"),
        description=(
            "Comma-separated Host header values the server answers (`host:*` matches any "
            "port). Set = DNS-rebinding protection on (other hosts get 421); empty = off."
        ),
    )

    outline_base_url: str = Field(
        "https://doc.foundryx.my",
        validation_alias=AliasChoices("OUTLINE_BASE_URL", "outline_base_url"),
        description="Base URL of the Outline workspace hosting the user guides.",
    )
    outline_api_token: str | None = Field(
        None,
        validation_alias=AliasChoices("OUTLINE_API_TOKEN", "outline_api_token"),
        description="Outline API token used by the user-guide search/read tools.",
    )
    outline_collection_id: str = Field(
        "18f78b01-bf5a-4032-934c-d5679609d553",
        validation_alias=AliasChoices("OUTLINE_COLLECTION_ID", "outline_collection_id"),
        description="UUID of the Sorento CRM user-guides collection.",
    )

    mcp_response_envelope: str = Field(
        "v1",
        validation_alias=AliasChoices("MCP_RESPONSE_ENVELOPE", "mcp_response_envelope"),
        description=(
            "Response envelope version. 'v1' = raw backend JSON (legacy passthrough). "
            "'v2' = wrapped {status, data, pagination, summary, warnings, provenance, "
            "escalation, questions}. Roll forward per-agent."
        ),
    )
    mcp_expose_internal: bool = Field(
        False,
        validation_alias=AliasChoices("MCP_EXPOSE_INTERNAL", "mcp_expose_internal"),
        description=(
            "When false (default), tools with ToolSpec.internal=True are skipped from "
            "FastMCP registration so the agent never sees admin-only procurement / GRN tools. "
            "Set to true for admin n8n flows that need raw receipt data."
        ),
    )
