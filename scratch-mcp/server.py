#!/usr/bin/env python3
"""scratch MCP server — exposes the scratch scratchpad as ONE tool.

Any MCP client registers this server and calls the single
`scratch` tool with a `command` field, mirroring how single-handle memory tools
collapse many operations under one name. Page CONTENTS are returned to
the caller only when a command asks (get/search) — never auto-injected.

Storage is profile-scoped: the MCP client passes the store root via the
SCRATCH_HOME env var in its mcp_servers entry. See scratch/core.py.

Run (stdio transport):
    .venv/bin/python scratch-mcp/server.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

# Make the parent dir importable (this dir is scratch-mcp/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scratch import core  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _result(ok: bool, **kw) -> str:
    return _json({"ok": ok, **kw})


# Single-focus cursor, ONE focused page per session. Held by THIS server
# process (in-memory, reset each session), so it costs the agent zero tokens
# and can never go stale across sessions. Only `open` moves it; `new` uses it
# as the implicit parent for nested topics.
_focus: dict = {"current": None}


server = MCPServer(
    "scratch",
    version="1.0.0",
    description=(
        "Persistent, topic-scoped scratchpad (extended working memory). "
        "Mutable current-state pages, one per topic, with lightweight nesting "
        "(each page knows only its direct parent/children/siblings). "
        "Not auto-loaded into context — call this tool to read or write. "
        "Contents appear only when a command requests them (get/search)."
    ),
)


@server.tool(
    name="scratch",
    description=(
        "Read or write the scratchpad. One page per topic; pages persist across "
        "sessions and hold CURRENT state (bodies are overwritten, never append). "
        "Commands: list (flat index of active pages, no bodies), search <query> "
        "(title+summary+body), open <slug> (read body AND set the single-focus "
        "cursor), get <slug> (read body, no cursor), set <slug> <content> "
        "(overwrite body, auto-creates the page), new <title> (create an EMPTY "
        "page — no body; nests under the focused page as its parent), "
        "summarize <slug> <text>, tag <slug> <tags>, archive <slug> (move to "
        "cabinet; re-parents its children one level up), restore <slug> (bring "
        "back + re-link), archived (list cabinet). Nesting: open a topic, then "
        "new <title> to spawn a child under it. New returns an existing page "
        "(\"created\"=false) rather than clobbering a duplicate title. "
        "Returns JSON: {\"ok\": bool, ...}. Use list or search first, then open/get. "
        "archive/restore are destructive-ish and flow through approval."
    ),
)
def scratch(
    command: Literal[
        "list", "search", "open", "get", "set", "new",
        "summarize", "tag", "touch", "archive", "restore", "archived", "info",
    ],
    slug: str = "",
    title: str = "",
    content: str = "",
    summary: str = "",
    query: str = "",
    tag_filter: str = "",
    tags: list[str] | None = None,
) -> str:
    """One handle over the scratchpad operations (see description)."""
    try:
        if command == "list":
            return _result(True, pages=core.op_list(tag_filter))
        if command == "search":
            return _result(True, hits=core.op_search(query))
        if command == "open":
            page = core.op_get(slug)
            if page is None:
                return _result(False, error=f"no page '{slug}'")
            _focus["current"] = slug
            return _result(True, page=page, focused=slug)
        if command == "get":
            page = core.op_get(slug)
            if page is None:
                return _result(False, error=f"no page '{slug}'")
            return _result(True, page=page)
        if command == "set":
            return _json(core.op_set(slug, content))
        if command == "new":
            return _json(core.op_new(title, parent=_focus["current"]))
        if command == "summarize":
            return _result(True, **core.op_summarize(slug, summary))
        if command == "tag":
            return _result(True, **core.op_tag(slug, tags or []))
        if command == "touch":
            return _json(core.op_touch(slug))
        if command == "archive":
            return _json(core.op_archive(slug))
        if command == "restore":
            return _json(core.op_restore(slug))
        if command == "archived":
            return _result(True, pages=core.op_archived())
        if command == "info":
            return _json(core.op_info())
        return _result(False, error=f"unknown command '{command}'")
    except Exception as exc:  # surface as a clean tool error, not a crash
        return _result(False, error=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    server.run(transport="stdio")