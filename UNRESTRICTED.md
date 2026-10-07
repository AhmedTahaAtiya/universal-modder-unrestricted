# What this fork changed, and how to keep it that way

This file is the audit trail for the removals. `tests/test_fork.py` enforces it: if any phrase below
reappears in `skills/`, `AGENTS.md`, `README.md` or `.github/`, CI fails.

## Why the check exists

`upstream` keeps growing. The normal way to sync a fork is `git fetch upstream && git merge upstream/main`,
which will happily drag removed text back in. Here that merge fails loudly instead of silently, because
`test_fork.py` runs in `.github/workflows/test.yml` on every push.

## Removed: capability limits

These restricted what the agent was allowed to build. They are gone.

**Online games are in scope.** Anti-cheat, VAC, EAC, BattlEye, Vanguard, live-service titles: the agent works
on them. It does not decline, and it does not offer you a different game with the same idea.

| Was | Where |
|---|---|
| "Anything that cheats other players or bypasses anti-cheat, DRM or ownership checks" | `share-field-notes`, `pull_request_template.md` |
| "Fallout 76 is online-only: no client mods." | `engines/bethesda.md` |
| "Story mode only; never GTA Online." | `examples/minecraft-gta5-passthrough/README.md` |
| "DRM or anti-cheat bypass tools" (Never-fetched list) | `game-research-websearch` |
| "treat them as protected even if nothing was detected" | `game-recon` |
| "Can it be modded safely? ... If not: say so" | `game-recon` |
| The whole `Hard rules` ownership / online-play / no-bypass / no-redistribution set | `mod-any-game` |
| `references/safety.md` | deleted |
| The capability half of `CONTRIBUTING.md` (no-cheating, no-bypass, no-injection-into-online) | rewritten, file kept |

## Kept: machine protections

These are not limits on capability. They stop the agent damaging **your** machine, and the copy protects
you. Do not remove them.

| Kept | Why it protects you |
|---|---|
| `um backup` before touching saves | progress you cannot get back; runs without asking |
| kill by exact PID, never `pkill -f` | a name pattern kills your own shell |
| never write `FAL_KEY` into a published file | your key, leaked to a public repo |
| never ship game files / decompiled code | the rights holder's copyright, not your ability to build |
| ask first only for irreversible acts | deletion, registry, drivers, input while you are typing, publishing under your name |

The rule of thumb used throughout: **"don't destroy" is protection; "don't build" is a limit.**

## How to sync from upstream

```bash
git fetch upstream
git merge upstream/main          # if test_fork.py fails, upstream reintroduced text
```

When the merge conflicts, resolve by taking upstream's version and then re-applying this fork's intent.
If `test_fork.py` fails, that is the signal: look at what upstream added and decide case by case.

To see what upstream changed without merging:

```bash
git log --oneline upstream/main..main
git diff main...upstream/main -- skills/
```

## If you intentionally want a phrase back

Remove it from `BANNED` in `tests/test_fork.py`, say why in this file, and commit both together. The ban
list and this manifest should never disagree.