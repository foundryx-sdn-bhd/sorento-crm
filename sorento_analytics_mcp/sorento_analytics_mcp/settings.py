"""Environment for the container. No shared API key and no database URL exist here by design."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AnalyticsSettings:
    crm_base_url: str
    internal_key: str
    public_base_url: str
    request_timeout_seconds: float = 60.0
    max_response_bytes: int = 8_000_000
    # `sorento_crm_mcp._compile_tool` templates read this attribute when they call the
    # escalation side call. That call is neutralised (see tools.py); the empty value is
    # what guarantees the shared key can never be sent even if it were not.
    external_api_key: str = ""

    @classmethod
    def from_env(cls) -> "AnalyticsSettings":
        def need(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise RuntimeError(f"{name} is required")
            return value

        return cls(
            crm_base_url=need("CRM_BASE_URL").rstrip("/"),
            internal_key=need("ANALYTICS_CONNECTOR_INTERNAL_KEY"),
            public_base_url=need("ANALYTICS_CONNECTOR_PUBLIC_BASE_URL").rstrip("/"),
            request_timeout_seconds=float(os.environ.get("ANALYTICS_MCP_TIMEOUT", "60")),
        )
