"""Device inventory — read only.

Endpoint verified live against real tenant accounts (not just the handover
doc): GET /api/v2/account/devices returns real device records.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import DattoClient, DattoError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], DattoClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def dattormm_get_devices(
        page: Annotated[
            int | None, Field(description="Optional 0-based page number, for paging through a large fleet.")
        ] = None,
        max_results: Annotated[
            int | None, Field(description="Optional page size. Omit for Datto's own default.")
        ] = None,
    ) -> str:
        """List the devices this tenant's Datto RMM account manages.

        Use for "what devices do we have in Datto", "find a device by
        hostname" (filter the returned list client-side — this call
        doesn't take a hostname filter), or to find a device_uid before
        calling dattormm_run_component. Returns real device records:
        hostname, site, OS, online/offline state, last-seen time, and
        more.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(
                "/api/v2/account/devices", params={"page": page, "max": max_results}
            )
            return dump_json_capped(result)
        except DattoError as e:
            return e.to_envelope()
