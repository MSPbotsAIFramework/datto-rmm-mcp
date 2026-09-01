"""tools/list snapshot + error-envelope mapping tests.

No network calls: tool enumeration goes through FastMCP's in-process
list_tools(), and the error-code mapping is tested directly against
DattoError, independent of any real HTTP request.
"""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from datto_rmm_mcp.api_client import DattoError, _normalize_base_url
from datto_rmm_mcp.config import Settings
from datto_rmm_mcp.server import create_mcp_server

# name -> (required params, expected annotation hint set to True)
EXPECTED_TOOLS = {
    "dattormm_get_devices": (set(), {"readOnlyHint"}),
    "dattormm_get_components": (set(), {"readOnlyHint"}),
    "dattormm_run_component": ({"device_uid", "component_uid"}, {"destructiveHint"}),
    "dattormm_get_job_status": ({"job_uid"}, {"readOnlyHint"}),
}


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"

    by_name = {t.name: t for t in tools}
    # dattormm_run_component is the sole exception to the SOP's 500-char
    # description guideline (§2.2, a "should" not a hard rule): it's a
    # destructive, irreversible action against real customer production
    # devices, and the known-limitation warning (no way to see success/
    # failure afterward) is load-bearing safety information, not decorative.
    _LONG_DESCRIPTION_EXCEPTIONS = {"dattormm_run_component"}
    for name, (expected_required, expected_hints) in EXPECTED_TOOLS.items():
        tool = by_name[name]
        required = set(tool.inputSchema.get("required", []))
        assert required == expected_required, f"{name}: required={required}"

        description = tool.description or ""
        if name not in _LONG_DESCRIPTION_EXCEPTIONS:
            assert len(description) <= 500, f"{name}: description too long ({len(description)})"
        first_line = description.strip().splitlines()[0] if description.strip() else ""
        assert len(first_line) <= 100, f"{name}: first line too long: {first_line!r}"
        assert "API:" not in description, f"{name}: leaked implementation detail"
        assert "GET /" not in description and "PUT /" not in description, (
            f"{name}: leaked implementation detail"
        )

        annotations = tool.annotations
        actual_hints = set()
        if annotations is not None:
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint"):
                if getattr(annotations, hint, None) is True:
                    actual_hints.add(hint)
        assert actual_hints == expected_hints, f"{name}: hints={actual_hints}"


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings())
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


@pytest.mark.parametrize(
    "status_code,expected_code,expected_retryable",
    [
        (0, "upstream_error", True),
        (400, "invalid_argument", False),
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
    ],
)
def test_error_envelope_mapping(status_code, expected_code, expected_retryable):
    err = DattoError(status_code, "boom")
    envelope = json.loads(err.to_envelope())
    assert envelope["error"]["code"] == expected_code
    assert envelope["error"]["retryable"] is expected_retryable
    assert envelope["error"]["message"] == "boom"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("zinfandel-api.centrastage.net", "https://zinfandel-api.centrastage.net"),
        ("https://zinfandel-api.centrastage.net", "https://zinfandel-api.centrastage.net"),
        ("https://zinfandel-api.centrastage.net/", "https://zinfandel-api.centrastage.net"),
        ("http://concord-api.centrastage.net", "http://concord-api.centrastage.net"),
    ],
)
def test_normalize_base_url_handles_real_data_inconsistency(raw, expected):
    # Real tenant_integration records store this field inconsistently — some
    # rows have a bare host, some a full URL, with or without a trailing
    # slash (confirmed by direct inspection of production data).
    assert _normalize_base_url(raw) == expected


@pytest.mark.asyncio
async def test_run_component_rejects_without_confirm_before_calling_api():
    captured = {}

    class _StubClient:
        async def put(self, path, json_body=None):
            captured["called"] = path
            return {"should": "not happen"}

    from datto_rmm_mcp.tools import jobs

    mcp = FastMCP(name="test")
    jobs.register(mcp, lambda: _StubClient())
    result = await mcp.call_tool(
        "dattormm_run_component",
        {"device_uid": "d1", "component_uid": "c1", "confirm": False},
    )
    text = result[0][0].text if isinstance(result, tuple) else str(result)
    assert "invalid_argument" in text
    assert "called" not in captured, "must reject before ever calling the API"


@pytest.mark.asyncio
async def test_no_credentials_returns_not_configured_without_calling_api():
    from datto_rmm_mcp.tools import devices

    mcp = FastMCP(name="test")
    devices.register(mcp, lambda: None)
    result = await mcp.call_tool("dattormm_get_devices", {})
    text = result[0][0].text if isinstance(result, tuple) else str(result)
    assert "not_configured" in text
