# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Alert visibility and resolution (PRD-21198).

Account/device/site-scoped alert listing and the one state-changing alert
action Datto's API still exposes (mute/unmute were removed in Datto's 8.9.0
release, so there is no "unresolve" path through this API). Field-unverified
against a real tenant - no Datto trial/sandbox credentials are available in
this environment, the same constraint the repo's own Known Gaps section
already documents for quickjob. See docs/tickets/PRD-21198.md Open Questions
and TC-DATTO-013/014 for what still needs a real-account check.
"""

from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import DattoClient, DattoError
from ._common import MAX_PAGE_SIZE, NO_TOKEN, fetch_page, page_size, start_at


def register(mcp: FastMCP, client_factory: Callable[[], DattoClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def dattormm_get_alerts(
        status: Annotated[
            Literal["open", "resolved"], Field(description='Which alerts to list (default "open").')
        ] = "open",
        device_uid: Annotated[
            str | None, Field(description="Only this device's alerts, from dattormm_get_devices. Not with site_uid.")
        ] = None,
        site_uid: Annotated[str | None, Field(description="Only this site's alerts. Not with device_uid.")] = None,
        muted: Annotated[
            bool | None, Field(description="Only alerts whose muted state matches this (matched by Datto).")
        ] = None,
        page: Annotated[
            int | None,
            Field(
                description=(
                    "Optional 0-based page number. Prefer offset / next_offset, which never skip an "
                    'alert. If the operator says "page 2" meaning the second page, pass 1.'
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
            Field(
                description="Alert position to start from (0-based). Pass next_offset from the previous result.",
                ge=0,
            ),
        ] = None,
    ) -> str:
        """List this tenant's Datto RMM alerts - account-wide, or narrowed to one device or site.

        Use for "what's alerting right now" (default status="open") or "what
        did we already resolve" (status="resolved"). Pass device_uid or
        site_uid to narrow the scope, never both. Find an alert_uid here
        before calling dattormm_resolve_alert. When has_more is true, call
        again with offset set to next_offset.
        """
        if device_uid and site_uid:
            return error_envelope("invalid_argument", "device_uid and site_uid are mutually exclusive", False)
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if device_uid:
            path = f"/api/v2/device/{device_uid}/alerts/{status}"
        elif site_uid:
            path = f"/api/v2/site/{site_uid}/alerts/{status}"
        else:
            path = f"/api/v2/account/alerts/{status}"
        size = page_size(max_results)
        try:
            result = await fetch_page(client, path, "alerts", {"muted": muted}, size, start_at(page, size, offset))
        except DattoError as e:
            return e.to_envelope()
        return dump_json_capped(result)

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def dattormm_resolve_alert(
        alert_uid: Annotated[str, Field(description="Alert to resolve, from dattormm_get_alerts.")],
        confirm: Annotated[
            bool,
            Field(
                description=(
                    "Required - must be explicitly true to proceed. This tool refuses to call the API otherwise."
                )
            ),
        ] = False,
    ) -> str:
        """Mark a Datto RMM alert as resolved.

        WARNING: IRREVERSIBLE. Datto's API has no "unresolve" or "reopen"
        action (mute/unmute were removed in Datto's 8.9.0 release). Only set
        confirm=true on an explicit instruction naming the alert, never on
        vague intent ("clear the alerts").
        """
        if not confirm:
            return error_envelope("invalid_argument", "Destructive operation requires confirm=true", False)
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.post(f"/api/v2/alert/{alert_uid}/resolve")
            return dump_json_capped(result)
        except DattoError as e:
            return e.to_envelope()
