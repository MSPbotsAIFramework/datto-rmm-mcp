"""Quick Job execution + status — the only write path this server exposes.

Request/response contract for PUT /api/v2/device/{deviceUid}/quickjob is
taken from Datto's own current official API documentation
(rmm.datto.com/help/en/Content/2SETUP/APIv2.htm) — this exact call has not
yet been executed for real (read-only investigation, by design), so treat
the request shape as documented-but-unexercised. GET /api/v2/job/{jobUid}
HAS been verified live, twice, against two different real tenant accounts.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import DattoClient, DattoError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], DattoClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def dattormm_run_component(
        device_uid: Annotated[
            str, Field(description="Target device's uid, from dattormm_get_devices.")
        ],
        component_uid: Annotated[
            str, Field(description="Component to run, from dattormm_get_components.")
        ],
        variables: Annotated[
            dict[str, str] | None,
            Field(
                description=(
                    "Optional component input variables as {name: value}. Names must "
                    "match what the chosen component itself declares — this tool has "
                    "no way to look those up; ask the operator if unsure rather than "
                    "guessing a variable name."
                )
            ),
        ] = None,
        confirm: Annotated[
            bool,
            Field(
                description=(
                    "Required — must be explicitly true to proceed. This tool refuses "
                    "to call the API otherwise."
                )
            ),
        ] = False,
    ) -> str:
        """Run a component (script) on a device right now — Datto RMM's "Quick Job".

        ⚠️ DESTRUCTIVE, IRREVERSIBLE. Runs real code on a real device
        immediately, no dry run, cannot be cancelled once queued. Only set
        confirm=true on an explicit, unambiguous instruction naming both
        the device and the component — never on vague intent ("clean up
        that machine").

        KNOWN LIMITATION, confirmed against Datto's own current API docs:
        there is no way to retrieve this job's success/failure or
        stdout/stderr through this API, on any account. Use
        dattormm_get_job_status afterward only to see whether it reached a
        terminal state — never claim or imply it succeeded, you cannot see
        that.
        """
        if not confirm:
            return error_envelope(
                "invalid_argument", "Destructive operation requires confirm=true", False
            )
        client = client_factory()
        if client is None:
            return NO_TOKEN
        body: dict = {"componentUid": component_uid}
        if variables:
            body.update(variables)
        try:
            result = await client.put(f"/api/v2/device/{device_uid}/quickjob", json_body=body)
            return dump_json_capped(result)
        except DattoError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def dattormm_get_job_status(
        job_uid: Annotated[
            str, Field(description="Job uid returned by dattormm_run_component.")
        ],
    ) -> str:
        """Check whether a triggered job has reached a terminal state.

        Returns only a lifecycle status ("active" while queued/running,
        "completed" once the device has run it) — per Datto's own current
        API documentation, there is no way to retrieve success/failure,
        exit code, or stdout/stderr through this API. "completed" means
        the device finished running it, NOT that it succeeded — never
        report this as a success.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/api/v2/job/{job_uid}")
            return dump_json_capped(result)
        except DattoError as e:
            return e.to_envelope()
