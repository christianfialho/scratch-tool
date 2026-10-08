# scratch — a topic-scoped scratchpad for an AI assistant

Persistent, lightweight **working memory**: mutable pages of current state, one per
topic, that can outlive a single session — but are *not* automatically loaded into
context and are *not* permanent archives. The middle tier between always-loaded
memory and the filing cabinet.

```
memory         always in context · permanent · tiny
conversations  auto per-session  · transient log
tasks          on-demand         · actionable (TODOIST)
scratch        on-demand, per-topic · mutable CURRENT STATE      <- this
cabinet        on-demand         · permanent reference/archive
```

## Design properties (from the source spec)

- **Persistent across sessions** — pages are plain files on disk.
- **Divisible by topic** — one page = one file = one slug.
- **Selectively retrievable** — `list`/`search` return only the index (slug, title,
  one-line summary, mtime); `get` loads the full page only when a topic is active.
- **Current-state optimized** — `set` *overwrites* the whole body. No journal, no
  append-only log. The body is the state, nothing else.
- **Mutable** — overwrite in place; `archive` moves a page to the cabinet
  (`restore` brings it back); there is no hard delete, everything is reversible.
- **Cheap** — create/update/delete are single-file writes. No schema, no server.
- **Unfinished thinking** — the body is free-form markdown. No requirement that a
  page be "done" or "valid".
- **Not auto-injected into context** — nothing is loaded unless the agent invokes
  `scratch`. The one-line summary keeps that decision cheap.
- **Not a replacement for** — conversation history, tasks (TODOIST), archived
  reference docs, or always-loaded memory. It deliberately sits in the gap.

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

The index is **derived**, not stored: `list`/`search` read each page's small
frontmatter at call time and never touch full bodies. No index file to drift.

Nesting is **lightweight**: a page knows only its direct `parent`, `children`,
and a `siblings` snapshot. The active `list` stays a flat slug list — it never
renders the tree. `parent`/`children` are the authoritative edges; `siblings`
is stored "as written" and refreshed on structural changes.

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

`scratch set` **auto-creates** the page if it doesn't exist — cheap create is a core
property, so writing state never requires a separate create step.

## Agent protocol (recommended)

1. **Scan** — `scratch list` (or `search <q>` when a topic is hinted). Read only the
   index. Do *not* load bodies.
2. **Load** — if a slug looks relevant, `scratch get <slug>` to pull current state.
3. **Work** — mutate in your own context.
4. **Persist** — `scratch set <slug> -m "<new current state>"` to *replace*; then
   `scratch summarize <slug> "<one-liner>"` so the index stays cheap next scan.
5. **Close** — when a topic is done but must be kept, `scratch archive <slug>`
   (moves it to the cabinet); `scratch restore <slug>` brings it back. There is no
   hard delete — archiving is the removal primitive.

Use the summary line aggressively: it's the gate that keeps `list`/`search` cheap and
lets you defer loading a page until it's actually on-topic.

### Nesting via the `new` flow

`new` creates an **empty** page — it takes no body content. To write state you use
`set`, which overwrites an existing body or auto-creates a missing page and never
fails on existence. `new "X"` on an existing `X` returns the existing page
(`created:false`) rather than clobbering it or adding a wrong link.

So the intended nesting flow is: `new "<Title>"` (empty shell) → optionally
`open <slug>` (focus it) → `set <slug> -m "..."` (write current state). In the
CLI, nest explicitly with `new "<Title>" --parent <slug>`.

## Focus cursor (MCP only)

The MCP server keeps a single in-memory focus (one page per session, reset each
session — it costs zero tokens and can't go stale). `open` sets it; `new` uses it
as the implicit `parent`, so the agent spawns children just by *opening* a topic
and running `new`, never passing parent slugs: `open projects` then
`new "GH Actions"` makes `gh-actions` a child of `projects`. The CLI has no cursor
— it uses `new --parent`.

## Design decisions (made on purpose)

- **Overwrite, no history/undo.** Matches "current state over historical record."
  You trade accidental-edit recovery; use the cabinet for anything that must persist.
- **Plain files over SQLite.** Cheaper to create/inspect, diffable via git if you
  ever want a journal, and a human can read the store directly.
- **No embeddings in v1.** Tags + substring search keep selection predictable and
  dependency-free. Add vector recall only if the agent struggles to find pages.
- **Derived index** (no separate INDEX file) — can't drift out of sync with pages.
- **Archive, no delete.** `rm` was removed in favor of `archive`/`restore` so every
  change is reversible; archiving re-parents a page's children one level up rather
  than orphaning them.

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
`scratch-mcp/server.py` that exposes the same scratchpad as a **single `scratch`
tool** with a `command` enum
(`list|search|open|get|set|new|summarize|tag|archive|restore|archived|info`).

- One tool schema ⇒ ~100–200 warm tokens/turn (vs a 6-tool server).
- `list`/`search` return **index only — never page bodies** — so the whole pad is
  never in context unless the agent calls `get`/`open` for an active topic.
- Storage is scoped per client via `env: SCRATCH_HOME` in the client's
  MCP server registration.

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