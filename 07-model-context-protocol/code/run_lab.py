import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ALLOWED_TOOLS = {"inject_leak", "probe_leak", "fix_leak"}


async def call(session: ClientSession, name: str) -> dict:
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"Tool is not allowed: {name}")
    result = await asyncio.wait_for(session.call_tool(name, arguments={}), timeout=10)
    if result.isError or result.structuredContent is None:
        raise RuntimeError(f"{name} failed or returned no structured result: {result.content}")
    return result.structuredContent


async def probe(session: ClientSession, label: str) -> dict:
    report = await call(session, "probe_leak")
    print(f"{label}: {json.dumps(report, sort_keys=True)}")
    return report


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def run() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("leak_server.py").resolve())],
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            info = await asyncio.wait_for(session.initialize(), timeout=10)
            print(f"initialize: {info.serverInfo.name} / {info.protocolVersion}")
            catalog = await asyncio.wait_for(session.list_tools(), timeout=10)
            names = {tool.name for tool in catalog.tools}
            print(f"tools/list: {', '.join(sorted(names))}")
            require(ALLOWED_TOOLS <= names, "Required lab tools are missing")

            baseline = await probe(session, "baseline")
            require(
                baseline["verdict"] == "CLEAR" and baseline["retained_payload_bytes"] == 0,
                "Expected a clean baseline",
            )
            print(f"inject_leak: {(await call(session, 'inject_leak'))['result']}")
            before_fix = await probe(session, "before_fix")
            require(
                before_fix["verdict"] == "GROWING" and before_fix["growth_payload_bytes"] > 0,
                "Expected a rising retained payload before fix",
            )
            print(f"fix_leak: {(await call(session, 'fix_leak'))['result']}")
            first = await probe(session, "after_fix_1")
            second = await probe(session, "after_fix_2")
            require(
                all(
                    report["verdict"] == "CLEAR"
                    and report["retained_payload_bytes"] == 0
                    and report["growth_payload_bytes"] == 0
                    for report in (first, second)
                ),
                "Expected no retained payload after fix",
            )
            require(
                all(
                    report["processed"] == 32 and report["checksum"] == 528
                    for report in (baseline, before_fix, first, second)
                ),
                "Batch processing regressed",
            )
            print("PASS: retention stopped and batch processing still works")


if __name__ == "__main__":
    asyncio.run(run())
