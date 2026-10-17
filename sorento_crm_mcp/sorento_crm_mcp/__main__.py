"""Run MCP server (streamable HTTP)."""
from __future__ import annotations

import logging
import sys

import uvicorn

from sorento_crm_mcp.server import create_http_app
from sorento_crm_mcp.settings import Settings


def main() -> None:
    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as e:
        print("Configuration error:", e, file=sys.stderr)
        print("Set CRM_BASE_URL, EXTERNAL_API_KEY and MCP_AUTH_TOKEN (see README).", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        app = create_http_app(settings)
    except RuntimeError as e:
        print("Configuration error:", e, file=sys.stderr)
        sys.exit(1)
    # Same server FastMCP.run_streamable_http_async starts, serving the bearer-gated app.
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
