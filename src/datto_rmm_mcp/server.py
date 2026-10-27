import contextvars
from collections.abc import Callable
from typing import NamedTuple

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ._arguments import EnvelopeFastMCP
from .api_client import DattoClient
from .config import Settings


class _GatewayCreds(NamedTuple):
    api_key: str
    api_secret: str
    api_url: str


# Per-request credential isolation via contextvars.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent SSE connections are isolated.
# Datto RMM has no shared/brokerable OAuth layer to delegate to a gateway —
# the tenant's own API Key/Secret Key *are* the credential (the OAuth2
# password-grant client_id/secret are Datto's fixed public constants, not a
# secret worth guarding at a gateway). So this server does its own token
# exchange per request (see api_client.DattoClient), same as most vendor-mcp
# services in this fleet — it never caches a token beyond one request.
# Datto RMM platform name -> regional API host. The platform's credential form asks for the
# platform name (X-Datto-Platform, default "concord"); X-Datto-Api-Url still wins when sent.
PLATFORM_URLS = {
    "concord": "https://concord-api.centrastage.net",
    "merlot": "https://merlot-api.centrastage.net",
    "pinotage": "https://pinotage-api.centrastage.net",
    "syrah": "https://syrah-api.centrastage.net",
    "vidal": "https://vidal-api.centrastage.net",
    "zinfandel": "https://zinfandel-api.centrastage.net",
}
DEFAULT_PLATFORM = "concord"


def resolve_api_url(api_url: str | None, platform: str | None) -> str:
    """X-Datto-Api-Url if given, else the host for X-Datto-Platform (default concord).
    Returns "" for an unknown platform so tools report it instead of the middleware rejecting it."""
    if api_url and api_url.strip():
        return api_url.strip()
    return PLATFORM_URLS.get((platform or DEFAULT_PLATFORM).strip().lower(), "")


_gateway_creds_var: contextvars.ContextVar[_GatewayCreds | None] = contextvars.ContextVar(
    "datto_gateway_creds", default=None
)


def get_client_from_context(settings: Settings) -> DattoClient | None:
    """Resolve the active DattoClient for the current request context."""
    creds = _gateway_creds_var.get()
    if not creds or not creds.api_url:
        return None
    return DattoClient(creds.api_key, creds.api_secret, creds.api_url)


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-Datto-Api-Key and X-Datto-Api-Secret (required) and the region from
    X-Datto-Api-Url or X-Datto-Platform (optional, default concord), and stores
    them in the contextvar. Only the presence of the key and secret is checked
    here; an unknown platform is reported by the tools. Returns 401 if the key
    or secret is missing on /mcp requests.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        api_key = request.headers.get("x-datto-api-key")
        api_secret = request.headers.get("x-datto-api-secret")
        api_url = resolve_api_url(request.headers.get("x-datto-api-url"), request.headers.get("x-datto-platform"))
        if not api_key or not api_secret:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": (
                        "This server requires the X-Datto-Api-Key and X-Datto-Api-Secret "
                        "headers. Set the region with X-Datto-Platform (concord, merlot, "
                        "pinotage, syrah, vidal, zinfandel; default concord) or X-Datto-Api-Url."
                    ),
                    "required_headers": ["X-Datto-Api-Key", "X-Datto-Api-Secret"],
                    "optional_headers": ["X-Datto-Platform", "X-Datto-Api-Url"],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_creds_var.set(_GatewayCreds(api_key, api_secret, api_url))
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_creds_var.reset(ctx_token)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all Datto RMM tools."""
    # DNS-rebinding protection is a browser-oriented safeguard that rejects
    # non-localhost Host headers with 421. Disable it so the server works
    # correctly behind a reverse proxy or docker network.
    mcp = EnvelopeFastMCP(
        name="datto-rmm-mcp",
        instructions=(
            "Datto RMM is an RMM (remote monitoring and management) platform MSPs "
            "use to manage on-prem device fleets. This server exposes device "
            "inventory, the component (script) library, and Quick Job execution — "
            "the mechanism for running a component on a device right now. Typical "
            "flow: dattormm_get_devices to find a device_uid (pass hostname); "
            "dattormm_get_components to find a component_uid (pass name); "
            "dattormm_run_component to trigger it (destructive, irreversible — "
            "requires confirm=true); dattormm_get_job_status to check whether it "
            "finished. IMPORTANT KNOWN LIMITATION, confirmed against Datto's own "
            "current API documentation: there is no way to retrieve a job's "
            "success/failure, exit code, or stdout/stderr through this API — "
            "dattormm_get_job_status can only confirm the job reached a terminal "
            "state (\"completed\"), never whether it succeeded. Do not claim or "
            "imply a result you cannot see."
        ),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        # No session state between requests: tools/list works without an initialize handshake.
        stateless_http=True,
    )

    client_factory: Callable[[], DattoClient | None] = lambda: get_client_from_context(settings)

    from .tools import components, devices, jobs

    devices.register(mcp, client_factory)
    components.register(mcp, client_factory)
    jobs.register(mcp, client_factory)

    return mcp
