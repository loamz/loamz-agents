# Connectors: generic MCP client and trusted catalog

Plan: `~/.claude/plans/i-want-to-build-eager-dusk.md` (2026-10-27). Summary of the decisions and the spike.

## Rules
- Connectors are MCP servers, never hand-written wrappers. Adding one is a catalog entry plus a test.
- Vendor-hosted servers only. Trust labels (Verified by Loamz, From the publisher) are set by us, never by the server.
- Writes and deletes default to "ask each time"; tool results are untrusted text.

## Spike: discovery probe of the first three vendors (2026-10-27, no account needed)
Every server answers an unauthenticated `initialize` with 401 and a `resource_metadata` link (RFC 9728), so the standard flow works.

| Vendor | MCP URL | Auth server | Registration | PKCE | Scopes |
|---|---|---|---|---|---|
| Notion | `https://mcp.notion.com/mcp` | `https://mcp.notion.com` | DCR (`/register`) and Client ID Metadata Documents | S256 | `default` |
| Linear | `https://mcp.linear.app/mcp` (read-only variant: `/mcp/readonly`) | `https://mcp.linear.app` | DCR and CIMD | S256 | `read`, `write` |
| Todoist | `https://ai.todoist.net/mcp` | `https://todoist.com` | DCR (`/oauth/register`) and CIMD | S256 | `data:read_write` (resource), many more on the auth server |

Consequences:
- No app registration or owner secret is needed for these three: the client registers itself (CIMD preferred, DCR fallback).
- Todoist has no read-only scope on its MCP resource, so read/write groups are enforced by our own tool-to-group map and approvals. Linear can use its `/mcp/readonly` endpoint for read-only installs.
- The MCP Python SDK in this repo (`mcp` 1.30.0) already provides `OAuthClientProvider`, a `TokenStorage` hook and a streamable HTTP client: build the connector manager on it instead of writing OAuth by hand.

## Next steps
1. Streamable HTTP transport and a per-person `TokenStorage` (encrypted, keyed by tenant, user, connector).
2. Connect, callback and disconnect routes; catalog fields (`mcp_url`, publisher, icon, tool-to-group map, checked_on).
3. Tool exposure with permission groups and approvals; tests against a fake MCP and OAuth server.
