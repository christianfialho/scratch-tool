"""End-to-end tests for the scratch CLI.

Each test runs against an isolated store in a temp dir via the SCRATCH_DIR
env var, so we never touch the real ~/.scratch.

Run:
    SCRATCH_DIR="$(mktemp -d)"          # not even needed; isolated per test
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "scratch"


def run(store: Path, *args: str) -> subprocess.CompletedProcedure:
    env = dict(os.environ)
    env["SCRATCH_HOME"] = ""  # temp SCRATCH_DIR must win; drop any leaked home
    env["SCRATCH_DIR"] = str(store)
    return subprocess.run(
        [sys.executable, str(BIN), *args],
        capture_output=True,
        text=True,
        env=env,
    )


class ScratchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name)
        self.topics = self.store / "topics"

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_creates_page(self):
        r = run(self.store, "new", "Deploy pipeline")
        self.assertEqual(r.returncode, 0)
        self.assertIn("deploy-pipeline", r.stdout)
        self.assertTrue((self.topics / "deploy-pipeline.md").exists())

    def test_set_autocreates_page(self):
        r = run(self.store, "set", "foo", "-m", "body text")
        self.assertEqual(r.returncode, 0)
        self.assertTrue((self.topics / "foo.md").exists())

    def test_set_overwrites_whole_body(self):
        run(self.store, "set", "foo", "-m", "version one")
        run(self.store, "set", "foo", "-m", "version two")
        out = run(self.store, "get", "foo")
        self.assertIn("version two", out.stdout)
        self.assertNotIn("version one", out.stdout)

    def test_set_reads_stdin(self):
        r = subprocess.run(
            [sys.executable, str(BIN), "set", "foo"],
            input="hello from stdin",
            capture_output=True,
            text=True,
            env={**os.environ, "SCRATCH_HOME": "", "SCRATCH_DIR": str(self.store)},
        )
        self.assertEqual(r.returncode, 0)
        out = run(self.store, "get", "foo")
        self.assertIn("hello from stdin", out.stdout)

    def test_mutation_updates_mtime_frontmatter(self):
        run(self.store, "set", "foo", "-m", "a")
        fm1 = (self.topics / "foo.md").read_text()
        run(self.store, "set", "foo", "-m", "b")
        fm2 = (self.topics / "foo.md").read_text()
        import re
        u1 = re.search(r"^updated: (.*)$", fm1, re.M).group(1)
        u2 = re.search(r"^updated: (.*)$", fm2, re.M).group(1)
        self.assertNotEqual(u1, u2)

    def test_summarize_and_tag(self):
        run(self.store, "new", "Topic")
        run(self.store, "summarize", "topic", "A one-line summary")
        run(self.store, "tag", "topic", "a,b")
        out = run(self.store, "list")
        self.assertIn("A one-line summary", out.stdout)
        self.assertIn("a, b", out.stdout)

    def test_list_shows_index_not_bodies(self):
        run(self.store, "set", "foo", "-m", "SECRET-BODY")
        out = run(self.store, "list")
        self.assertIn("foo", out.stdout)
        self.assertNotIn("SECRET-BODY", out.stdout)

    def test_list_tag_filter(self):
        run(self.store, "new", "Alpha")
        run(self.store, "tag", "alpha", "x")
        run(self.store, "new", "Beta")
        out = run(self.store, "list", "--tag", "x")
        self.assertIn("alpha", out.stdout)
        self.assertNotIn("beta", out.stdout)

    def test_search_finds_in_summary_body_title(self):
        run(self.store, "new", "Deploy pipeline")
        run(self.store, "set", "deploy-pipeline", "-m", "SSH step is flaky")
        for q in ("flaky", "deploy", "pipeline"):
            out = run(self.store, "search", q)
            self.assertEqual(out.returncode, 0, f"query {q!r} failed")
            self.assertIn("deploy-pipeline", out.stdout, f"query {q!r} missed")

    def test_search_miss(self):
        run(self.store, "new", "Alpha")
        out = run(self.store, "search", "zzz-noway")
        self.assertIn("no matches", out.stdout)

    def test_new_existing_does_not_duplicate(self):
        run(self.store, "new", "Deploy pipeline")
        r = run(self.store, "new", "Deploy pipeline")
        self.assertEqual(r.returncode, 0)
        self.assertIn("exists", r.stdout)
        self.assertEqual(len(list(self.topics.glob("*.md"))), 1)

    def test_new_under_parent_sets_links(self):
        run(self.store, "new", "Project")
        run(self.store, "new", "Task One", "--parent", "project")
        run(self.store, "new", "Task Two", "--parent", "project")
        t1 = (self.topics / "task-one.md").read_text()
        self.assertIn("parent: project", t1)
        # siblings snapshot written at the last structural write
        self.assertIn("siblings: [task-two]", t1)
        pt = (self.topics / "project.md").read_text()
        self.assertIn("children: [task-one, task-two]", pt)

    def test_new_parent_missing_errors(self):
        run(self.store, "new", "Task One", "--parent", "nope")
        r = run(self.store, "get", "task-one")
        self.assertEqual(r.returncode, 1)  # page not created against a bad parent

    def test_archive_moves_to_cabinet(self):
        run(self.store, "new", "Foo")
        run(self.store, "archive", "foo")
        self.assertFalse((self.topics / "foo.md").exists())
        self.assertTrue((self.store / "cabinet" / "foo.md").exists())
        # gone from list
        out = run(self.store, "list")
        self.assertNotIn("foo", out.stdout)

    def test_archive_reparents_children_up(self):
        run(self.store, "new", "Project")
        run(self.store, "new", "Task One", "--parent", "project")
        run(self.store, "new", "Sub Task", "--parent", "task-one")
        r = run(self.store, "archive", "task-one")
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.topics / "task-one.md").exists())
        self.assertTrue((self.store / "cabinet" / "task-one.md").exists())
        # sub-task re-parented up to the archived page's parent
        st = (self.topics / "sub-task.md").read_text()
        self.assertIn("parent: project", st)
        pt = (self.topics / "project.md").read_text()
        self.assertIn("sub-task", pt)

    def test_restore_reparents_back(self):
        run(self.store, "new", "Project")
        run(self.store, "new", "Task One", "--parent", "project")
        run(self.store, "new", "Sub Task", "--parent", "task-one")
        run(self.store, "archive", "task-one")
        r = run(self.store, "restore", "task-one")
        self.assertEqual(r.returncode, 0)
        self.assertTrue((self.topics / "task-one.md").exists())
        self.assertFalse((self.store / "cabinet" / "task-one.md").exists())
        # re-linked under its original parent and child re-attached
        self.assertIn("parent: project", (self.topics / "task-one.md").read_text())
        self.assertIn("parent: task-one", (self.topics / "sub-task.md").read_text())

    def test_archived_lists_cabinet_only(self):
        run(self.store, "new", "Foo")
        run(self.store, "archive", "foo")
        out = run(self.store, "archived")
        self.assertIn("foo", out.stdout)
        not_archived = run(self.store, "list")
        self.assertNotIn("foo", not_archived.stdout)

    def test_missing_get_errors(self):
        r = run(self.store, "get", "nope")
        self.assertEqual(r.returncode, 1)

    def test_roundtrip_frontmatter_preserved(self):
        run(self.store, "new", "Multi Word Title")
        run(self.store, "set", "multi-word-title", "-m", "body")
        run(self.store, "summarize", "multi-word-title", "s")
        run(self.store, "tag", "multi-word-title", "infra,deploy")
        text = (self.topics / "multi-word-title.md").read_text()
        self.assertIn("title: Multi Word Title", text)
        self.assertIn("summary: s", text)
        self.assertIn("tags: [infra, deploy]", text)


if __name__ == "__main__":
    unittest.main()