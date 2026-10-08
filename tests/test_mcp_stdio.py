#!/usr/bin/env python3
"""Drive scratch-mcp/server.py over stdio with raw MCP JSON-RPC, the same
wire protocol MCP clients speak. Uses a throwaway store via SCRATCH_HOME."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "scratch-mcp" / "server.py"
PY = ROOT / ".venv" / "bin" / "python"


class Client:
    def __init__(self, proc):
        self.proc = proc
        self._id = 0
        self._pending = {}

    def send(self, method, params=None, *, notify=False):
        self._id += 1
        msg = {"jsonrpc": "2.0", "method": method, "id": self._id}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        self._pending[self._id] = True
        return self._read_response(self._id)

    def _read_response(self, want_id):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("server closed stdout")
            msg = json.loads(line)
            if msg.get("id") == want_id:
                self._pending.pop(want_id, None)
                return msg
            # ignore notifications / others


def main():
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["SCRATCH_HOME"] = td
        env["SCRATCH_DIR"] = ""  # ensure SCRATCH_HOME wins even if env leaked
        proc = subprocess.Popen(
            [str(PY), str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, env=env, bufsize=1,
        )
        c = Client(proc)
        try:
            init = c.send("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "devin-test", "version": "0"},
            })
            assert "result" in init, f"initialize failed: {init}"
            c.send("notifications/initialized", notify=True)

            tools = c.send("tools/list")
            names = [t["name"] for t in tools["result"]["tools"]]
            assert names == ["scratch"], f"expected exactly one tool 'scratch', got {names}"
            print(f"tools/list -> {names} (1 tool, no content leakage)")

            # set a page (also exercises auto-create)
            r = c.send("tools/call", {
                "name": "scratch",
                "arguments": {"command": "set", "slug": "deploy-pipeline",
                              "content": "SSH step is flaky"},
            })
            txt = r["result"]["content"][0]["text"]
            assert '"ok": true' in txt.replace("'", '"') or '"ok": true' in txt, txt
            print("tools/call set  ->", txt)

            # list should NOT include body content
            r = c.send("tools/call", {
                "name": "scratch", "arguments": {"command": "list"},
            })
            txt = r["result"]["content"][0]["text"]
            assert "flaky" not in txt, f"list leaked body: {txt}"
            assert "deploy-pipeline" in txt
            print("tools/call list -> index only, no body  OK")

            # get SHOULD return body
            r = c.send("tools/call", {
                "name": "scratch", "arguments": {"command": "get", "slug": "deploy-pipeline"},
            })
            txt = r["result"]["content"][0]["text"]
            assert "SSH step is flaky" in txt
            print("tools/call get  -> body returned  OK")

            # search finds it
            r = c.send("tools/call", {
                "name": "scratch", "arguments": {"command": "search", "query": "flaky"},
            })
            txt = r["result"]["content"][0]["text"]
            assert "deploy-pipeline" in txt
            print("tools/call search -> hit  OK")

            # missing get returns ok:false error, not crash
            r = c.send("tools/call", {
                "name": "scratch", "arguments": {"command": "get", "slug": "nope"},
            })
            txt = r["result"]["content"][0]["text"]
            assert '"ok": false' in txt, txt
            print("tools/call get(missing) -> clean error  OK")

            # files landed under SCRATCH_HOME (profile-scoped)
            pages = Path(td) / "topics"
            assert (pages / "deploy-pipeline.md").exists()
            print(f"store root honors SCRATCH_HOME -> {td}  OK")

            def call(**arguments):
                r = c.send("tools/call", {"name": "scratch", "arguments": arguments})
                return r["result"]["content"][0]["text"]

            # open sets the in-memory focus cursor
            txt = call(command="open", slug="deploy-pipeline")
            assert '"focused": "deploy-pipeline"' in txt, txt
            print("tools/call open  -> focus set  OK")

            # new (no body) nests under the focused page
            call(command="new", title="Sub Topic")
            call(command="new", title="Sub Two")
            st = (pages / "sub-topic.md").read_text()
            assert "parent: deploy-pipeline" in st, st
            # siblings snapshot written at creation time
            assert "siblings: [sub-two]" in st, st
            print("tools/call new   -> nested under focus + sibling snapshot  OK")

            # archive drops it from the active list; children stay + re-parent up
            call(command="archive", slug="deploy-pipeline")
            assert not (pages / "deploy-pipeline.md").exists()
            assert (pages / "sub-topic.md").exists()
            txt = call(command="list")
            assert "deploy-pipeline" not in txt, txt
            assert "sub-topic" in txt, txt
            txt = call(command="archived")
            assert "deploy-pipeline" in txt, txt
            print("tools/call archive -> children re-parented up; gone from list  OK")

            # restore brings it back and re-attaches children
            call(command="restore", slug="deploy-pipeline")
            assert (pages / "deploy-pipeline.md").exists()
            assert "parent: deploy-pipeline" in (pages / "sub-topic.md").read_text()
            txt = call(command="list")
            assert "deploy-pipeline" in txt
            print("tools/call restore -> back under parent; children re-attached  OK")

            print("\nALL MCP STDIO CHECKS PASSED")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())