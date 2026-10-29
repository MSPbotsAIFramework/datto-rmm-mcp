# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""dattormm_get_alerts / dattormm_resolve_alert (PRD-21198): status switch,
device/site scoping, mutual-exclusion rejection, and resolve with/without
confirm, including the 404 -> not_found mapping (AC1-5, AC7-9)."""

import functools
import json

import httpx
import pytest

from datto_rmm_mcp import api_client, server
from datto_rmm_mcp.config import Settings

BASE = "https://concord-api.centrastage.net"

ACCOUNT_OPEN = [{"alertUid": "acct-open-1"}]
ACCOUNT_RESOLVED = [{"alertUid": "acct-resolved-1"}]
DEVICE_OPEN = [{"alertUid": "dev-open-1"}]
SITE_OPEN = [{"alertUid": "site-open-1"}]


class FakeAlerts:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/auth/oauth/token":
            return httpx.Response(200, json={"access_token": "t"})
        page_details = {"totalCount": 0}
        if path == "/api/v2/account/alerts/open":
            return httpx.Response(200, json={"pageDetails": page_details, "alerts": ACCOUNT_OPEN})
        if path == "/api/v2/account/alerts/resolved":
            return httpx.Response(200, json={"pageDetails": page_details, "alerts": ACCOUNT_RESOLVED})
        if path == "/api/v2/device/dev-1/alerts/open":
            return httpx.Response(200, json={"pageDetails": page_details, "alerts": DEVICE_OPEN})
        if path == "/api/v2/site/site-1/alerts/open":
            return httpx.Response(200, json={"pageDetails": page_details, "alerts": SITE_OPEN})
        if path == "/api/v2/alert/alert-1/resolve" and request.method == "POST":
            return httpx.Response(200, json={"alertUid": "alert-1", "resolved": True})
        if path == "/api/v2/alert/unknown/resolve" and request.method == "POST":
            return httpx.Response(404, json={"message": "Alert not found"})
        return httpx.Response(500, json={"message": f"unexpected path {path}"})


@pytest.fixture
def fake(monkeypatch):
    fake = FakeAlerts()
    monkeypatch.setattr(
        server, "DattoClient", functools.partial(api_client.DattoClient, transport=httpx.MockTransport(fake))
    )
    token = server._gateway_creds_var.set(server._GatewayCreds("key", "secret", BASE))
    yield fake
    server._gateway_creds_var.reset(token)


@pytest.fixture
def call():
    mcp = server.create_mcp_server(Settings())

    async def _call(tool, /, **args):
        result = await mcp.call_tool(tool, args)
        blocks = result[0] if isinstance(result, tuple) else result
        return json.loads(blocks[0].text)

    return _call


@pytest.mark.asyncio
async def test_get_alerts_default_status_is_open_account_wide(fake, call):
    out = await call("dattormm_get_alerts")
    assert fake.requests[-1].url.path == "/api/v2/account/alerts/open"
    assert [a["alertUid"] for a in out["items"]] == ["acct-open-1"]
    assert {"items", "count", "limit", "offset", "has_more"} <= out.keys()


@pytest.mark.asyncio
async def test_get_alerts_status_resolved(fake, call):
    out = await call("dattormm_get_alerts", status="resolved")
    assert fake.requests[-1].url.path == "/api/v2/account/alerts/resolved"
    assert [a["alertUid"] for a in out["items"]] == ["acct-resolved-1"]


@pytest.mark.asyncio
async def test_get_alerts_scoped_to_device_uid(fake, call):
    out = await call("dattormm_get_alerts", device_uid="dev-1")
    assert fake.requests[-1].url.path == "/api/v2/device/dev-1/alerts/open"
    assert [a["alertUid"] for a in out["items"]] == ["dev-open-1"]


@pytest.mark.asyncio
async def test_get_alerts_scoped_to_site_uid(fake, call):
    out = await call("dattormm_get_alerts", site_uid="site-1")
    assert fake.requests[-1].url.path == "/api/v2/site/site-1/alerts/open"
    assert [a["alertUid"] for a in out["items"]] == ["site-open-1"]


@pytest.mark.asyncio
async def test_get_alerts_rejects_device_and_site_uid_together(fake, call):
    out = await call("dattormm_get_alerts", device_uid="dev-1", site_uid="site-1")
    assert out["error"]["code"] == "invalid_argument"
    assert not fake.requests


@pytest.mark.asyncio
async def test_resolve_alert_with_confirm_calls_resolve_endpoint(fake, call):
    out = await call("dattormm_resolve_alert", alert_uid="alert-1", confirm=True)
    assert fake.requests[-1].url.path == "/api/v2/alert/alert-1/resolve"
    assert fake.requests[-1].method == "POST"
    assert out == {"alertUid": "alert-1", "resolved": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{}, {"confirm": False}])
async def test_resolve_alert_without_confirm_is_rejected(fake, call, args):
    out = await call("dattormm_resolve_alert", alert_uid="alert-1", **args)
    assert out["error"]["code"] == "invalid_argument"
    assert not fake.requests


@pytest.mark.asyncio
async def test_resolve_alert_not_found_maps_to_not_found_envelope(fake, call):
    out = await call("dattormm_resolve_alert", alert_uid="unknown", confirm=True)
    assert out["error"]["code"] == "not_found"
    assert out["error"]["retryable"] is False
