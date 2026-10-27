# datto-rmm-mcp

MCP server for **Datto RMM** — device inventory, the component (script) library,
and Quick Job execution (run a component on a device right now), over the
[Model Context Protocol](https://modelcontextprotocol.io/) (Streamable HTTP/SSE
transport).

Built for [PRD-16562](https://app.clickup.com/t/2280862/PRD-16562) (on-prem
operations via Datto RMM, alongside self-host/NinjaOne) after a feasibility
investigation confirmed the API surface — see Known Gaps for exactly what was
and wasn't verified before this was written.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## When would you use this

- "What devices do we have in Datto?" / find a device before running
  something on it → `dattormm_get_devices`
- "What scripts/components can we run?" → `dattormm_get_components`
- "Run [component] on [device] now" → `dattormm_run_component`
  (⚠️ destructive, irreversible — see Known Gaps for what you can and can't
  confirm afterward)
- "Did that job finish?" → `dattormm_get_job_status`

## Tools

Authorization requires the `X-Datto-Api-Key` and `X-Datto-Api-Secret` request headers; the region comes from `X-Datto-Platform` or `X-Datto-Api-Url` (see the authentication notes below).

| Tool | Description | Parameters |
|---|---|---|
| `dattormm_get_devices` | List the devices under the tenant's Datto RMM account, compact by default | `offset`, `max_results` (default 50, max 250), `page`, `hostname`, `site_name`, `device_type`, `operating_system`, `full_response` (all optional) |
| `dattormm_get_components` | List the runnable components/script library, with their variables | `name`, `offset`, `max_results`, `page` (all optional) |
| `dattormm_run_component` | Run the specified component on the specified device immediately (Datto's "Quick Job"), **destructive and irreversible** | `device_uid` (required), `component_uid` (required), `variables` (optional), `confirm` (required, must be true) |
| `dattormm_get_job_status` | Check whether a job has reached a terminal state | `job_uid` (required) |

> **`dattormm_get_job_status` can only confirm whether the job is "completed", not whether it succeeded**; see Known Gaps.

List results are `{"items", "count", "limit", "offset", "has_more"}` (plus `total_count`). When `has_more` is true, `next_offset` is where to continue: it starts right after the last record returned, also when a page was cut to fit the 20,000-character limit, so following it never skips a record. A call can return fewer than `limit` records while more remain. A full Datto device record is about 5,000 characters, so devices come back with the main fields (uid, hostname, site, OS, online state, last seen, IPs, antivirus, patch status) unless `full_response` is true. `hostname`, `site_name`, `device_type` and `operating_system` are partial matches done by Datto; the component `name` filter is applied here, scanning up to 1,000 components per call. Invalid arguments (negative `page` / `offset`, `max_results` outside 1-250, a missing required argument) return the `invalid_argument` envelope without calling Datto.

## Quick Start

### Docker (recommended)

```bash
docker compose up --build
```

The server starts on `http://localhost:8080`.

### Local (uv)

```bash
uv sync
python -m datto_rmm_mcp
```

## Health Check

```bash
curl http://localhost:8080/health
# {"status": "ok"}
```

No credentials are required for the health endpoint.

## Authentication

Every request to `/mcp` must include the following HTTP headers (provided by the
MCP caller/gateway):

| Header | Type | Required | Description |
|---|---|---|---|
| `X-Datto-Api-Key` | string | Required | API Key generated by the tenant in Datto RMM. Used as the username of the OAuth2 password grant. |
| `X-Datto-Api-Secret` | string | Required | The corresponding API Secret Key. Used as the password. |
| `X-Datto-Platform` | string | Optional | Datto RMM platform hosting the account: `concord` (default, US East), `zinfandel` (US West), `merlot` (Europe), `vidal` (Canada), `syrah` (Australia), `pinotage` (South Africa). Used when `X-Datto-Api-Url` is not sent. An unknown value is reported by the tools. |
| `X-Datto-Api-Url` | string | Optional | Overrides the platform: the Datto RMM regional API host where the tenant's account lives (Datto has a multi-region architecture and each tenant account is bound to one region, e.g. `zinfandel-api.centrastage.net`). A bare host or one with the `https://` prefix both work; the server normalizes it automatically. |

Missing the key or secret returns `401 Unauthorized`.

**Authentication mechanism**: after receiving these three credentials, this service itself calls Datto's OAuth2 password grant (`POST /auth/oauth/token`; client_id/secret are the fixed values Datto publishes officially, `public-client`/`public`, and the tenant's API Key/Secret are used as username/password) to exchange them for the real bearer token before calling the actual endpoints. The token is not issued by the gateway on its behalf; this service does it itself (because the client credentials of Datto's OAuth are themselves public constants, there is no secret that the gateway would need to manage). The token is held only for the lifetime of a single request and is never cached.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_HTTP_PORT` | `8080` | Listening port |
| `MCP_HTTP_HOST` | `0.0.0.0` | Listening host |

## MCP Endpoint

```
POST http://localhost:8080/mcp
```

Connect your MCP client with:
- Transport: `http` (Streamable HTTP / SSE)
- Headers: `X-Datto-Api-Key`, `X-Datto-Api-Secret` (required); `X-Datto-Platform` or `X-Datto-Api-Url` (optional, default platform `concord`)

## Test Example

```bash
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Datto-Api-Key: <api-key>" \
  -H "X-Datto-Api-Secret: <api-secret>" \
  -H "X-Datto-Api-Url: zinfandel-api.centrastage.net" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": { "name": "dattormm_get_devices", "arguments": { "max_results": 10 } }
  }'
```

> ⚠️ This repository is public. Do not write real API Keys/Secrets into any committed file;
> the placeholders above are for illustration only.

## Known Gaps

- **What was actually verified live, and how.** This server was written after
  a feasibility investigation on real customer Datto RMM accounts (read-only
  credentials, OAuth2 password grant against real `tenant_integration`
  records), not from documentation alone:
  - `GET /api/v2/account/devices` — called for real, returned real device
    records (1711 devices in one tested account).
  - `GET /api/v2/account/components` — called for real, returned 250 real
    components.
  - `GET /api/v2/job/{jobUid}` — called for real, twice, against two
    different real job UIDs on two different tenant accounts. Both returned
    only `{id, dateCreated, name, uid, status}` — **no exitCode, stdout,
    stderr, or success/failure field of any kind.**
  - `PUT /api/v2/device/{deviceUid}/quickjob` (the tool that actually runs a
    script) has **never been executed** — by design, the investigation was
    read-only to stay invisible to customers. Its request/response contract
    in `api_client.py`/`tools/jobs.py` is taken from Datto's own current
    official documentation (rmm.datto.com/help/en/Content/2SETUP/APIv2.htm),
    not from a real call. **Test this for real (a Datto trial/sandbox
    account, or an explicit customer-consented dry run) before treating this
    tool as production-ready.**
- **No success/failure or stdout/stderr reporting, on purpose — and this is
  not a version artifact.** Datto's own current API documentation states
  outright: "It is not possible from within the API to examine the success
  or failure result of a completed job schedule and access the
  StdOut/StdErr." Datto's **v14.0.0 release notes separately claim** "New API
  endpoints under v2/job now allow retrieval of job results, including
  StdOut and StdErr" — these two official Datto documents contradict each
  other, and this investigation could not resolve it: the "new endpoint" the
  release notes describe was never found (WebSearch results describing it
  could not be traced to a real, citable page), and there's no way to read a
  tested account's platform version from the API to know whether it should
  even have this capability. What's certain: **the two real accounts tested,
  right now, do not return this data via `GET /v2/job/{jobUid}`.** Given
  this, `dattormm_get_job_status`'s docstring is deliberately conservative —
  it reports only "reached a terminal state," never success. Do not add a
  success/failure field to this tool without a fresh, real test proving the
  new endpoint (whatever it turns out to be) actually exists and works.
- **`dattormm_run_component`'s request shape is desk-verified, not
  field-verified.** `{"componentUid": ..., "<variableName>": "<value>", ...}`
  is what Datto's docs say to send; the docs also say a successful response
  contains a `uid` usable as `dattormm_get_job_status`'s `job_uid` — this
  server is built to that contract, but no real call has confirmed it works
  as documented, whether variable names are validated, or what a validation
  error actually looks like from this specific endpoint (blind probing
  during this investigation got a generic `{"detail":"Failed to read
  request"}` for every guessed body shape, including ones matching the
  eventual documented shape — that response never got more specific no
  matter what was sent, so it's untested whether the documented shape
  actually parses).
- **componentUid discovery**: `dattormm_get_components` is the intended way
  to find one (confirmed working), even though Datto's own docs say to copy
  it from the UI manually — the API path works in practice, use it.
- **No write/admin endpoints beyond quickjob are wrapped**: device
  claim/unclaim, site management, alert management, and anything else in
  Datto's API are out of scope for this MCP; PRD-16562 only asked for
  deployment/execution + inventory.
- **Pagination**: Datto's `page` is 0-based and page N starts at
  N * `max` (confirmed by QA on INT, PRD-21480). Tools expose `offset` /
  `next_offset` on top of it; `max_results` is capped at 250. Query
  parameters and record fields follow Datto's published OpenAPI document
  (`https://concord-api.centrastage.net/api/v3/api-docs`).
