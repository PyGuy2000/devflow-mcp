#!/usr/bin/env python3
"""The server over stdio from an empty state dir, the way a fresh install runs it.

Spawns server.py exactly as the plugin's .mcp.json does (python3 server.py),
speaks MCP over stdio with the ``mcp`` client, and proves a project and a
ticket can be created and read back, and that the state file appears in the
config dir on the first write. Nothing touches your real state.

Also checks that server.py picked the server class belonging to the installed
SDK major. mcp 2.0 renamed FastMCP to MCPServer; server.py accepts both, and
that import is the kind of thing a later edit quietly hardcodes back to one
name. Run under both majors to mean anything, which CI does.

    python3 test_mcp_stdio.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"


def _payload(result) -> dict:
    """The tool's JSON reply; a non-JSON reply (an error string) comes back under "_raw"."""
    for block in result.content:
        if getattr(block, "type", "") == "text":
            try:
                data = json.loads(block.text)
            except ValueError:
                return {"_raw": block.text}
            return data if isinstance(data, dict) else {"_value": data}
    return {}


async def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="devflow-stdio-test-"))
    env = {**os.environ, "DEVFLOW_CONFIG_DIR": str(tmp)}
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS {name}")
        else:
            failed += 1
            print(f"  FAIL {name} {detail}")

    # The SDK class server.py resolved, against the SDK actually installed.
    import importlib.metadata as _meta
    import importlib.util as _util

    major = int(_meta.version("mcp").split(".")[0])
    spec = _util.spec_from_file_location("_devflow_server_under_test", SERVER)
    module = _util.module_from_spec(spec)
    spec.loader.exec_module(module)
    resolved = type(module.mcp).__name__
    expected = "MCPServer" if major >= 2 else "FastMCP"
    check(f"mcp {major}.x resolves to {expected}", resolved == expected,
          f"got {resolved}")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            check("18 tools listed", len(tools) >= 18, sorted(tools))
            for name in ("create_project", "add_ticket", "get_ticket", "get_status_report", "refresh_adr_index", "list_adrs"):
                check(f"tool {name} present", name in tools)
            check("state file absent before the first write", not (tmp / "devflow_state.json").exists())
            r = _payload(await session.call_tool("create_project", {"name": "fresh_app", "goal": "prove the install"}))
            check("create_project ok", r.get("success") is True or "project" in r, r)
            r = _payload(await session.call_tool("add_ticket", {"title": "First ticket", "why": "prove a ticket can be made", "project": "fresh_app", "priority": "high"}))
            tid = (r.get("ticket") or {}).get("id") or r.get("id")
            check("add_ticket returned an id", isinstance(tid, str) and tid.startswith("T-"), r)
            r = _payload(await session.call_tool("get_ticket", {"ticket_id": tid}))
            check("get_ticket reads it back", (r.get("ticket") or {}).get("title") == "First ticket", r)
            r = _payload(await session.call_tool("get_status_report", {}))
            check("status report counts the project", r.get("project_count") == 1, {k: r.get(k) for k in ("project_count", "summary")})
            check("state file created in the config dir", (tmp / "devflow_state.json").exists())
            state = json.loads((tmp / "devflow_state.json").read_text(encoding="utf-8"))
            check("state holds one project and one ticket", len(state["projects"]) == 1 and len(state["tickets"]) == 1)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
