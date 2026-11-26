# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Quick job body shape and dattormm_get_job_result (PRD-22489): variables as
name/value pairs, outcome mapping (failed wins, unknown is never success),
stream truncation, error envelopes and the owl probe call."""

import functools
import json

import httpx
import pytest

from datto_rmm_mcp import api_client, server
from datto_rmm_mcp.config import Settings

BASE = "https://concord-api.centrastage.net"
RESULTS = "/api/v2/job/job-1/results/dev-1"


class FakeDatto:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.result: dict | None = {
            "jobUid": "job-1",
            "deviceUid": "dev-1",
            "ranOn": "2026-11-26T07:41:00Z",
            "jobDeploymentStatus": "Success",
            "componentResults": [
                {
                    "componentUid": "c-1",
                    "componentName": "Create AD user",
                    "componentStatus": "Success",
                    "numberOfWarnings": 0,
                    "hasStdOut": True,
                    "hasStdErr": True,
                }
            ],
        }
        self.stdout = "user created"
        self.stderr = ""
        self.components_status = 200

    def paths(self):
        return [r.url.path for r in self.requests if r.url.path != "/auth/oauth/token"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/auth/oauth/token":
            return httpx.Response(200, json={"access_token": "t"})
        if path == "/api/v2/device/dev-1/quickjob" and request.method == "PUT":
            return httpx.Response(200, json={"job": {"uid": "job-1", "status": "active"}, "jobComponents": []})
        if path == RESULTS:
            if self.result is None:
                return httpx.Response(404, json={"message": "Job not found"})
            return httpx.Response(200, json=self.result)
        if path == RESULTS + "/stdout":
            return httpx.Response(200, json=[{"componentUid": "c-1", "componentName": "n", "stdData": self.stdout}])
        if path == RESULTS + "/stderr":
            return httpx.Response(200, json=[{"componentUid": "c-1", "componentName": "n", "stdData": self.stderr}])
        if path == "/api/v2/account/components":
            if self.components_status != 200:
                return httpx.Response(self.components_status, json={"message": "denied"})
            comps = [
                {"uid": "c-1", "name": "A", "variables": [{"name": "UserName", "type": "string"}]},
                {"uid": "c-2", "name": "B", "variables": []},
            ]
            limit = int(request.url.params.get("max", len(comps)))
            return httpx.Response(200, json={"pageDetails": {"totalCount": 2}, "components": comps[:limit]})
        return httpx.Response(500, json={"message": f"unexpected path {path}"})


@pytest.fixture
def fake(monkeypatch):
    fake = FakeDatto()
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


def _put_body(fake):
    (req,) = [r for r in fake.requests if r.method == "PUT"]
    return json.loads(req.content)


@pytest.mark.asyncio
async def test_run_component_sends_job_name_and_variable_list_and_returns_job_uid(fake, call):
    out = await call(
        "dattormm_run_component",
        device_uid="dev-1",
        component_uid="c-1",
        variables={"UserName": "jdoe.test", "Ou": "OU=Test"},
        confirm=True,
    )
    body = _put_body(fake)
    assert body["jobName"]
    assert body["jobComponent"] == {
        "componentUid": "c-1",
        "variables": [{"name": "UserName", "value": "jdoe.test"}, {"name": "Ou", "value": "OU=Test"}],
    }
    assert out["job_uid"] == "job-1" == out["job"]["uid"]


@pytest.mark.asyncio
async def test_run_component_without_confirm_makes_no_request(fake, call):
    out = await call("dattormm_run_component", device_uid="dev-1", component_uid="c-1")
    assert out["error"]["code"] == "invalid_argument"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_run_component_without_variables_omits_them_and_defaults_job_name(fake, call):
    out = await call("dattormm_run_component", device_uid="dev-1", component_uid="c-1", confirm=True)
    body = _put_body(fake)
    assert "variables" not in body["jobComponent"]
    assert body["jobName"].startswith("MSPbots quick job")
    assert "error" not in out


@pytest.mark.asyncio
async def test_get_components_max_results_one_returns_one_component_with_variables(fake, call):
    out = await call("dattormm_get_components", max_results=1)
    assert out["count"] == 1
    assert out["items"][0]["variables"][0]["name"] == "UserName"


@pytest.mark.asyncio
async def test_job_result_success_includes_stdout_and_stderr(fake, call):
    fake.stderr = "a warning line"
    out = await call("dattormm_get_job_result", job_uid="job-1", device_uid="dev-1")
    assert out["outcome"] == "success"
    comp = out["components"][0]
    assert (comp["stdout"], comp["stderr"]) == ("user created", "a warning line")
    assert fake.paths() == [RESULTS, RESULTS + "/stdout", RESULTS + "/stderr"]


@pytest.mark.parametrize("status", ["Running", "Pending"])
@pytest.mark.asyncio
async def test_job_result_running_does_not_fetch_streams(fake, call, status):
    fake.result["jobDeploymentStatus"] = status
    out = await call("dattormm_get_job_result", job_uid="job-1", device_uid="dev-1")
    assert out["outcome"] == status.lower()
    assert fake.paths() == [RESULTS]


@pytest.mark.parametrize("level", ["job", "component"])
@pytest.mark.asyncio
async def test_job_result_failure_is_failed_with_stderr(fake, call, level):
    fake.stderr = "Access is denied"
    if level == "job":
        fake.result["jobDeploymentStatus"] = "Failure"
    else:
        fake.result["componentResults"][0]["componentStatus"] = "Failure"
    out = await call("dattormm_get_job_result", job_uid="job-1", device_uid="dev-1")
    assert out["outcome"] == "failed"
    assert out["components"][0]["stderr"] == "Access is denied"


@pytest.mark.parametrize("status", ["Weird", None])
@pytest.mark.asyncio
async def test_job_result_unknown_status_is_never_success(fake, call, status):
    fake.result["jobDeploymentStatus"] = status
    fake.result["componentResults"] = []
    out = await call("dattormm_get_job_result", job_uid="job-1", device_uid="dev-1")
    assert out["outcome"] == "unknown"


@pytest.mark.asyncio
async def test_job_result_unknown_job_returns_not_found(fake, call):
    fake.result = None
    out = await call("dattormm_get_job_result", job_uid="job-1", device_uid="dev-1")
    assert out["error"]["code"] == "not_found"
    assert fake.paths() == [RESULTS]


@pytest.mark.asyncio
async def test_job_result_truncates_long_stdout_to_tail(fake, call):
    fake.stdout = "x" * 5_000 + "END" * 3_000
    out = await call("dattormm_get_job_result", job_uid="job-1", device_uid="dev-1")
    comp = out["components"][0]
    assert comp["stdout_truncated"] is True
    assert len(comp["stdout"]) == 8_000
    assert comp["stdout"].endswith("END")
    assert "stderr_truncated" not in comp


@pytest.mark.asyncio
async def test_probe_call_reads_one_component_and_maps_401_to_unauthorized(fake, call):
    out = await call("dattormm_get_components", max_results=1)
    assert "error" not in out and out["count"] == 1
    assert fake.paths() == ["/api/v2/account/components"]
    fake.components_status = 401
    bad = await call("dattormm_get_components", max_results=1)
    assert bad["error"]["code"] == "unauthorized"
