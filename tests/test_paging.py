# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Cut device / component pages continue exactly by offset (PRD-21480, PRD-21481),
and bad arguments come back as the invalid_argument envelope."""

import functools
import json

import httpx
import pytest

from datto_rmm_mcp import api_client, server
from datto_rmm_mcp.config import Settings

BASE = "https://concord-api.centrastage.net"
CAP = 20_000


def device(i):
    # ~5,000 characters, like a real Datto device record.
    return {
        "uid": f"dev-{i:04d}",
        "id": i,
        "hostname": f"HOST-{i:04d}",
        "siteName": "Main",
        "online": i % 2 == 0,
        "lastSeen": "2026-10-27T00:00:00Z",
        **{f"extra_{k}": "x" * 100 for k in range(48)},
    }


def component(i):
    name = "Disk Cleanup [WIN]" if i == 200 else f"Component {i:03d}"
    return {
        "uid": f"cmp-{i:03d}",
        "id": i,
        "name": name,
        "categoryCode": "scripts",
        "description": "Does something useful on the device. " * 3,
        "variables": [{"name": "target", "type": "string", "direction": False, "variablesIdx": 1}],
    }


def alert(i):
    # ~5,000 characters, like a real Datto alert record (dynamic alertContext).
    return {
        "alertUid": f"alert-{i:04d}",
        "timestamp": "2026-10-27T00:00:00Z",
        "alertContext": {"@class": "device-alert", "detail": "x" * 100},
        **{f"extra_{k}": "x" * 100 for k in range(48)},
    }


class FakeDatto:
    """Datto API v2 paging: page N (0-based) starts at N * max; pageDetails like the real one."""

    def __init__(self, devices, components, alerts=None):
        self.data = {
            "/api/v2/account/devices": ("devices", devices),
            "/api/v2/account/components": ("components", components),
            "/api/v2/account/alerts/open": ("alerts", alerts or []),
        }
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/auth/oauth/token":
            return httpx.Response(200, json={"access_token": "t"})
        key, rows = self.data[request.url.path]
        params = request.url.params
        number, size = int(params.get("page", 0)), int(params.get("max", 250))
        if number < 0 or size < 1:
            return httpx.Response(500, json={"message": "Internal Server Error"})
        needle = params.get("hostname", "").lower()
        rows = [r for r in rows if needle in r.get("hostname", "").lower()] if needle else rows
        chunk = rows[number * size : (number + 1) * size]
        more = (number + 1) * size < len(rows)
        details = {"count": len(chunk), "totalCount": len(rows), "nextPageUrl": f"{BASE}/next" if more else None}
        return httpx.Response(200, json={"pageDetails": details, key: chunk})


@pytest.fixture
def fake(monkeypatch):
    fake = FakeDatto([device(i) for i in range(130)], [component(i) for i in range(260)], [alert(i) for i in range(90)])
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


async def walk(call, tool, args, id_key="uid"):
    ids, calls, start = [], 0, {}
    while True:
        out = await call(tool, **args, **start)
        calls += 1
        assert len(json.dumps(out)) <= CAP + 500 and "retry_page_size" not in out
        ids += [r[id_key] for r in out["items"]]
        if not out.get("has_more"):
            assert "next_offset" not in out
            return ids, calls
        start = {"offset": out["next_offset"]}
        assert calls < 300


@pytest.mark.asyncio
@pytest.mark.parametrize("max_results", [None, 5, 50, 250])
async def test_devices_walk_reaches_every_device_once(fake, call, max_results):
    args = {} if max_results is None else {"max_results": max_results}
    ids, _ = await walk(call, "dattormm_get_devices", args)
    assert ids == [f"dev-{i:04d}" for i in range(130)]


@pytest.mark.asyncio
async def test_full_device_records_still_walk_without_gaps(fake, call):
    ids, calls = await walk(call, "dattormm_get_devices", {"full_response": True, "max_results": 50})
    assert ids == [f"dev-{i:04d}" for i in range(130)]
    assert calls > 30  # about 3 full records fit per call


@pytest.mark.asyncio
async def test_device_offset_inside_a_page(fake, call):
    out = await call("dattormm_get_devices", offset=57, max_results=50)
    params = fake.requests[-1].url.params
    assert params["page"] == "1" and params["max"] == "50"
    assert out["items"][0]["uid"] == "dev-0057" and out["offset"] == 57 and out["total_count"] == 130


@pytest.mark.asyncio
async def test_device_hostname_filter_goes_to_datto(fake, call):
    out = await call("dattormm_get_devices", hostname="host-012")
    assert fake.requests[-1].url.params["hostname"] == "host-012"
    assert [d["uid"] for d in out["items"]] == [f"dev-{i:04d}" for i in range(120, 130)]
    assert "extra_1" not in out["items"][0]


@pytest.mark.asyncio
async def test_components_walk_reaches_every_component_once(fake, call):
    ids, _ = await walk(call, "dattormm_get_components", {})
    assert ids == [f"cmp-{i:03d}" for i in range(260)]


@pytest.mark.asyncio
async def test_component_name_lookup_finds_one_past_the_first_page_of_30(fake, call):
    out = await call("dattormm_get_components", name="disk cleanup")
    assert [c["uid"] for c in out["items"]] == ["cmp-200"] and out["has_more"] is False
    assert out["items"][0]["variables"] == [{"name": "target", "type": "string", "direction": False}]


@pytest.mark.asyncio
async def test_component_name_walk_continues_after_the_last_match(fake, call):
    ids, _ = await walk(call, "dattormm_get_components", {"name": "component 1", "max_results": 7})
    assert ids == [f"cmp-{i:03d}" for i in range(260) if f"Component {i:03d}".lower().startswith("component 1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{"page": -1}, {"max_results": 0}, {"offset": -5}, {"max_results": 251}])
async def test_bad_paging_arguments_are_invalid_argument_and_never_reach_datto(fake, call, args):
    error = (await call("dattormm_get_devices", **args))["error"]
    assert error["code"] == "invalid_argument" and error["retryable"] is False
    assert not fake.requests


@pytest.mark.asyncio
async def test_alerts_resume_continues_exactly_by_offset(fake, call):
    # TC-DATTO-006 (AC6): same continuation guarantee as devices/components,
    # exercised through dattormm_get_alerts.
    ids, _ = await walk(call, "dattormm_get_alerts", {"max_results": 7}, id_key="alertUid")
    assert ids == [f"alert-{i:04d}" for i in range(90)]


@pytest.mark.asyncio
async def test_missing_required_argument_uses_the_envelope(fake, call):
    error = (await call("dattormm_get_job_status"))["error"]
    assert error["code"] == "invalid_argument" and "job_uid" in error["message"]
