# Cheat Engine over MCP: live memory as the agent's eyes

`um ce` wraps [cheatengine-mcp-bridge](https://github.com/miscusi-peek/cheatengine-mcp-bridge) (MIT, (c) 2025
miscusi-peek and contributors): Cheat Engine plus a Lua bridge, driven through ~180 MCP tools. Use it when a
value has to be *found in a running process*: offsets, pointer chains, what writes to an address, struct
layouts, AOB signatures. The bridge is a separate project, pinned by commit SHA, so upstream fixes arrive with
`um ce install --force` and nothing is vendored here.

## Setup (once per machine)

```bash
um ce install            # clones the pinned SHA to ~/.universal-modder/ce-bridge + installs mcp/pywin32
um ce install --write    # also adds the `cheatengine` MCP server to this repo's agent configs
um ce doctor             # walks the whole chain: CE running? Lua bridge loaded? pipe reachable? ping?
```

Then, in Cheat Engine: `File -> Execute Script` -> `.../ce-bridge/MCP_Server/ce_mcp_bridge.lua` (or paste
`dofile([[<path>]])` in `Table -> Show Cheat Table Lua Script`). Look for
`MCP Server Listening on: CE_MCP_Bridge_v99`. Attach to the game (`open_process`, or open it in CE and press
Attach) - `ping` returns `process_id: 0` until then.

Two things to get right before anything else, both from the bridge's own hard-won notes:

1. **CE -> Settings -> Extra -> disable "Query memory region routines".** With it on, memory scans over
   protected pages can take the machine down (`CLOCK_WATCHDOG_TIMEOUT` BSOD), and DBVM makes that more
   likely. This is a machine-crash setting: if the user is at the PC, say what you are about to do.
2. **Keep `CE_MCP_ALLOW_SHELL` unset.** It enables `run_command` / `shell_execute`: arbitrary code execution
   inside whatever Cheat Engine is attached to.

## The loop

1. **`um ce tools <filter>`** to see what exists; ask for one tool at a time rather than trusting the agent to
   guess from ~180 names.
2. **Scan** `scan_all` for a value you can change in game (health, ammo, gold), then filter on the new value.
   Fewer than ~10 hits usually means the search is specific enough.
3. **`find_what_writes` / `find_what_accesses`** on the surviving addresses. That is the function that owns the
   value; hardware breakpoints (DR0-DR3), not `0xCC`, so the game keeps running.
4. **`dissect_structure`** on the address, and `get_rtti_classname` on the pointer above it - RTTI names the
   C++ object ("this is a CPlayerInventory") and gives the struct a story.
5. **`read_pointer_chain`** to walk `[[base+0x10]+0x20]+0x8`. Resolve a chain once, then read the base and add
   the offsets at runtime - never hard-code an absolute address.
6. **`generate_signature` / `aob_scan`** for the function you hook, with wildcards for relocations, so the mod
   survives a game update. Keep a fallback that logs clearly when the pattern is not found.
7. **Write it into `MODLOG.md`**: symbol names, IDs, offsets, formats, and the exact game build you measured.
   What you keep out of the repo: anything read out of the game's memory that is game data or assets - no
   dumps, no extracted files, no decompiled code.

## Useful tools by task

| Task | Tools |
|---|---|
| Find a value | `scan_all`, `aob_scan`, `search_string` |
| Who owns it | `find_what_writes_safe`, `find_what_accesses`, `analyze_function`, `disassemble` |
| Understand it | `dissect_structure`, `get_rtti_classname`, `read_pointer_chain`, `get_symbol_address`, `enum_modules` |
| Survive updates | `generate_signature`, `register_symbol`, `get_address_list` |
| Change behaviour | `assemble_instruction`, `compile_c_code`, `execute_code`, `allocate_memory`, `set_memory_protection` |
| Invisible tracing | `start_dbvm_watch` (ring -1; needs DBVM on in CE settings), `set_breakpoint` (hardware) |

## When the agent runs off Windows (WSL, container, Linux)

The named pipe is Windows-only. Keep Cheat Engine and the Lua bridge on the Windows box, run
`um ce relay` there (bound to 127.0.0.1 unless you mean to expose it - anyone who reaches the relay controls
Cheat Engine), and point the MCP server at it:

```bash
CE_MCP_TRANSPORT=tcp CE_MCP_HOST=127.0.0.1 CE_MCP_PORT=9876 um ce doctor
```

## Gotchas

1. **A tool call hangs instead of failing.** The Lua bridge runs every command on CE's *GUI thread*
   (`thread.synchronize`), so a busy or modal CE blocks the pipe. `CE_MCP_TIMEOUT` (default 30s) turns that
   into an error. Reload the Lua script if CE is wedged.
2. **"too many local variables" in CE.** Load the bridge from disk with `dofile(...)`; pasting the whole script
   into a cheat table hits CE's 200-local-per-chunk limit. The handlers are globals for this reason.
3. **32 vs 64-bit.** Always branch on `targetIs64Bit()` semantics (the bridge's `getArchInfo`); use
   `readPointer`, not `readInteger`, when you mean pointer-sized. Hardcoded pointer sizes break on the other
   architecture.
4. **Reloading the script is safe** (`StartMCPBridge` tears down the old breakpoints, DBVM watches and scan
   objects first). Do it rather than leaving a half-configured bridge in place.
5. **DBVM and DBK are kernel-level.** They are the right tool for protected or anti-cheat-guarded memory, and
   they are also the fastest way to destabilise the machine. Prefer hardware breakpoints first, DBVM watches
   when you need to be invisible, and stop watches you no longer need.
6. **Don't trust one read.** Agents misidentify things confidently. Confirm an address by changing the value
   in game and reading it again, and confirm a function by disassembling around it, before building on it.
