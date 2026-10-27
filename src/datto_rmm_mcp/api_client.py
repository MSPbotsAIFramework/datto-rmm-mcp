# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
import asyncio
from typing import Any

import httpx

from ._json import error_envelope

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0

# Datto RMM's OAuth2 password-grant client credentials are fixed, public
# constants documented by Datto itself - not a per-tenant secret. The real
# secret is the tenant's own API Key/Secret Key, sent as username/password.
_OAUTH_CLIENT_ID = "public-client"
_OAUTH_CLIENT_SECRET = "public"

# No shared HTTP client: an httpx.AsyncClient keeps a cookie jar, so one client
# shared across tenants would send a Set-Cookie received for one tenant with the
# next tenant's requests (PRD-21492). Every HTTP call opens its own client and
# closes it, so nothing outlives the call.


# status_code -> (error code, retryable). status_code 0 means a network/
# connection-level failure (no response at all).
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    0: ("upstream_error", True),
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def _classify(status_code: int) -> tuple[str, bool]:
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500:
        return "upstream_error", True
    return "invalid_argument", False


class DattoError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Datto RMM API error {status_code}: {message}")

    def to_envelope(self) -> str:
        code, retryable = _classify(self.status_code)
        return error_envelope(code, self.message, retryable)


def _normalize_base_url(api_url: str) -> str:
    """Real tenant records store this inconsistently - sometimes a bare
    host ("zinfandel-api.centrastage.net"), sometimes a full URL
    ("https://zinfandel-api.centrastage.net"). Normalize to a full https URL
    either way.
    """
    host = api_url.strip()
    if host.startswith("http://") or host.startswith("https://"):
        return host.rstrip("/")
    return f"https://{host.rstrip('/')}"


class DattoClient:
    """Async httpx client wrapping the Datto RMM REST API v2.

    Takes the tenant's raw API Key/Secret Key (and its region-specific API
    host) and does the OAuth2 password-grant exchange itself, once per
    client instance (memoized on self, never persisted beyond the lifetime
    of the request this client was built for - see server.py's
    contextvar-based credential isolation). The grant's client_id/secret
    are Datto's own fixed public constants; the tenant's API Key/Secret
    Key are sent as the grant's username/password, per Datto's documented
    auth flow.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = _normalize_base_url(api_url)
        self._token: str | None = None
        self._transport = transport  # tests only

    def _http(self) -> httpx.AsyncClient:
        """A fresh client for one HTTP call; use it with ``async with`` so it is closed."""
        return httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, transport=self._transport)

    async def _ensure_token(self) -> str:
        if self._token is None:
            self._token = await self._exchange_token()
        return self._token

    async def _exchange_token(self) -> str:
        try:
            async with self._http() as client:
                resp = await client.post(
                    f"{self._base_url}/auth/oauth/token",
                    auth=(_OAUTH_CLIENT_ID, _OAUTH_CLIENT_SECRET),
                    data={
                        "grant_type": "password",
                        "username": self._api_key,
                        "password": self._api_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.RequestError as e:
            raise DattoError(0, f"{e or type(e).__name__} (token exchange)") from e

        if resp.status_code >= 400:
            raise DattoError(resp.status_code, self._extract_error(resp))
        body = self._parse_body(resp)
        token = (body or {}).get("access_token") if isinstance(body, dict) else None
        if not token:
            raise DattoError(0, "Token exchange succeeded but response had no access_token")
        return token

    def _extract_error(self, resp: httpx.Response) -> str:
        try:
            detail = resp.json()
            if isinstance(detail, dict):
                return (
                    detail.get("detail")
                    or detail.get("message")
                    or detail.get("error_description")
                    or detail.get("error")
                    or str(detail)
                )
            return str(detail)
        except ValueError:
            return resp.text

    def _clean_params(self, params: dict | None) -> dict:
        if not params:
            return {}
        return {k: v for k, v in params.items() if v is not None}

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def put(self, path: str, json_body: Any = None) -> Any:
        return await self._request("PUT", path, json_body=json_body)

    async def _request(self, method: str, path: str, params: dict | None = None, json_body: Any = None) -> Any:
        token = await self._ensure_token()
        url = f"{self._base_url}{path}"
        params = self._clean_params(params)

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with self._http() as client:
                    resp = await client.request(method, url, headers=headers, params=params, json=json_body)
            except httpx.RequestError as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                    continue
                raise DattoError(0, f"{e or type(e).__name__} (url={url})") from e

            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                delay = self._retry_delay(resp, attempt)
                await asyncio.sleep(delay)
                continue

            self._raise_for_status(resp)
            return self._parse_body(resp)

        # Unreachable in practice (loop always returns or raises above), but
        # keeps type checkers happy and guards against future edits.
        if last_exc:
            raise DattoError(0, f"{last_exc}") from last_exc
        raise DattoError(0, "request failed with no response")

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2**attempt, _MAX_BACKOFF_SECONDS)

    def _parse_body(self, resp: httpx.Response) -> Any:
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return {"raw_response": resp.text}

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise DattoError(resp.status_code, self._extract_error(resp))
