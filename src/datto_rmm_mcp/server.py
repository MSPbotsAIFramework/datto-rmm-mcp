import contextvars
from collections.abc import Callable
from typing import NamedTuple

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

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
_gateway_creds_var: contextvars.ContextVar[_GatewayCreds | None] = contextvars.ContextVar(
    "datto_gateway_creds", default=None
)


def get_client_from_context(settings: Settings) -> DattoClient | None:
    """Resolve the active DattoClient for the current request context."""
    creds = _gateway_creds_var.get()
    if not creds:
        return None
    return DattoClient(creds.api_key, creds.api_secret, creds.api_url)


class GatewayTokenMiddleware:
    """ASGI middleware.

    Reads X-Datto-Api-Key, X-Datto-Api-Secret, and X-Datto-Api-Url (all
    required) from request headers and stores them in the contextvar.
    X-Datto-Api-Url is the tenant's region-specific API host (Datto RMM is
    multi-region; each tenant's account lives on exactly one, e.g.
    "zinfandel-api.centrastage.net") — there is no fixed default, unlike a
    single-region vendor, so it must be supplied per tenant. Returns 401 if
    any is missing on /mcp requests.
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
        api_url = request.headers.get("x-datto-api-url")
        if not api_key or not api_secret or not api_url:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": (
                        "This server requires the X-Datto-Api-Key header, the "
                        "X-Datto-Api-Secret header, and the X-Datto-Api-Url header "
                        "(the tenant's region-specific Datto RMM API host, e.g. "
                        "zinfandel-api.centrastage.net)"
                    ),
                    "required_headers": ["X-Datto-Api-Key", "X-Datto-Api-Secret", "X-Datto-Api-Url"],
                    "optional_headers": [],
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
    mcp = FastMCP(
        name="datto-rmm-mcp",
        instructions=(
            "Datto RMM is an RMM (remote monitoring and management) platform MSPs "
            "use to manage on-prem device fleets. This server exposes device "
            "inventory, the component (script) library, and Quick Job execution — "
            "the mechanism for running a component on a device right now. Typical "
            "flow: dattormm_get_devices to find a device_uid; "
            "dattormm_get_components to find a component_uid (what to run); "
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
    )

    client_factory: Callable[[], DattoClient | None] = lambda: get_client_from_context(settings)

    from .tools import components, devices, jobs

    devices.register(mcp, client_factory)
    components.register(mcp, client_factory)
    jobs.register(mcp, client_factory)

    return mcp
