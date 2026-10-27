# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
from .._json import error_envelope

NO_TOKEN = error_envelope(
    "not_configured",
    "No usable Datto RMM credentials. Send X-Datto-Api-Key and X-Datto-Api-Secret, "
    "and a valid X-Datto-Platform (concord, merlot, pinotage, syrah, vidal, zinfandel) "
    "or X-Datto-Api-Url.",
    False,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 250  # Datto's largest page


def page_size(max_results: int | None) -> int:
    return min(max(max_results or DEFAULT_PAGE_SIZE, 1), MAX_PAGE_SIZE)


def start_at(page: int | None, size: int, offset: int | None) -> int:
    """Record position to start from: ``offset`` wins over the 0-based ``page``."""
    return offset if offset is not None else (page or 0) * size


async def fetch_page(client, path: str, key: str, params: dict, size: int, start: int) -> dict:
    """One Datto page holding ``start`` (Datto page N starts at N * max), from ``start`` on.

    ``next_offset`` continues right after the last record returned, also when the
    size cap cuts the page (``_resume``), so following it never skips a record.
    """
    number = start // size
    first = number * size
    payload = await client.get(path, params={**params, "page": number, "max": size}) or {}
    rows = payload.get(key) or []
    details = payload.get("pageDetails") or {}
    total = details.get("totalCount")
    if isinstance(total, int):
        has_more = first + len(rows) < total
    elif "nextPageUrl" in details:
        has_more = bool(details.get("nextPageUrl"))
    else:
        has_more = len(rows) >= size
    items = rows[start - first :]
    result: dict = {"items": items, "count": len(items), "limit": size, "offset": start, "has_more": has_more}
    if isinstance(total, int):
        result["total_count"] = total
    if has_more:
        result["next_offset"] = first + len(rows)
    result["_resume"] = {"key": "next_offset", "start": start}
    return result


def pick(record: dict, fields: tuple[str, ...]) -> dict:
    return {k: record[k] for k in fields if record.get(k) not in (None, "")}
