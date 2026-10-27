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
from ._common import MAX_PAGE_SIZE, NO_TOKEN, fetch_page, page_size, pick, start_at

# What a device returns by default. A full Datto device record is about 5,000 characters,
# so only 3 fit in one result; these fields fit about 50.
DEVICE_FIELDS = (
    "uid",
    "id",
    "hostname",
    "description",
    "siteName",
    "siteUid",
    "deviceType",
    "deviceClass",
    "operatingSystem",
    "online",
    "suspended",
    "deleted",
    "rebootRequired",
    "lastSeen",
    "lastReboot",
    "lastLoggedInUser",
    "domain",
    "intIpAddress",
    "extIpAddress",
    "antivirus",
    "patchManagement",
)

Like = "Only devices whose {} contains this text (matched by Datto)."


def register(mcp: FastMCP, client_factory: Callable[[], DattoClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def dattormm_get_devices(
        page: Annotated[
            int | None,
            Field(
                description=(
                    "Optional 0-based page number. Prefer offset / next_offset, which never skip a "
                    'device. If the operator says "page 2" meaning the second page, pass 1.'
                ),
                ge=0,
            ),
        ] = None,
        max_results: Annotated[
            int | None,
            Field(description=f"Optional page size (default 50, max {MAX_PAGE_SIZE}).", ge=1, le=MAX_PAGE_SIZE),
        ] = None,
        offset: Annotated[
            int | None,
            Field(description="Device position to start from (0-based). Pass next_offset from the previous result.", ge=0),
        ] = None,
        hostname: Annotated[str | None, Field(description=Like.format("hostname"))] = None,
        site_name: Annotated[str | None, Field(description=Like.format("site name"))] = None,
        device_type: Annotated[str | None, Field(description=Like.format("device type"))] = None,
        operating_system: Annotated[str | None, Field(description=Like.format("operating system"))] = None,
        full_response: Annotated[
            bool | None, Field(description="Return Datto's full device records (about 3 fit per call).")
        ] = None,
    ) -> str:
        """List the devices this tenant's Datto RMM account manages.

        Use for "what devices do we have in Datto", "find a device by
        hostname" (pass hostname), or to find a device_uid before calling
        dattormm_run_component. Returns hostname, site, OS, online state,
        last-seen time and more. When has_more is true, call again with
        offset set to next_offset.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        size = page_size(max_results)
        filters = {
            "hostname": hostname,
            "siteName": site_name,
            "deviceType": device_type,
            "operatingSystem": operating_system,
        }
        try:
            result = await fetch_page(
                client, "/api/v2/account/devices", "devices", filters, size, start_at(page, size, offset)
            )
        except DattoError as e:
            return e.to_envelope()
        if not full_response:
            result["items"] = [pick(d, DEVICE_FIELDS) for d in result["items"]]
        return dump_json_capped(result)
