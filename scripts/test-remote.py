from __future__ import annotations

import argparse
import asyncio
import os

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def run(url: str) -> None:
    load_dotenv()
    token = os.getenv("MCP_ACCESS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else None

    async with httpx.AsyncClient(headers=headers, timeout=None) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                print("Connected.")
                print("Tools:", ", ".join(names))
                result = await session.call_tool("gateway_ping", arguments={})
                print("gateway_ping:", result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Remote MCP URL, e.g. https://host.example/mcp")
    args = parser.parse_args()
    asyncio.run(run(args.url))


if __name__ == "__main__":
    main()
