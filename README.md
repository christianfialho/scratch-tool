# scratch — a topic-scoped scratchpad for an AI assistant

A persistent scratchpad of mutable "current state" pages, one per topic.
It can outlive a single session, but it is never auto-loaded into context and
it isn't an archive either; it's the middle tier between always-loaded memory
and the filing cabinet.

```
memory         always in context · permanent · tiny
conversations  auto per-session  · transient log
tasks          on-demand         · actionable (TODOIST)
scratch        on-demand, per-topic · mutable CURRENT STATE      <- this
cabinet        on-demand         · permanent reference/archive
```

## Design properties

Pages are plain files on disk, so state survives across sessions. One page
is one file is one slug, divisible by topic.

`list`/`search` return only the index (slug, title, one-line summary, mtime);
`get` loads the full page only when a topic is actually active. That's the
point of the one-line summary: it keeps the scan decision cheap, so nothing
lands in context unless you ask.

`set` overwrites the whole body. No journal, no append-only log; the body is
the state, nothing else. `archive` moves a page to the cabinet (`restore`
brings it back) and there is no hard delete, so every change is reversible.
Create/update are single-file writes; no schema, no server.

The body is free-form markdown. A page doesn't have to be finished, or valid,
or even coherent; it's a place to hold half-formed thinking. This is not a
replacement for conversation history, tasks (TODOIST), archived reference
docs, or always-loaded memory. It deliberately sits in the gap between them.

## Storage layout

```
$SCRATCH_HOME  or  $SCRATCH_DIR     (SCRATCH_HOME wins; default ~/.scratch)
  topics/<slug>.md        the mutable pages
  cabinet/<slug>.md       archive target
```

An MCP client passes the store root via `SCRATCH_HOME`, so each client
configuration (or each agent profile) gets its own isolated scratchpad.

A page is markdown with tiny frontmatter:

```markdown
---
title: Deploy pipeline
tags: [infra, deploy]
summary: Migrating to GH Actions; the SSH step is flaky
parent: projects          # slug of parent page, if nested
children: [cf-github-actions]   # direct children (slugs)
siblings: [circleci-migration]  # snapshot of siblings at last structural write
created: 2026-09-22T13:34:21-06:00
updated: 2026-09-22T13:34:21-06:00
---

Migrating from CircleCI. The SSH step (ssh-add) is flaky —
investigate whether the agent socket survives a reboot.
```

The index is derived, not stored: `list`/`search` read each page's small
frontmatter at call time and never touch full bodies, so there's no index
file that can drift out of sync with the pages.

Nesting stays lightweight: a page knows only its direct `parent`, `children`,
and a `siblings` snapshot. `list` stays a flat slug list and never renders a
tree. `parent`/`children` are the authoritative edges; `siblings` is stored
as written and refreshed on structural changes.

## Commands

```
scratch new "<Title>"          create a page (empty)
scratch new "<Title>" --parent slug   nest under an existing parent
scratch touch <slug>           create empty / bump mtime
scratch open <slug>            print full body (CLI: same as get — no cursor)
scratch get <slug>             print full body          (agent: reload an active topic)
scratch list [--tag a,b]       index only: slug|title|summary|mtime  (cheap)
scratch search <q>             match in titles+summaries+bodies
scratch set <slug> [-m body]   overwrite body from arg or stdin   (agent's main update)
scratch summarize <slug> ".."  set one-line summary for the index
scratch tag <slug> a,b         set tags (enable list --tag grouping)
scratch archive <slug>         move to cabinet/; its children re-parent one level up
scratch restore <slug>         bring back from cabinet/, re-link parent/children
scratch archived               list the cabinet/
scratch edit <slug>            open in $EDITOR            (human use)
scratch info                   show store path + page count
```

`scratch set` auto-creates the page if it doesn't exist. Cheap create is a core
property: writing state never requires a separate create step.

## Agent protocol (recommended)

1. Scan with `scratch list` (or `search <q>` when a topic is hinted). Read only
   the index; don't load bodies.
2. If a slug looks relevant, `scratch get <slug>` to pull current state.
3. Work on it in your own context.
4. Persist with `scratch set <slug> -m "<new current state>"` (a full replace),
   then `scratch summarize <slug> "<one-liner>"` so the index stays cheap on
   the next scan.
5. When a topic is done but worth keeping, `scratch archive <slug>` (moves it
   to the cabinet); `scratch restore <slug>` brings it back. Archiving is the
   removal primitive; there is no hard delete.

Use the summary line aggressively: it's the gate that keeps `list`/`search` cheap and
lets you defer loading a page until it's actually on-topic.

### Nesting via the `new` flow

`new` creates an empty page; it takes no body content. To write state you use
`set`, which overwrites an existing body or auto-creates a missing page and never
fails on existence. `new "X"` on an existing `X` returns the existing page
(`created:false`) rather than clobbering it or adding a wrong link.

So the intended nesting flow is: `new "<Title>"` (empty shell) → optionally
`open <slug>` (focus it) → `set <slug> -m "..."` (write current state). In the
CLI, nest explicitly with `new "<Title>" --parent <slug>`.

## Focus cursor (MCP only)

The MCP server keeps a single in-memory focus (one page per session, reset
each session; it costs zero tokens and can't go stale). `open` sets it; `new`
uses it
as the implicit `parent`, so the agent spawns children just by opening a topic
and running `new`, never passing parent slugs: `open projects` then
`new "GH Actions"` makes `gh-actions` a child of `projects`. The CLI has no
cursor and uses `new --parent` instead.

## Design decisions

- Overwrite, no history/undo. This matches "current state over historical
  record". You give up accidental-edit recovery; use the cabinet for anything
  that must persist.
- Plain files over SQLite. Cheaper to create and inspect, diffable via git if
  a journal is ever wanted, and a human can read the store directly.
- No embeddings in v1. Tags plus substring search keep selection predictable
  and dependency-free. Vector recall can wait until the agent actually
  struggles to find pages.
- Derived index instead of a separate INDEX file, so the index can't drift
  out of sync with the pages.
- Archive instead of delete. `rm` was removed in favor of `archive`/`restore`
  so every change is reversible; archiving re-parents a page's children one
  level up rather than orphaning them.

## Install

Requires only Python 3.8+ (stdlib) for the CLI. No third-party deps.

```bash
ln -s ~/scratch-tool/bin/scratch ~/.local/bin/scratch
export SCRATCH_DIR=~/.scratch          # optional; this is the default
```

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

## MCP server

The CLI's storage logic lives in `scratch/core.py`, shared with an MCP server at
`scratch-mcp/server.py` that exposes the same scratchpad as a single `scratch`
tool with a `command` enum
(`list|search|open|get|set|new|summarize|tag|archive|restore|archived|info`).

- One tool schema means roughly 100-200 warm tokens per turn, versus a
  6-tool server.
- `list`/`search` return the index only, never page bodies, so the whole pad
  never lands in context unless the agent calls `get`/`open` for an active
  topic.
- Storage is scoped per client via `env: SCRATCH_HOME` in the client's MCP
  server registration.

Register (any MCP client, JSON form):

```json
{
  "mcpServers": {
    "scratch": {
      "command": "/path/to/scratch-tool/.venv/bin/python",
      "args": ["/path/to/scratch-tool/scratch-mcp/server.py"],
      "env": { "SCRATCH_HOME": "/path/to/scratch-store" }
    }
  }
}
```

The server needs the `mcp` SDK; it's installed in the venv above. Rebuild it with:

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python mcp
```

Integration check (spawns the server and speaks real MCP JSON-RPC over stdio):

```bash
.venv/bin/python tests/test_mcp_stdio.py
```

## Roadmap (not built)

- `scratch list --stale 7d` — surfaces topics untouched for N days (candidates for
  rm/archive).
- git-based opt-in history per page (`scratch init-git`) if undo is ever wanted.
- embedding index behind `search` for fuzzy recall.