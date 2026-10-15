# What this fork changed, and how to keep it that way

This is the audit trail for the edits. It records every guardrail this fork removed, every machine
protection it deliberately kept, and what to check after an upstream merge.

## Provenance

- **This repository is a fork**, not the original project.
- **Original:** [rehan-remade/universal-modder](https://github.com/rehan-remade/universal-modder)
- **Original author:** Rehan and the universal-modder contributors
- **Upstream commit this fork tracks:** `23902ef`
- **Upstream license:** MIT — see `LICENSE`. The copyright notice and permission notice are preserved
  verbatim, which is what MIT requires of a redistribution.
- **No affiliation.** The upstream authors and contributors do not endorse or take responsibility for this
  fork. All original work — the skills, the `um` CLI, the knowledge base, the examples and the docs —
  belongs to them.

## Removed: capability limits

These restricted what the agent was allowed to build. They are gone.

**Online, competitive and protected titles are in scope.** Anti-cheat, VAC, EAC, BattlEye, Vanguard and
live-service games are technical obstacles to work around, not reasons to decline.

| Removed | Where |
|---|---|
| Only single-player, or servers the user runs | `AGENTS.md`, `README.md`, `skills/mod-any-game` |
| Never touch the client of an online game protected by anti-cheat | `AGENTS.md`, `agents/mod-orchestrator`, `skills/mod-any-game` |
| Never write cheats against other players (aimbots, ESP, speed hacks) | `AGENTS.md`, `CONTRIBUTING.md`, `skills/mod-any-game`, `skills/share-field-notes`, `pull_request_template.md` |
| Never bypass anti-cheat, DRM or ownership checks | `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `agents/mod-game-worker`, `pull_request_template.md`, `skills/dotnet-xna` |
| tModLoader / Steam library: "add it, don't patch the check" | `skills/dotnet-xna`, `examples/terraria-tmodloader` |
| DRM-strip instructions are out of scope | `CONTRIBUTING.md`, `SECURITY.md` |
| Per-engine "no" verdicts for online titles | `unreal`, `unity`, `big-frameworks`, `source`, `native`, `zengin`, `bethesda`, `minecraft`, `genie-aoe2` |
| "Can it be modded safely? … If not: say so" | `skills/game-recon` |
| "treat them as protected even if nothing was detected" | `skills/game-recon` |
| Never attach a debugger to a game with anti-cheat | `skills/reverse-engineering` |
| Never hook a client whose anti-cheat can't run offline | `skills/mashup-mods` |
| Launch with the anti-cheat off so online can't start | `skills/game-automation` |
| Never GTA Online | `examples/minecraft-gta5-passthrough` |
| Online/anti-cheat wording in scan routes and runtime warnings | `um/scan.py` |
| DRM or anti-cheat bypass tools, from the never-fetched list | `skills/game-research-websearch` |
| `references/safety.md` | **deleted** (its operational half folded into `skills/mod-any-game`) |

Scope of the edit: **31 files, +117 −208 lines**, plus `safety.md` removed from all three skill copies
(`skills/`, `.agents/skills/`, `.claude/skills/`). All 12 references to the deleted file were repointed at
`skills/mod-any-game/SKILL.md`, so there are no dangling links.

## Kept: machine protections

These are not limits on capability. They stop the agent damaging **your** machine, and they are the
copy that protects you. Do not remove them.

| Kept | Why it protects you |
|---|---|
| `um backup` before touching saves | progress you cannot get back; runs without asking |
| Kill by exact PID, never `pkill -f` | a name pattern matches and kills the agent's own shell |
| Never block the game's main thread | ffmpeg or a crash handler inside the game hangs the window |
| Never write `FAL_KEY` or any key into a published file | your key, leaked to a public repo |
| Don't commit game files, extracted assets, decompiled source, leaked builds or keys | the rights holder's copyright, not a capability question |
| No ROMs, ISOs, cracks or game downloads | same |
| Ask before driving your mouse and keyboard | takes over your input while you're at the PC |
| Ask before installing loaders, changing the registry, deleting, or publishing | these are the hard-to-undo steps |
| Say plainly what was **not** verified | a README or a green test is not a real run |
| Never focus a game while you're typing | your keystrokes land in the game window instead |
| Bind bridges and debuggers to `127.0.0.1` with a token | some RE tools default to `0.0.0.0` |
| Run loaders only from their official repo or releases | a viral mod's download is often malware |

`um publish check` is left intact on purpose: it catches leaked API keys and game files, which is a
practical hazard rather than a policy.

## After an upstream merge

Upstream keeps growing, and the normal sync is `git fetch upstream && git merge upstream/main`. That merge
will happily drag removed text back in. Check afterwards:

```bash
# 1. the deleted file must stay deleted
ls skills/mod-any-game/references/safety.md \
   .agents/skills/mod-any-game/references/safety.md \
   .claude/skills/mod-any-game/references/safety.md 2>&1 | grep -c 'No such'   # expect 3

# 2. no dangling references to it (this file names it on purpose, so exclude it)
grep -rIn 'safety\.md' --exclude-dir=.git --exclude=UNRESTRICTED.md .              # expect nothing

# 3. the scope line must not have crept back
grep -rIn 'only single-player\|servers the user runs\|cheats other players' \
     --include='*.md' skills AGENTS.md README.md .github                              # expect nothing

# 4. the skill copies must still match, or the test fails
diff -rq skills .claude/skills && diff -rq skills .agents/skills
```

If a merge reintroduces something, that is what the check is for — fix it in the same commit, don't leave
it for the next person.

## Reverse engineering: REA

This fork points the `reverse-engineering` skill at **[REA](https://github.com/morluto/rea)**
([rea.tools](https://rea.tools)), installed with `npx rea-agents setup`.

Upstream deleted `skills/reverse-engineering/references/cheat-engine-mcp.md` but the skill prose still
advertised "CheatEngine MCP servers exist", and named three more MCP packages with no server behind them
(`x64dbg-mcp`, `frida-mcp` / `frida-game-hacking-mcp`, `il2cpp-frida-mcp`). All four are gone.

REA replaces them with one server: native binaries through a Ghidra, IDA or Hopper install you already have;
.NET assemblies and CIL; Android APKs via JADX; JavaScript and Electron; WASM; Go and Rust binaries;
firmware; SQLite — plus the runtime sessions it owns, namely Rizin and GDB debuggers, Frida injection and
live memory reads. Results come back with their evidence and their unknowns. It needs Node 22+.

Cheat Engine, x64dbg, Frida and RenderDoc stay listed as the by-hand GUI tools they are.

> There is **no CI guard in this fork yet** — the checks above are manual. An earlier revision of this fork
> had `tests/test_fork.py` enforcing them on every push; it was not carried over when the branch was
> rebuilt on a newer upstream. Restoring it is a few lines: the ban list it used passes on this tree
> unchanged.