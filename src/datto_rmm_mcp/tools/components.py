"""Component (script/tool) library — read only.

Endpoint verified live: GET /api/v2/account/components returned 250 real
components (e.g. "Change user password [WIN]", "Disk Cleanup [WIN]") in
testing.
"""

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .._json import dump_json_capped
from ..api_client import DattoClient, DattoError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], DattoClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def dattormm_get_components() -> str:
        """List the components (scripts/tools) this tenant can run on a device.

        Call this BEFORE dattormm_run_component — you need a real
        component_uid from here, guessing one is not possible. Each entry
        has a name and uid; component names describe what they do (e.g.
        "Disk Cleanup [WIN]", "Change user password [WIN]") — match the
        operator's request to a name here, don't invent a uid.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get("/api/v2/account/components")
            return dump_json_capped(result)
        except DattoError as e:
            return e.to_envelope()
