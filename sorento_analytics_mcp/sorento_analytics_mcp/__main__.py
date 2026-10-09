"""Run the connector container."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "sorento_analytics_mcp.app:create_app",
        factory=True,
        host=os.environ.get("ANALYTICS_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("ANALYTICS_MCP_PORT", "8780")),
    )


if __name__ == "__main__":
    main()
