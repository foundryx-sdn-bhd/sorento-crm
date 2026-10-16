# PLAN: lock the MCP endpoint + default-deny company scope

Status: planning (full track, size M: auth change on the MCP server, no migration, no UI change)
UAC: `sec-mcp-auth-acceptance-criteria.md`
Lane: SEC-MCP-AUTH (owner D7, 16 Oct)

## Problem

The CRM MCP server (`sorento_crm_mcp`) accepts any caller on `/mcp` with no credential, and a
tool call without contact/company scope reads every company.

Investigation and design follow in the next commit.
