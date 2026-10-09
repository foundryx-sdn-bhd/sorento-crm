# Sorento CRM MCP (read-only)

Streamable HTTP MCP server that exposes one tool per in-scope **GET** on the Sorento CRM FastAPI API (`/api/v1/...`). Intended for **self-hosted n8n** (MCP Client Tool) and other MCP clients.

## Backend prerequisites

1. **`EXTERNAL_API_KEY`** - must match the key this server sends as `X-API-Key`.
2. **`EXTERNAL_API_KEY_ACT_AS_USER_ID`** - UUID of a **real** `users.id` row. API key auth resolves RBAC as that user (view permissions on modules you need). Without this, the legacy `system` user has no permissions and most routes return 403.

Create a dedicated user in the CRM (e.g. `n8n-mcp@internal`), assign a role with **view** permissions for product, order, inventory, procurement, marketing, forms, workflow forms, resources, and SLA (or use an admin/superadmin role in a single-tenant setup). Set `EXTERNAL_API_KEY_ACT_AS_USER_ID` to that user’s id.

## Configure the MCP server

| Variable | Required | Description |
|----------|----------|-------------|
| `CRM_BASE_URL` | Yes | API origin only, e.g. `https://crm-api.example.com` (no trailing slash) |
| `EXTERNAL_API_KEY` | Yes | Same as backend `EXTERNAL_API_KEY` |
| `CRM_MCP_HOST` | No | Default `0.0.0.0` |
| `CRM_MCP_PORT` | No | Default `8765` |
| `CRM_MCP_TIMEOUT` | No | HTTP timeout seconds (default `60`) |
| `CRM_MCP_MAX_RESPONSE_BYTES` | No | Cap JSON body size (default `8000000`) |
| `CRM_MCP_LOG_LEVEL` | No | Default `INFO` |
| `MCP_AUTH_TOKEN` | **Yes** | Bearer token every caller sends as `Authorization: Bearer <token>`; 32+ chars (`openssl rand -hex 32`). The server will not start without it. The backend sends it from its own `MCP_AUTH_TOKEN` (same value). |
| `CRM_MCP_ALLOWED_HOSTS` | No | Comma-separated Host values (`host:*` = any port). Set = DNS-rebinding protection on: other hosts get 421, and any request with an `Origin` header gets 403 (server-side callers send none). Empty = off. |

`GET /healthz` answers `ok` with no token, for probes.

## Run locally

```bash
cd sorento_crm_mcp
python -m venv .venv && source .venv/bin/activate
pip install -e .
export CRM_BASE_URL=http://localhost:8000
export EXTERNAL_API_KEY=your-key
export MCP_AUTH_TOKEN=$(openssl rand -hex 32)   # put the same value in the backend .env
export EXTERNAL_API_KEY_ACT_AS_USER_ID=...   # on the API server / .env, not here
python -m sorento_crm_mcp
```

Default MCP streamable HTTP path: **`/mcp`** (see [MCP streamable HTTP](https://modelcontextprotocol.io/)).

## Docker

Build and run next to the API (same Compose network as n8n):

```bash
docker build -t sorento-crm-mcp ./sorento_crm_mcp
docker run --rm -e CRM_BASE_URL=http://sorento-api:8000 -e EXTERNAL_API_KEY=... -e MCP_AUTH_TOKEN=... -p 8765:8765 sorento-crm-mcp
```

## n8n

1. Use the **MCP Client Tool** (or current n8n MCP node) with transport **Streamable HTTP** (or SSE if your n8n build requires it).
2. Server URL: `http://<mcp-host>:8765/mcp` (adjust for TLS and path).
3. Authentication: a **Bearer Auth** credential holding `MCP_AUTH_TOKEN` (the node sends `Authorization: Bearer <token>`). Without it every call is 401.
4. Ensure n8n can reach the MCP container and the MCP container can reach `CRM_BASE_URL`.

## Logging

- Logs include tool names at DEBUG; CRM GET failures log status and a short body prefix.
- Avoid logging full CRM responses in production if they contain PII.

## Tests

```bash
pip install -e ".[dev]"  # if dev extra added, or pip install pytest
pytest sorento_crm_mcp/tests -q
```
