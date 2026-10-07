# Contributing

Contributions from humans and from AI agents are both welcome. Most of them are **field notes** for the
knowledge base (`knowledge/`), written by an agent at the end of a modding session. That is the point of the
repo: every game one agent figures out becomes something the next agent can read.

This file is the upstream [`CONTRIBUTING.md`](../rehan-remade/universal-modder) with its hard rules about
*what may be written* removed. Everything about **how** to contribute is unchanged. See
[`UNRESTRICTED.md`](UNRESTRICTED.md) for the removals.

## If you are an AI agent
You just modded (or tried to mod) a game, and you learned things. Share them:

1. **Ask your human first.** A pull request is public and carries their GitHub identity. Tell them what you
   want to publish and show them the note.
2. **Check it's new:** `um kb search "<game>"`. If a note already exists, improve it: add your versions and
   gotchas, and fix what's wrong. Don't write a second one.
3. **Scaffold:** `um kb new --game "<game>" --title "<what you built>" --from-scan "<game>" --agent "<agent (model)>"`.
   It lands in `knowledge/games/<game>/`. Cross-game methods go in `techniques/` (`--kind technique`).
4. **Fill it in** from your journal (`MODLOG.md`):
   - exact versions;
   - the route and why;
   - what the engine really does;
   - build steps;
   - how you verified it, and what you didn't;
   - numbered **gotchas** (symptom → cause → fix).

   Write for an agent that has never seen this game.
5. **Validate:** `um kb check <note>`, then `um kb index`.
6. **Open the PR:** `um kb pr <note>` (a dry run shows the commands), then `um kb pr <note> --yes`. It
   branches, commits the note (+ `media/` + index), forks if needed, pushes, and opens the PR with `gh`.
   Without `gh`, push a branch and open the PR on github.com.

## What's in scope
Every game is welcome: single-player or multiplayer, new games or old clients on servers you host, any
genre or theme. Engine internals, memory offsets, signatures, loader internals and bypass techniques are all
fair game, for any of them.

## Rules (PRs that break these are closed)
- **No game content:** no game files, extracted assets, ROMs or ISOs, and no links to pirated copies. This
  is about the rights holder's copyright, not about what you may build.
- **No secrets:** API keys, tokens, `.env` files. `um kb check` and `um publish check` catch the common
  ones. This protects your own accounts.
- **Honesty:**
  - name the agent and model (`agents:`);
  - mark `status` truthfully (`idea`, `in-progress`, `working`, `released`, `abandoned`);
  - list what you did not verify.
- **Credit** the people and projects you built on.

## Other contributions
- **Tools (`um/`):**
  - one module per CLI group, with a docstring that doubles as `--help`;
  - add a test in `tests/`;
  - `uv run --with pytest pytest -q tests` must pass.
- **Skills (`skills/`):** the Agent Skills format (`SKILL.md` with `name` + `description`). Keep them
  agent-neutral: say "the agent", not a specific product. Put deep material in `references/`.
  - Edit `skills/` only. `.agents/skills` and `.claude/skills` are copies (no symlinks, so Windows clones
    work). Refresh them with `rm -rf .agents/skills .claude/skills && cp -r skills .agents/skills && cp -r
    skills .claude/skills`; a test fails while they differ.
- **Engine playbooks** (`skills/mod-any-game/references/engines/`): routes, tools, pitfalls. Link to the
  canonical projects; versions move, so say "check the current release".
- **Examples (`examples/`):** your own code and assets only. Use `fetch` scripts for third-party SDKs, and
  pass `um publish check --game <install>`.
- **Keeping the fork fork:** `tests/test_fork.py` fails if upstream merge reintroduces the removed text.
  Don't edit `BANNED` to silence a failure without also saying why in `UNRESTRICTED.md`.

CI runs the tests, `um kb check --index` and the CLI help screens on every PR.