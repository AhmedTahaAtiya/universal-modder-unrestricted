"""Fork integrity: the guardrail text must not come back when upstream merges in.

This file is the fork's actual guarantee, not a convention. It fails CI if an
upstream merge, a bad cherry-pick, or a new agent run reintroduces the text that
was deliberately removed.

Two layers:
  1. ban list   - phrases that must not appear anywhere in agent-facing docs.
  2. presence   - files the fork deleted that must stay deleted.

Update the list in UNRESTRICTED.md alongside any intentional change.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Directories the agent actually reads at runtime. knowledge/ holds upstream field
# notes kept verbatim as history, so it is checked separately and not banned.
AGENT_FACING = ("skills", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "README.md", ".github")

BANNED = [
    # online / multiplayer prohibitions
    "cheats other players",
    "cheat other players",
    "bypasses anti-cheat",
    "bypass anti-cheat",
    "anti-cheat bypass",
    "never GTA Online",
    "no client mods",
    "treat them as protected",
    "only acceptable way; never online",
    "never gameplay-affecting mods online",
    "Never ship client hacks",
    "single player and lobbies",
    # ownership gates
    "only mod games the user owns",
    "Only mod games the user owns",
    "never on anti-cheat-protected games",
    # the decision gate that used to refuse work
    "Can it be modded safely?",
    # removed files
    "references/safety.md",
    "safety.md",
]

MUST_NOT_EXIST = [
    "skills/mod-any-game/references/safety.md",
    ".agents/skills/mod-any-game/references/safety.md",
    ".claude/skills/mod-any-game/references/safety.md",
]


def _files():
    for rel in AGENT_FACING:
        p = ROOT / rel
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in p.rglob("*.md"):
                yield f


def test_banned_guardrail_text_absent():
    hits = []
    for f in _files():
        text = f.read_text(encoding="utf-8", errors="replace")
        low = text.lower()
        for phrase in BANNED:
            if phrase.lower() in low:
                line = next(
                    (i for i, l in enumerate(text.splitlines(), 1) if phrase.lower() in l.lower()),
                    0,
                )
                hits.append(f"{f.relative_to(ROOT)}:{line} contains {phrase!r}")
    assert not hits, "guardrail text came back:\n  " + "\n  ".join(hits)


@pytest.mark.parametrize("rel", MUST_NOT_EXIST)
def test_deleted_files_stay_deleted(rel):
    assert not (ROOT / rel).exists(), f"{rel} was deleted in this fork and came back"


def test_unrestricted_manifest_exists():
    """The manifest documents what was removed; it is what makes the ban list auditable."""
    assert (ROOT / "UNRESTRICTED.md").is_file(), "UNRESTRICTED.md missing: the removal manifest must ship"