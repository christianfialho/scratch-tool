#!/usr/bin/env python3
"""scratch — a lightweight topic-scoped scratchpad for an AI agent.

This file is a thin CLI wrapper over scratch.core (which holds the shared
storage logic also used by the MCP server). Command surface and output format
are stable — source of truth for logic is core.py.

Design goals (from spec):
  - Persistent across sessions (files on disk).
  - Divisible by topic (one page = one file).
  - Small enough to retrieve selectively (list shows index only; get shows body).
  - Optimized for current state (body is overwritten, not append-only).
  - Mutable, cheap to create/update/delete.
  - Suitable for unfinished thinking (free-form markdown).
  - Not automatically injected into context (agent calls this explicitly).
  - Not a replacement for conversation history / tasks / archives / memory.

Usage:
    scratch new "<Title>" [--parent slug]   create a page (empty; nest under parent)
    scratch touch <slug>           (re)create empty or bump mtime
    scratch open <slug>            print full body (CLI: same as get — no cursor)
    scratch get <slug>             print full body
    scratch list [--tag t]         print index (slug | title | summary | mtime)
    scratch search <q>             match titles + summaries + bodies
    scratch set <slug> [-m body]   overwrite body (stdin if no -m)
    scratch summarize <slug> "..." update one-line summary
    scratch tag <slug> t1,t2       set tags
    scratch archive <slug>         move to cabinet; children re-parent one level up
    scratch restore <slug>         bring back from cabinet, re-link
    scratch archived               list cabinet contents
    scratch edit <slug>            open in $EDITOR
    scratch info                   show store path + counts
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from scratch.core import (
    cabinet_dir,
    corpus_tags,
    find_slug,
    index_rows,
    now_iso,
    op_archive,
    op_archived,
    op_get,
    op_new,
    op_restore,
    op_set,
    op_summarize,
    op_tag,
    op_touch,
    read_page,
    render_page,
    slugify,
    store_root,
    topics_dir,
)


# --------------------------------------------------------------------------- #
# Commands (formatting only — real logic lives in core)
# --------------------------------------------------------------------------- #

def cmd_new(args):
    root = store_root()
    slug = slugify(args.title)
    res = op_new(args.title, parent=getattr(args, "parent", None) or None)
    path = find_slug(root, slug)
    if res["created"]:
        tag = f"  (child of {res['parent']})" if res.get("parent") else ""
        print(f"created {slug}  ->  {path}{tag}")
    elif "error" in res:
        print(res["error"], file=sys.stderr)
        return 1
    else:
        print(f"exists  {slug}  ->  {path}  (use 'archive' then 'new', or 'touch' to reset)")
    return 0


def cmd_touch(args):
    res = op_touch(args.slug)
    print(f"{'created' if res['created'] else 'touched'} {args.slug}")
    return 0


def cmd_get(args):
    res = op_get(args.slug)
    if res is None:
        print(f"no page '{args.slug}'", file=sys.stderr)
        return 1
    print(res["body"], end="")
    return 0


def cmd_list(args):
    rows = index_rows(store_root())
    if args.tag:
        need = {t.strip().lower() for t in args.tag.split(",")}
        rows = [r for r in rows if need <= {t.lower() for t in corpus_tags(r[1])}]
    if not rows:
        print("(empty)")
        return 0
    width = max(len(r[0]) for r in rows)
    for slug, fm, _body, mtime in rows:
        up = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        title = fm.get("title") or slug
        summary = fm.get("summary") or ""
        tags = corpus_tags(fm)
        tag_txt = f"  [{', '.join(tags)}]" if tags else ""
        print(f"{slug:<{width}}  {up}  {title}{tag_txt}")
        if summary:
            print(f"{'':<{width}}  {summary}")
    return 0


def cmd_search(args):
    q = args.query.lower()
    hits = []
    for slug, fm, body, mtime in index_rows(store_root()):
        title = (fm.get("title") or slug).lower()
        summary = (fm.get("summary") or "").lower()
        if q in title or q in summary or q in body.lower():
            hits.append((slug, fm, body, mtime))
    if not hits:
        print("(no matches)")
        return 0
    for slug, fm, body, mtime in hits:
        up = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        print(f"== {slug}  ({up}) ==")
        if fm.get("summary"):
            print(f"   summary: {fm['summary']}")
        idx = body.lower().find(q)
        if idx >= 0:
            start = max(0, idx - 40)
            end = min(len(body), idx + len(q) + 60)
            print(f"   …{body[start:end].strip()}…")
    return 0


def cmd_set(args):
    if args.message is not None:
        body = args.message
    else:
        body = sys.stdin.read()
    res = op_set(args.slug, body)
    print(f"{'created+set' if res['created'] else 'set'} {args.slug}")
    return 0


def cmd_summarize(args):
    res = op_summarize(args.slug, args.summary)
    if not res["ok"]:
        print(res["error"], file=sys.stderr)
        return 1
    print(f"summary set on {args.slug}")
    return 0


def cmd_tag(args):
    res = op_tag(args.slug, args.tags)
    if not res["ok"]:
        print(res["error"], file=sys.stderr)
        return 1
    print(f"tags set on {args.slug}: {res['tags']}")
    return 0


def cmd_archive(args):
    res = op_archive(args.slug)
    if not res["ok"]:
        print(res["error"], file=sys.stderr)
        return 1
    print(f"archived {args.slug}  ->  {res['path']}")
    return 0


def cmd_restore(args):
    res = op_restore(args.slug)
    if not res["ok"]:
        print(res["error"], file=sys.stderr)
        return 1
    print(f"restored {args.slug}  ->  {res['path']}")
    return 0


def cmd_archived(args):
    rows = op_archived()
    if not rows:
        print("(archive empty)")
        return 0
    for row in rows:
        print(f"{row['slug']}  {row['title']}")
    return 0


def cmd_open(args):
    return cmd_get(args)


def cmd_edit(args):
    root = store_root()
    path = find_slug(root, args.slug)
    if not path.exists():
        topics_dir(root).mkdir(parents=True, exist_ok=True)
        now = now_iso()
        fm = {"title": args.slug, "tags": [], "summary": "", "created": now, "updated": now}
        path.write_text(render_page(fm, ""), encoding="utf-8")
    editor = os.environ.get("EDITOR", "vi")
    subprocess.call([editor, str(path)])
    fm, body = read_page(path)
    fm["updated"] = now_iso()
    path.write_text(render_page(fm, body), encoding="utf-8")
    print(f"edited {args.slug}")
    return 0


def cmd_info(args):
    root = store_root()
    n = len(index_rows(root))
    print(f"store : {root}")
    print(f"pages : {n}")
    print(f"env   : SCRATCH_HOME={os.environ.get('SCRATCH_HOME', '')} "
          f"SCRATCH_DIR={os.environ.get('SCRATCH_DIR', '(default)')}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="scratch",
        description="Topic-scoped scratchpad: mutable current state, selective retrieval.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="create a page")
    p.add_argument("title")
    p.add_argument("--parent", help="nest under this parent slug (default: top-level)")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("touch", help="create empty or bump mtime")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_touch)

    p = sub.add_parser("get", help="print full body (agent reloads a topic)")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_get)

    p = sub.add_parser("list", help="list index (cheap, no bodies)")
    p.add_argument("--tag", help="filter by tag(s), comma-separated")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("search", help="match query in titles+summaries+bodies")
    p.add_argument("query")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("set", help="overwrite body (agent's main update)")
    p.add_argument("slug")
    p.add_argument("-m", "--message", help="body text; or pipe stdin")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("summarize", help="set one-line summary for index")
    p.add_argument("slug")
    p.add_argument("summary")
    p.set_defaults(fn=cmd_summarize)

    p = sub.add_parser("tag", help="set tags")
    p.add_argument("slug")
    p.add_argument("tags")
    p.set_defaults(fn=cmd_tag)

    p = sub.add_parser("open", help="print full body (read; no cursor in CLI)")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_open)

    p = sub.add_parser("archive", help="move to cabinet; children re-parent up")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_archive)

    p = sub.add_parser("restore", help="bring back from cabinet and re-link")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("archived", help="list cabinet contents")
    p.set_defaults(fn=cmd_archived)

    p = sub.add_parser("edit", help="open page in $EDITOR")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("info", help="show store path and count")
    p.set_defaults(fn=cmd_info)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())