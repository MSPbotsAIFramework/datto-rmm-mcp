# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""No state is shared between tenants: a cookie Datto sets for one tenant is never
sent with another tenant's requests (PRD-21492)."""

import ast
import functools
import json
from pathlib import Path

import httpx
import pytest

from datto_rmm_mcp import api_client, server
from datto_rmm_mcp.config import Settings

BASE = "https://concord-api.centrastage.net"
SRC = Path(__file__).resolve().parents[1] / "src" / "datto_rmm_mcp"


class CookieDatto:
    """Sets a cookie on every response, like a load balancer or WAF in front of Datto would."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        tenant = request.headers.get("authorization", "") or request.content.decode()
        tag = "tenant-a" if "key-a" in tenant or "token-a" in tenant else "tenant-b"
        cookie = {"Set-Cookie": f"affinity={tag}; Path=/"}
        if request.url.path == "/auth/oauth/token":
            token = "token-a" if tag == "tenant-a" else "token-b"
            return httpx.Response(200, json={"access_token": token}, headers=cookie)
        return httpx.Response(200, json={"pageDetails": {"totalCount": 0}, "devices": []}, headers=cookie)


@pytest.mark.asyncio
async def test_a_cookie_set_for_one_tenant_is_not_sent_for_another(monkeypatch):
    fake = CookieDatto()
    # One transport shared by both tenants, as one process-wide connection pool would be.
    transport = httpx.MockTransport(fake)
    monkeypatch.setattr(server, "DattoClient", functools.partial(api_client.DattoClient, transport=transport))
    mcp = server.create_mcp_server(Settings())

    for key in ("key-a", "key-b"):
        token = server._gateway_creds_var.set(server._GatewayCreds(key, "secret", BASE))
        try:
            result = await mcp.call_tool("dattormm_get_devices", {})
        finally:
            server._gateway_creds_var.reset(token)
        blocks = result[0] if isinstance(result, tuple) else result
        assert "error" not in json.loads(blocks[0].text)

    assert len(fake.requests) == 4  # token + devices, for each tenant
    for request in fake.requests:
        assert "cookie" not in request.headers, (request.url.path, request.headers.get("cookie"))


def test_no_module_level_http_client_or_mutable_state():
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.Global):
                pytest.fail(f"{path.name}: global statement")
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(value, ast.Call) and "AsyncClient" in ast.unparse(value.func):
                    pytest.fail(f"{path.name}: module-level httpx client")
        assert "global " not in path.read_text(), path.name
