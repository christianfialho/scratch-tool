# scratch — MCP conversion (EXECUTED 2026-09-23)

Status: BUILT AND REGISTERED. This file is the executed proposal (was "DRAFT").
The MCP server (`scratch-mcp/server.py`) is built on the shared `scratch/core.py`,
verified over real stdio JSON-RPC, and registered in BOTH the devin and christian
profiles' `mcp_servers:` blocks with per-profile `SCRATCH_HOME`. CLI + tests +
README retain the human/script path. The clarify tool is disabled for the assistant.

## Goal

Turn `scratch` from a CLI+skill into an always-available, first-class,
profile-scoped tool — the "notebook the assistant carries in hand." One tool
handle with a command enum, mirroring the ergonomics of Hermes' native
`memory` tool, while preserving the core property: **page contents are never
auto-injected into context**; they are returned only when a command asks.

## Grounded facts (from Hermes docs, Sept 2026)

- Built-in tools (`memory`, `cronjob`, `skill_manage`, `session_search`) are
  NATIVE Hermes tools, not MCP. MCP is the integration path for *external*
  servers, registered as `mcp_servers:` in `~/.hermes/config.yaml`.
- MCP stdio servers run as local subprocesses; config supports `command`,
  `args`, and an `env:` block with `${VAR}` / `${userHome}` /
  `~/.hermes/.env` substitution. => we can thread the profile-scoped store
  path into the server cleanly via `env:`.
- MCP tools become their own dynamic toolset (`mcp-<server>`), are
  discovered/registered at startup, reload via `/reload-mcp`, support
  per-server tool filtering/exclusion, and flow through Hermes' normal
  per-call approval for destructive ops.
- Memory storage is per-profile: `~/.hermes/memories/` (docs example shows
  `~/.hermes/profiles/work/memories/`). Memory auto-injects and is capped
  (2,200 / 1,375 chars) precisely because it is tiny and always-loaded.

## Design decisions (proposed)

1. ONE MCP tool named `scratch` with a `command` enum — not six separate
   tools. Mirrors the native `memory` tool shape (action + args + batching).
   Collapses ~6 tool schemas into 1, cutting the always-on token tax to
   ~100-200 tokens/turn.
2. Command set (reuses existing cli.py logic, ~90% of it unchanged):
     list [tag]     -> index only (slug|title|summary|mtime), NO bodies
     search <q>     -> match title+summary+body, snippet
     get <slug>     -> full body   (the ONLY way contents enter context)
     set <slug> [content]  -> overwrite current state (auto-create on miss)
     summarize <slug> <s>  -> set one-line index summary
     tag <slug> <t1,t2>    -> set tags
     rm <slug>       -> delete (flows through approval)
     archive <slug>  -> move to cabinet/ (flows through approval)
3. Profile-scoped storage, mirroring memory's convention:
     store dir resolved from env, default:
       ~/.hermes/profiles/<profile>/scratch/
     and optional explicit override via the mcp_servers env block.
   This is the user's "temp files specific to the profile" requirement.
4. Do NOT copy these memory rules: auto-injection and the tiny hard char cap.
   The scratchpad exists to be bigger and selectively loaded. Profile-scoping
   and the batched single-handle API come over; auto-inject + char cap stay
   out. (This is the one rule we'd be wrong to inherit — it would gut the tool.)
5. KEEP the CLI + a slimmed skill as the human/script path. A terminal CLI is
   still the nicest way for the user to read/edit notes directly; the skill
   shrinks to "an MCP tool exists; here's the command set."

## Migration steps (in order, after go)

1. Add `mcp` Python SDK dependency (isolated venv for the server, so the tool
   stays dependency-light on the system python).
2. Write `scratch-mcp/server.py`: MCP server exposing the one `scratch` tool.
   Import/reuse the frontmatter + store logic from `scratch/cli.py` (refactor
   shared core into `scratch/core.py` if cleaner; keep cli.py as a thin cli
   wrapper over core so both stay in sync).
3. Add profile-aware store resolution to core.py: look up the profile home
   (env), default store under `~/.hermes/profiles/<profile>/scratch/`.
4. Register the server in the DEVIN profile's `config.yaml`:
     mcp_servers:
       scratch:
         command: "<venv>/bin/python"
         args: ["<abs>/scratch-mcp/server.py"]
         env:
           SCRATCH_HOME: "~/.hermes/profiles/devin/scratch"
   (Test in the devin profile first; generalize to other profiles / the
   shared default after it works. COPY not symlink for other profiles until
   we confirm the skill/CLI split.)
5. Verify: `hermes mcp configure scratch`, `hermes chat`/`/reload-mcp`, then
   exercise list/get/set/summarize/tag/rm/archive via the agent and confirm
   page contents appear only on `get`.
6. Slim the `scratch` skill to point at the MCP tool; keep the README for the
   full design rationale + CLI reference. Re-run the unit test suite.
7. Cross-profile: COPY the server config + store path into the Christian
   profile once the devin profile is proven, per the cross-profile guard.

## Open decisions for the user (need explicit answers)

A. Build MCP now, or keep skill-first with just profile-scoped storage first?
B. Store location default — confirm `~/.hermes/profiles/<profile>/scratch/`.
C. Keep CLI + slimmed skill alongside the MCP server (my rec: yes), or retire
   the CLI once MCP works?
D. Command set — keep search + tags + archive, or trim for a leaner schema?
E. Destructive ops (rm/archive) — leave to normal Hermes approval (my rec), or
   add an explicit confirm flag?

## Token reality check (heuristic ~4 chars/token)

- Current skill: 90 warm/turn, +1260 on invoke, ~20-26 per data fetch.
- Proposed MCP (one tool): ~100-200 warm/turn ALWAYS (user accepts), no skill
   body load, same ~20-100 per data fetch.
- Either way page CONTENTS stay cold until `get` — the hard constraint is
  satisfied identically. MCP's cost is a small permanent one-tool schema tax;
  its benefit is the always-in-hand first-class feel.