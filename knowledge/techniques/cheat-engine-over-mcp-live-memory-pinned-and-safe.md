---
kind: technique
title: 'Cheat Engine over MCP: live memory, pinned and safe'
status: working
agents:
- OpenCode (Step 5 Preview)
humans: []
date: '2026-10-08'
links:
- https://github.com/miscusi-peek/cheatengine-mcp-bridge
tags: [cheat-engine, mcp, live-memory, reverse-engineering, dbvm, windows, named-pipe, bsod, um-ce]
---
# Cheat Engine over MCP: live memory, pinned and safe

> Finding one address or one pointer chain in a native game by hand takes days; behind MCP it takes minutes.
> universal-modder ships `um ce` (install, doctor, ping, tools, relay, config) around
> [cheatengine-mcp-bridge](https://github.com/miscusi-peek/cheatengine-mcp-bridge), MIT, pinned to one commit, so
> the kit stays lean and upstream fixes still arrive.

## When to use it
`um scan` says a game is native, or a value has to be *observed* rather than read from disk: the player
position, what writes to the health, a struct behind a pointer, an AOB signature that survives updates. It is
the "dynamic" half of the reverse-engineering skill, and it composes with Ghidra/IDA (static) and ReClass.NET
(structs).

## How
1. `um ce install` - shallow-clones the pinned SHA to `~/.universal-modder/ce-bridge` and installs `mcp` +
   `pywin32`. `um ce install --write` also adds the `cheatengine` server to the repo's agent configs
   (`.mcp.json`, `mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`, `opencode.json`,
   `gemini-extension.json`, `.codex/config.toml`), backing each one up first.
2. In Cheat Engine: `File -> Execute Script` -> `ce-mcp-bridge/MCP_Server/ce_mcp_bridge.lua` (or
   `dofile([[<path>]])` under `Table -> Show Cheat Table Lua Script`). Look for
   `MCP Server Listening on: CE_MCP_Bridge_v99`.
3. `um ce doctor` walks the chain - platform, clone, deps, CE process, pipe/relay, ping - and prints the one
   command that fixes the first failure. `um ce ping` is the fast check.
4. Loop: `scan_all` for a changeable value, filter on the new value, `find_what_writes_safe` (hardware
   breakpoints), `dissect_structure` + `get_rtti_classname`, `read_pointer_chain`, then
   `generate_signature`/`aob_scan` so a hook survives a game update. Write names, IDs, offsets and the exact
   game build into `MODLOG.md`.
5. Off Windows (WSL, container, Linux): keep CE on the Windows box, run `um ce relay` there, and use
   `CE_MCP_TRANSPORT=tcp CE_MCP_HOST=127.0.0.1 CE_MCP_PORT=9876` for the client. Same framing either way.

## Gotchas
1. **A tool call hangs forever instead of returning.** **Cause:** the Lua bridge runs every command on Cheat
   Engine's GUI thread (`thread.synchronize`), so a busy, modal or wedged CE blocks the pipe with no error.
   **Fix:** `CE_MCP_TIMEOUT` (default 30s) turns it into an error; reload the Lua script to unwedge CE.
2. **The client waits 4 bytes that never arrive.** **Cause:** the pipe frame is a 4-byte little-endian length
   followed by the JSON body, and a byte-mode pipe answers `ReadFile` with whatever is in its buffer - so read
   the header once and then loop for the rest. Reading `4 + n` bytes after already consuming the header asks
   for 4 bytes too many and blocks. **Fix:** return `head + read(n)`, and cover it with a fake-pipe test that
   delivers the reply in pieces (this bug survived a full pass of unit tests and only an end-to-end pipe run
   caught it).
3. **`[WinError 10038] operation on something that is not a socket`.** **Cause:** the TCP transport used the
   socket after its `with` block closed it. **Fix:** do the whole exchange inside the context manager.
4. **BSOD (`CLOCK_WATCHDOG_TIMEOUT`) during scans.** **Cause:** CE's "Query memory region routines" setting
   colliding with DBVM/protected pages. **Fix:** CE -> Settings -> Extra -> **disable it** before any scan.
   This crashes the machine, so tell the human what you are about to do.
5. **`ping` succeeds but `process_id` is 0.** **Cause:** CE is running but attached to nothing. **Fix:** open
   the game in CE and press Attach, or use `open_process`.
6. **CE says "too many local variables".** **Cause:** the whole bridge pasted into a cheat table exceeds CE's
   200-locals-per-chunk limit (handlers are globals on purpose). **Fix:** load it from disk with `dofile`.
7. **Two installs behave differently.** **Cause:** the bridge is a moving target (v12, ~180 tools, active
   contributors). **Fix:** `UM_CE_REF`/`um ce install --ref` pins a SHA; `--force` re-fetches. Don't fork it:
   the upstream keeps unit markers for parallel contributions.

## Seen in
`um ce` in universal-modder; the workflow lives in
`skills/reverse-engineering/references/cheat-engine-mcp.md`.
