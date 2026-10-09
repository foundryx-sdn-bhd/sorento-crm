# sorento_analytics_mcp

The public face of the claude.ai custom connector (card
`documentation/plans/analytics/CARD-claude-connector-09oct.md`). A thin process: it holds no database
credential and no `EXTERNAL_API_KEY`. OAuth state, sessions, RBAC and audit live in the backend.

Served under `/analytics/`: `/analytics/mcp` (Streamable HTTP MCP, bearer required),
`/analytics/oauth/{register,authorize,token}` (forwarded to the backend), the RFC 9728 / RFC 8414
well-known documents and `/analytics/healthz`.

Every `/analytics/mcp` request is first checked with `GET /api/v1/analytics-connector/me` using the
caller's bearer and the internal key; anything but 200 is a 401 with `WWW-Authenticate`. Tools then call
the backend AS that user. The 34 reused catalog tools are the static `ANALYTICS_REUSED_TOOLS`; the five
`chat_*` tools map to `/api/v1/analytics-connector/*`. Output is capped at 120,000 characters, and every
reused tool call is audited through `POST /api/v1/analytics-connector/audit`.

## Environment

| Variable | Meaning |
| --- | --- |
| `CRM_BASE_URL` | Backend base URL, e.g. `http://backend:8000` (internal network) |
| `ANALYTICS_CONNECTOR_INTERNAL_KEY` | Shared secret, same value as the backend's |
| `ANALYTICS_CONNECTOR_PUBLIC_BASE_URL` | `https://<crm-domain>/analytics` |
| `ANALYTICS_MCP_HOST` / `ANALYTICS_MCP_PORT` | Bind address, default `0.0.0.0:8780` |
| `ANALYTICS_MCP_TIMEOUT` | Backend call timeout in seconds, default 60 |

## Run and test

```bash
docker build -f sorento_analytics_mcp/Dockerfile -t sorento-analytics-mcp .   # from the repo root
cd sorento_analytics_mcp && python -m pytest tests -q
```
