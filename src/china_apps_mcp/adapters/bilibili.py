from __future__ import annotations

import os
from typing import Any

import httpx

_API_BASE = "https://api.bilibili.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


async def _get_json(path: str, *, params: dict[str, Any] | None = None, cookie: str = "") -> dict[str, Any]:
    headers = dict(_HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    async with httpx.AsyncClient(base_url=_API_BASE, headers=headers, timeout=15.0) as client:
        response = await client.get(path, params=params)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Bilibili returned an unexpected response shape")
    if payload.get("code") != 0:
        raise RuntimeError(f"Bilibili API error: code={payload.get('code')} message={payload.get('message')}")
    return payload


def register_bilibili_tools(mcp: Any) -> None:
    @mcp.tool()
    async def bilibili_get_video(bvid: str) -> dict[str, Any]:
        """Read public metadata for one Bilibili video by BV id. No account cookie is required."""
        bvid = bvid.strip()
        if not bvid.upper().startswith("BV"):
            raise ValueError("bvid must look like BVxxxxxxxxxx")

        payload = await _get_json("/x/web-interface/view", params={"bvid": bvid})
        data = payload.get("data") or {}
        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        return {
            "bvid": data.get("bvid"),
            "aid": data.get("aid"),
            "title": data.get("title"),
            "description": data.get("desc"),
            "duration_seconds": data.get("duration"),
            "published_at": data.get("pubdate"),
            "owner": {
                "mid": owner.get("mid"),
                "name": owner.get("name"),
            },
            "stats": {
                "view": stat.get("view"),
                "danmaku": stat.get("danmaku"),
                "reply": stat.get("reply"),
                "favorite": stat.get("favorite"),
                "coin": stat.get("coin"),
                "share": stat.get("share"),
                "like": stat.get("like"),
            },
        }

    @mcp.tool()
    async def bilibili_account_status() -> dict[str, Any]:
        """Report whether a Bilibili cookie is configured. Never returns the cookie itself."""
        cookie = os.getenv("BILIBILI_COOKIE", "").strip()
        return {"cookie_configured": bool(cookie)}

    @mcp.tool()
    async def bilibili_get_my_profile() -> dict[str, Any]:
        """Read the profile for the Bilibili account represented by BILIBILI_COOKIE. Read-only."""
        cookie = os.getenv("BILIBILI_COOKIE", "").strip()
        if not cookie:
            return {
                "configured": False,
                "message": "BILIBILI_COOKIE is not configured. Leave it unset for the public-only PoC.",
            }

        payload = await _get_json("/x/web-interface/nav", cookie=cookie)
        data = payload.get("data") or {}
        return {
            "configured": True,
            "is_login": data.get("isLogin"),
            "mid": data.get("mid"),
            "uname": data.get("uname"),
            "level": data.get("level_info", {}).get("current_level"),
            "vip_type": data.get("vipType"),
            "money": data.get("money"),
        }
