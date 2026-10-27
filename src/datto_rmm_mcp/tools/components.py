"""Component (script/tool) library — read only.

Endpoint verified live: GET /api/v2/account/components returned 250 real
components (e.g. "Change user password [WIN]", "Disk Cleanup [WIN]") in
testing.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import DattoClient, DattoError
from ._common import MAX_PAGE_SIZE, NO_TOKEN, fetch_page, page_size, pick, start_at

COMPONENT_FIELDS = ("uid", "id", "name", "categoryCode", "description", "credentialsRequired", "variables")
VARIABLE_FIELDS = ("name", "type", "direction", "defaultVal", "description")
# Pages read per call when filtering by name (Datto has no name filter for components).
NAME_SCAN_PAGES = 4


def _compact(component: dict) -> dict:
    out = pick(component, COMPONENT_FIELDS)
    if isinstance(out.get("variables"), list):
        out["variables"] = [pick(v, VARIABLE_FIELDS) for v in out["variables"] if isinstance(v, dict)]
    return out


def register(mcp: FastMCP, client_factory: Callable[[], DattoClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def dattormm_get_components(
        name: Annotated[
            str | None, Field(description="Only components whose name contains this text (case-insensitive).")
        ] = None,
        page: Annotated[int | None, Field(description="Optional 0-based page number.", ge=0)] = None,
        max_results: Annotated[
            int | None,
            Field(description=f"Optional page size (default 50, max {MAX_PAGE_SIZE}).", ge=1, le=MAX_PAGE_SIZE),
        ] = None,
        offset: Annotated[
            int | None,
            Field(description="Position to start from (0-based). Pass next_offset from the previous result.", ge=0),
        ] = None,
    ) -> str:
        """List the components (scripts/tools) this tenant can run on a device.

        Call this BEFORE dattormm_run_component — you need a real
        component_uid from here, guessing one is not possible. Pass name to
        find a component (e.g. "Disk Cleanup", "password"); each entry has a
        uid, name, description and the variables it takes. When has_more is
        true, call again with offset set to next_offset.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        size = page_size(max_results)
        start = start_at(page, size, offset)
        try:
            if not (name and name.strip()):
                result = await fetch_page(client, "/api/v2/account/components", "components", {}, size, start)
                result["items"] = [_compact(c) for c in result["items"]]
                return dump_json_capped(result)
            return dump_json_capped(await _find(client, name.strip().lower(), size, start))
        except DattoError as e:
            return e.to_envelope()


async def _find(client: DattoClient, needle: str, size: int, start: int) -> dict:
    """Scan Datto's pages from ``start`` and keep components whose name matches.

    ``next_offset`` is the position right after the last match returned, or where the
    scan stopped, so a follow-up call continues without skipping a component.
    """
    matches: list[tuple[int, dict]] = []
    position, more = start, True
    for _ in range(NAME_SCAN_PAGES):
        page = await fetch_page(client, "/api/v2/account/components", "components", {}, MAX_PAGE_SIZE, position)
        for i, component in enumerate(page["items"]):
            if needle in str(component.get("name") or "").lower():
                matches.append((position + i + 1, component))
        position, more = page.get("next_offset", position + len(page["items"])), page["has_more"]
        if len(matches) > size or not more:
            break
    shown = matches[:size]
    result: dict = {
        "items": [_compact(c) for _, c in shown],
        "count": len(shown),
        "limit": size,
        "offset": start,
        "has_more": len(matches) > size or more,
    }
    if len(matches) > size:
        result["next_offset"] = shown[-1][0]
    elif more:
        result["next_offset"] = position
    result["_resume"] = {"key": "next_offset", "positions": [p for p, _ in shown]}
    return result
