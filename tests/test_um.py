"""Offline tests for the pieces that don't need a game, a GPU or a fal key.

    uv run --with pytest pytest -q
"""
import json
import shutil
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from um import backup, ce, fal, publish, scan, sprite, video  # noqa: E402


# --------------------------------------------------------------------------- scan

def make(root: Path, files: dict):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())


def engine_of(tmp_path, files):
    make(tmp_path, files)
    hits, _ = scan.detect(scan.Index(tmp_path))
    return hits[0][0], hits[0][3]


def test_unity_mono_and_version(tmp_path):
    key, det = engine_of(tmp_path, {
        "UnityPlayer.dll": b"MZ", "Game_Data/Managed/Assembly-CSharp.dll": b"MZ",
        "Game_Data/globalgamemanagers": b"\0" * 20 + b"2022.3.21f1\0" + b"\0" * 100,
        "Game_Data/app.info": "Studio\nCoolGame",
    })
    assert key == "unity-mono"
    assert det["version"] == "2022.3.21f1" and det["product"] == "CoolGame"


def test_unity_il2cpp(tmp_path):
    key, _ = engine_of(tmp_path, {"UnityPlayer.dll": b"MZ", "GameAssembly.dll": b"MZ",
                                  "Game_Data/il2cpp_data/Metadata/global-metadata.dat": b"\xaf\x1b\xb1\xfa"})
    assert key == "unity-il2cpp"


def test_unreal_version_from_exe(tmp_path):
    exe = b"MZ" + b"\0" * 5000 + "++UE5+Release-5.3".encode("utf-16-le") + b"\0" * 100
    key, det = engine_of(tmp_path, {"Proj/Binaries/Win64/Proj-Win64-Shipping.exe": exe, "Proj/Content/Paks/Proj-Windows.pak": b"x",
                                    "Proj/Content/Paks/Proj-Windows.utoc": b"x"})
    assert key == "unreal" and det["engine_version"] == "UE5+Release-5.3" and det["iostore"]


def test_godot_pck(tmp_path):
    key, det = engine_of(tmp_path, {"game.exe": b"MZ", "game.pck": b"GDPC" + struct.pack("<4I", 2, 4, 2, 1)})
    assert key == "godot" and det["version"].startswith("4.2.1")


def test_gamemaker_and_rpgmaker(tmp_path):
    assert engine_of(tmp_path / "a", {"data.win": b"FORM\0\0\0\0GEN8\0\0\0\0\0\x11"})[0] == "gamemaker"
    assert engine_of(tmp_path / "b", {"www/js/rpg_core.js": "//", "Game.exe": b"MZ"})[0] == "rpgmaker-mvmz"


def test_managed_pe(tmp_path):
    # minimal PE32 with a CLR header directory entry
    pe = bytearray(1024)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", pe, 0x84, 0x14C)
    opt = 0x80 + 24
    struct.pack_into("<H", pe, opt, 0x10B)
    struct.pack_into("<I", pe, opt + 96 + 14 * 8, 0x2000)
    p = tmp_path / "Game.exe"
    p.write_bytes(bytes(pe))
    assert scan.pe_info(p) == {"arch": "x86", "managed": True}


def test_vdf():
    d = scan._vdf('"AppState" { "appid" "105600" "name" "Terraria" "installdir" "Terraria" }')
    assert d["AppState"]["installdir"] == "Terraria"


@pytest.mark.parametrize("library_name,install_name,game_name", [
    ("SteamLibrary", "ExampleGame", "Example Game"),
    ("SteamLibrary", "ExampleGame", "Example Game\u2122"),
    ("SteamLibrary", "Jeu\u00e9", "Example Game"),
    ("Biblioth\u00e8que", "ExampleGame", "Example Game"),
])
def test_steam_games_utf8(tmp_path, monkeypatch, library_name, install_name, game_name):
    root = tmp_path / "Steam"
    library = tmp_path / library_name
    apps = library / "steamapps"
    game_path = apps / "common" / install_name
    game_path.mkdir(parents=True)
    (root / "steamapps").mkdir(parents=True)
    (root / "steamapps/libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" "' + library.as_posix() + '" } }', encoding="utf-8",
    )
    (apps / "appmanifest_123.acf").write_text(
        f'"AppState" {{ "appid" "123" "name" "{game_name}" "installdir" "{install_name}" }}',
        encoding="utf-8",
    )
    monkeypatch.setattr(scan, "steam_roots", lambda: [root])

    # Emulate a non-UTF-8 Windows default on every test platform, using real files.
    original_read_text = Path.read_text

    def read_text(path, encoding=None, errors=None, **kwargs):
        return original_read_text(path, encoding=encoding or "cp1252", errors=errors, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    assert scan.steam_games() == [{
        "store": "steam", "appid": "123", "name": game_name,
        "path": str(game_path), "workshop": None,
    }]


def test_steam_root_from_registry(tmp_path, monkeypatch):
    # Steam installed outside Program Files (e.g. C:\Steam): its libraryfolders.vdf, and every library in it, was never read
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    monkeypatch.setattr(scan, "steam_registry_root", lambda: root)
    assert root.resolve() in scan.steam_roots()

def test_known_game_longest_key_wins(tmp_path):
    # "grand theft auto v" is a substring of "grand theft auto v enhanced";
    # the more specific entry must win, not whichever lands first in the dict
    d = tmp_path / "Grand Theft Auto V Enhanced"
    d.mkdir()
    for i in range(6):
        (d / f"f{i}.txt").write_text("x")
    r = scan.scan(str(d))
    assert r["routes"][0]["route"] == scan.KNOWN["grand theft auto v enhanced"][0]


def test_record_encodes_and_tags_bt709(tmp_path, monkeypatch):
    # RGB frames -> yuv420p used the BT.601 matrix untagged; browsers read HD video as BT.709 and shift colours
    from um import win
    seen = {}
    monkeypatch.setattr(win, "is_wsl", lambda: False)   # under WSL, Recorder calls wslpath through the patched Popen

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
    monkeypatch.setattr(win, "ffmpeg_win", lambda *a, **k: "ffmpeg")
    monkeypatch.setattr(win, "encoder", lambda: "libx264")
    monkeypatch.setattr(win.subprocess, "Popen", FakePopen)
    win.Recorder(exe="Game.exe", out=str(tmp_path / "take"), audio=False).start()
    cmd = seen["cmd"]
    assert "out_color_matrix=bt709" in cmd[cmd.index("-vf") + 1]
    assert cmd[cmd.index("-colorspace") + 1] == "bt709" and cmd[cmd.index("-color_range") + 1] == "tv"


def test_auto_hdr_detection(monkeypatch):
    # Auto HDR on an HDR display washes out captures of SDR games; um warns from the registry setting
    from um import win
    prefs = {"DirectXUserGlobalSettings": "AutoHDREnable=0;SwapEffectUpgradeEnable=1;",
             r"E:\Games\Foo\Foo.exe": "AppStatus=1;AutoHDREnable=2097;",
             r"E:\Games\Bar\Bar.exe": "AppStatus=1;AutoHDREnable=2096;"}
    monkeypatch.setattr(win, "_gpu_prefs", lambda: prefs)
    assert win.auto_hdr_on("Foo.exe") and win.auto_hdr_on("foo")
    assert not win.auto_hdr_on("Bar.exe") and not win.auto_hdr_on("Other.exe") and not win.auto_hdr_on()
    prefs["DirectXUserGlobalSettings"] = "AutoHDREnable=1;"
    assert win.auto_hdr_on("Other.exe") and not win.auto_hdr_on("Bar.exe")


def test_slay_the_spire_2_is_not_sts1(tmp_path):
    # StS2 is Godot + C#; the StS1 entry (ModTheSpire, Java) must not match it
    d = tmp_path / "Slay the Spire 2"
    d.mkdir()
    for i in range(6):
        (d / f"f{i}.txt").write_text("x")
    route = scan.scan(str(d))["routes"][0]["route"]
    assert route == scan.KNOWN["slay the spire 2"][0] and "ModTheSpire" not in route


def test_online_only_matches_whole_names():
    # a substring test told agents to stop on single-player games: Rusty Lake ("rust"), Battlefield 1942, MW2 (2009)
    for offline in ["Rusty Lake Paradise", "Rusted Warfare", "Battlefield 1942", "Call of Duty: Modern Warfare 2 (2009)",
                    "Deadlock: Planetary Conquest", "The Final Station", "Trusty Rusty"]:
        assert scan.online_only(offline) is None, offline
    for online in ["Rust", "Counter-Strike 2", "Call of Duty®", "Tom Clancy's Rainbow Six® Siege", "PUBG: BATTLEGROUNDS",
                   "Overwatch® 2", "Deadlock", "NARAKA: BLADEPOINT"]:
        assert scan.online_only(online), online


def test_scan_warns_only_for_online_games(tmp_path):
    for name, warned in [("Rust", True), ("Rusty Lake Paradise", False)]:
        d = tmp_path / name
        d.mkdir()
        for i in range(6):
            (d / f"f{i}.txt").write_text("x")
        assert any("online competitive" in w for w in scan.scan(str(d))["warnings"]) == warned, name


def test_ffmpeg_download_is_checksummed(tmp_path, monkeypatch):
    import hashlib, io
    from um import win
    payload = b"PK fake ffmpeg zip"
    good = hashlib.sha256(payload).hexdigest()
    sums = f"{'0' * 64}  ffmpeg-other.zip\n{good}  ffmpeg-master-latest-win64-gpl.zip\n".encode()
    monkeypatch.delenv("UM_FFMPEG_SHA256", raising=False)
    monkeypatch.setattr(win.urllib.request, "urlopen", lambda url, timeout=None: io.BytesIO(sums))
    assert win.ffmpeg_sha256() == good
    monkeypatch.setattr(win.urllib.request, "urlretrieve", lambda url, dst: Path(dst).write_bytes(payload))
    z = tmp_path / "ffmpeg.zip"
    win.download_ffmpeg(z)
    assert z.read_bytes() == payload
    monkeypatch.setenv("UM_FFMPEG_SHA256", "ab" * 32)        # a pin wins over the published sum
    with pytest.raises(SystemExit):
        win.download_ffmpeg(z)
    assert not z.exists()                                      # a bad download is deleted, never extracted


# --------------------------------------------------------------------------- sprite

def sprite_on_white(w=64, h=48):
    im = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    for x in range(20, 40):
        for y in range(10, 30):
            im.putpixel((x, y), (200, 30, 30, 255))
    im.putpixel((30, 20), (255, 255, 255, 255))   # an interior white "eye" must survive
    return im


def test_cutout_keeps_interior_white():
    out = sprite.cutout(sprite_on_white())
    assert out.size == (20, 20)
    assert out.getpixel((10, 10))[3] == 255          # the interior white pixel is still opaque
    assert out.getpixel((0, 0))[:3] == (200, 30, 30)


def test_fit_and_hard_alpha():
    f = sprite.fit(sprite.cutout(sprite_on_white()), 10, 10, anchor="bottom")
    assert f.size == (10, 10) and f.getbbox()[3] == 10
    assert set(sprite.hard_alpha(f).getchannel("A").getdata()) <= {0, 255}


def test_sheet_slice_roundtrip():
    frames = [Image.new("RGBA", (8, 8), (i * 40, 0, 0, 255)) for i in range(5)]
    sh = sprite.sheet(frames, cols=3)
    assert sh.size == (24, 16)
    assert len(sprite.slice_sheet(sh, 8, 8)) == 5


@pytest.mark.parametrize("alpha", [1, 64, 128, 192, 254, 255])
@pytest.mark.parametrize("operation", ["fit", "sheet", "squash"])
def test_sprite_placement_preserves_rgba(alpha, operation):
    # Placing a frame on a transparent canvas must not apply its alpha twice.
    im = Image.new("RGBA", (8, 8), (200, 100, 50, alpha))
    if operation == "fit":
        out = sprite.fit(im, 8, 8)
    elif operation == "sheet":
        out = sprite.slice_sheet(sprite.sheet([im]), 8, 8)[0]
    else:
        out = sprite.simple_frames(im, n=1, kind="squash")[0]
    assert out.tobytes() == im.tobytes()


def test_team_mask():
    im = Image.new("RGBA", (4, 1), (0, 0, 0, 255))
    im.putpixel((0, 0), (20, 60, 240, 255))            # saturated blue -> player colour
    im.putpixel((1, 0), (200, 200, 200, 255))          # grey stays
    rgb, mask = sprite.team_mask(im)
    assert mask.getpixel((0, 0)) > 200 and mask.getpixel((1, 0)) == 0


def test_seamless_edges_match():
    import numpy as np
    ramp = np.tile(np.linspace(0, 255, 64)[None, :, None], (64, 1, 4)).astype(np.uint8)   # huge seam at the wrap
    ramp[..., 3] = 255
    out = np.asarray(sprite.seamless(Image.fromarray(ramp))).astype(int)
    before = np.abs(ramp[:, 0, :3].astype(int) - ramp[:, -1, :3].astype(int)).mean()
    after = np.abs(out[:, 0, :3] - out[:, -1, :3]).mean()
    assert before > 200 and after < 12


# --------------------------------------------------------------------------- fal (offline parts)

def test_kv_and_urls(tmp_path):
    assert fal._kv(["prompt=a cat", "num_images:=2", "flag:=true"]) == {"prompt": "a cat", "num_images": 2, "flag": True}
    res = {"images": [{"url": "https://v3.fal.media/a.png", "content_type": "image/png"}, {"url": "https://v3.fal.media/b.png"}],
           "mask_image": {"url": "https://v3.fal.media/m.png"}}
    assert [u for _, u, _ in fal._urls_in(res)] == ["https://v3.fal.media/a.png", "https://v3.fal.media/b.png", "https://v3.fal.media/m.png"]


def test_upload_uses_cdn_token_and_explains_big_failures(tmp_path, monkeypatch, capsys):
    # storage/upload/initiate?storage_type=gcs now answers 400 "Invalid storage type"; files over 8 MiB then failed silently
    calls = []
    monkeypatch.setattr(fal, "_req", lambda method, url, body=None, **k: calls.append(url) or {"token": "t", "token_type": "Bearer"})

    class Resp:
        def __init__(self, req):
            self.req = req

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"access_url": "https://v3.fal.media/files/x/a.png"}).encode()
    sent = []
    monkeypatch.setattr(fal.urllib.request, "urlopen", lambda req, timeout=None: sent.append(req) or Resp(req))
    f = tmp_path / "a.png"
    f.write_bytes(b"\x89PNG")
    assert fal.upload(f) == "https://v3.fal.media/files/x/a.png"
    assert "storage_type=fal-cdn-v3" in calls[0] and sent[0].full_url == fal.CDN + "/files/upload"
    assert sent[0].get_header("Authorization") == "Bearer t" and sent[0].get_header("X-fal-file-name") == "a.png"

    def fail(req, timeout=None):
        raise fal.urllib.error.URLError("boom")
    monkeypatch.setattr(fal.urllib.request, "urlopen", fail)
    assert fal.upload(f).startswith("data:image/png;base64,")              # small: inline fallback
    big = tmp_path / "big.mp4"
    big.write_bytes(b"\0" * ((8 << 20) + 1))
    with pytest.raises(SystemExit):
        fal.upload(big)
    assert "only covers files under 8 MiB" in capsys.readouterr().err  # big: says why instead of a bare exit 1


def test_failed_download_keeps_the_request_id(tmp_path, monkeypatch, capsys):
    # a finished (paid) job whose output URL 404s must not vanish: say which request to fetch again
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def urlopen(req, timeout=None):
        if req.full_url.endswith("big.mov"):
            raise fal.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return Resp()
    monkeypatch.setattr(fal.urllib.request, "urlopen", urlopen)
    res = {"video": {"url": "https://v3b.fal.media/files/x/big.mov"}, "thumb": {"url": "https://v3b.fal.media/files/x/t.png"},
           "_request_id": "req-123", "_endpoint": "fal-ai/some-model"}
    with pytest.raises(SystemExit):
        fal.download_outputs(res, tmp_path, "clip")
    err = capsys.readouterr().err
    assert "big.mov" in err and "um fal result fal-ai/some-model req-123" in err
    assert (tmp_path / "clip_thumb.png").read_bytes() == b"ok"   # the other outputs still saved


# --------------------------------------------------------------------------- publish

def test_publish_check(tmp_path, capsys):
    # fixtures assembled at runtime so this file doesn't trip the toolkit's own publish check
    fake_key = "FAL" + "_KEY=" + "abcdefghijklmnopqrstuvwxyz0123"
    ghidra_name = "FUN" + "_00401000"
    make(tmp_path / "mod", {"src/Mod.cs": f"int {ghidra_name}();\n// " + "Decompiled with ILSpy", "README.md": "My mod, built with dnSpy notes",
                            "config.txt": fake_key})
    make(tmp_path / "game", {"data/big.bin": b"x" * 4096})
    (tmp_path / "mod" / "copied.bin").write_bytes(b"x" * 4096)
    assert publish.check(str(tmp_path / "mod"), str(tmp_path / "game")) == 1
    out = capsys.readouterr().out
    assert "game file copied verbatim" in out and "FAL_KEY assignment" in out and "Ghidra auto-name" in out
    normalized = out.replace("\\", "/")
    assert "decompiler header x1 in src/Mod.cs" in normalized and "README.md" not in normalized.split("decompiler header")[-1].split("\n")[0]


@pytest.mark.parametrize("label,key", [
    # assembled at runtime so this file doesn't trip the toolkit's own publish check
    ("OpenAI key", "sk-" + "proj-" + "Ab3_dE-f" + "Gh1jK2lM3nO4pQ5rS6tU7vW8xY9z0" * 4),
    ("OpenAI key", "sk-" + "svcacct-" + "Ab3_dE-f" + "Gh1jK2lM3nO4pQ5rS6tU7vW8xY9z0" * 4),
    ("OpenAI key", "sk-" + "Gh1jK2lM3nO4pQ5rS6tU7vW8xY9z0" * 2),
    ("GitHub token", "github" + "_pat_" + "11ABCDEFG0123456789abc" + "_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5bC7dE9fG1hJ3kL5mN7pQ9rS1t"),
    ("GitHub token", "gh" + "p_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z"),
])
def test_secret_patterns_catch_current_key_formats(label, key):
    rx = dict(publish.SECRET_PATTERNS)[label]
    assert rx.search(f"key = {key}\n"), key


def test_secret_patterns_ignore_ordinary_text():
    text = "sk-learn-style-kebab-case-identifiers-are-not-keys and github_pat_ alone"
    assert not [label for label, rx in publish.SECRET_PATTERNS if rx.search(text)]


# --------------------------------------------------------------------------- video

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_compile_small_edl(tmp_path):
    for i, color in enumerate(["red", "blue"]):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:d=3:r=30", "-f", "lavfi", "-i", "sine=f=440:d=3",
                        "-shortest", str(tmp_path / f"c{i}.mp4")], check=True)
    edl = {"size": [640, 360], "fps": 30, "bpm": 120, "beat_lock": True, "transition": {"type": "cut"},
           "segments": [{"clip": "c0.mp4", "in": 0, "beats": 4, "hook": "Hello"},
                        {"clip": "c1.mp4", "in": 0.5, "beats": 4, "title": "A title", "credit": "@someone", "transition": {"type": "fade", "duration": 0.3}},
                        {"card": {"title": "The end"}, "dur": 1.5}]}
    (tmp_path / "edl.json").write_text(json.dumps(edl))
    video.compile_edl(tmp_path / "edl.json", str(tmp_path / "out.mp4"))
    info = video.probe(tmp_path / "out.mp4")
    assert abs(info["duration"] - (2 + 2 + 1.5)) < 0.15 and info["audio"]


# --------------------------------------------------------------------------- knowledge base

from um import kb  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def test_repo_knowledge_is_valid():
    root = REPO / "knowledge"
    for p, _, _ in kb.notes(root):
        fails, _ = kb.check_note(p, root)
        assert not fails, (p, fails)
    idx, rows = kb.build_index(root)
    assert (root / "INDEX.md").read_text(encoding="utf-8") == idx, "run `um kb index`"
    assert len(rows) >= 7


def test_kb_new_check_search(tmp_path):
    import shutil
    root = tmp_path / "knowledge"
    root.mkdir()
    shutil.copy(REPO / "knowledge" / "TEMPLATE.md", root / "TEMPLATE.md")
    p = kb.new_note(root, "Hades II", "A new boon god", agent="Codex (gpt-6)", route="loader-api")
    fails, _ = kb.check_note(p, root)
    assert any("unfilled template text" in f for f in fails)          # a fresh scaffold must not pass
    good = p.read_text(encoding="utf-8")
    good = good.replace("FILL IN: exact build", "1.0.1 (Steam)").replace("anti_cheat: FILL IN", "anti_cheat: none")
    good = good.replace("> Two to four sentences: what you built", "> Added a boon god via a Lua mod loader")
    good = good.replace("The most valuable section. Numbered; each one symptom → cause → fix.", "")
    good = good.replace("1. **Symptom.** What you saw. **Cause:** what it really was. **Fix:** what worked.",
                        "1. **Boons never offered.** **Cause:** pool cached at load. **Fix:** register before the run starts.")
    p.write_text(good, encoding="utf-8")
    fails, _ = kb.check_note(p, root)
    assert not fails, fails
    res = kb.search(root, ["boon"])
    assert res and res[0]["path"].endswith("a-new-boon-god.md")
    assert kb.search(root, ["boon"], route="native-hook") == []


def test_kb_search_matches_word_starts(tmp_path):
    root = tmp_path / "knowledge" / "games" / "x"
    root.mkdir(parents=True)
    for name, title in [("a.md", "Trust and frustum culling"), ("b.md", "A Rust server plugin"), ("c.md", "Rusty Lake puzzles"),
                        ("d.md", "Patching plugin.esp")]:
        (root / name).write_text(f"---\nkind: game\ntitle: {title}\ngame: X\n---\n# {title}\n", encoding="utf-8")
    found = {r["title"] for r in kb.search(tmp_path / "knowledge", ["rust"])}
    assert found == {"A Rust server plugin", "Rusty Lake puzzles"}
    assert [r["title"] for r in kb.search(tmp_path / "knowledge", [".esp"])] == ["Patching plugin.esp"]   # punctuation-led terms match anywhere


def test_kb_check_rejects_secrets_and_dumps(tmp_path):
    note = tmp_path / "n.md"
    code = "\n".join(f"int x{i} = {i};" for i in range(160))
    note.write_text("---\nkind: technique\ntitle: t\ntags: [x]\ndate: 2026-09-30\nagents: [a]\n---\n# t\n"
                    f"```c\n{code}\n```\n" + "FAL" + "_KEY=abcdefghijklmnopqrstuvwxyz0123\n")
    fails, _ = kb.check_note(note)
    assert any("code block" in f for f in fails) and any("FAL_KEY" in f for f in fails)


def test_kb_impossible_date_is_reported_not_raised(tmp_path):
    # YAML turns an unquoted YYYY-MM-DD into a date; a day that doesn't exist raises ValueError, not YAMLError
    root = tmp_path / "knowledge"
    (root / "techniques").mkdir(parents=True)
    note = root / "techniques" / "t.md"
    note.write_text("---\nkind: technique\ntitle: t\ntags: [x]\ndate: 2026-09-31\nagents: [a]\n---\n# t\n", encoding="utf-8")
    fails, _ = kb.check_note(note, root)
    assert any("front matter is not valid YAML" in f for f in fails), fails   # the date error's wording varies by Python
    kb.search(root, ["t"])                                             # one bad note must not break search or index
    kb.build_index(root)


@pytest.mark.parametrize("url", ["https://github.com/alice/universal-modder.git", "https://github.com/alice/universal-modder",
                                 "git@github.com:alice/universal-modder.git", "ssh://git@github.com/alice/universal-modder.git"])
def test_pr_head_from_fork(url):
    # gh looks a bare --head branch up in the base repo; a PR from a fork needs "<owner>:<branch>"
    assert kb.pr_head("kb/a-b", url) == "alice:kb/a-b"


def test_pr_head_same_repo():
    assert kb.pr_head("kb/a-b", None) == "kb/a-b"


# --------------------------------------------------------------------------- powershell

def test_ps_exe_falls_back_to_full_path(tmp_path, monkeypatch):
    # an agent's PATH often lacks System32\WindowsPowerShell\v1.0; bare "powershell" then raises WinError 2
    from um import common
    exe = tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(common, "is_wsl", lambda: False)
    monkeypatch.setattr(common.shutil, "which", lambda name: None)
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    assert common.ps_exe() == str(exe)
    monkeypatch.setattr(common.shutil, "which", lambda name: "/on/path/" + name)
    assert common.ps_exe() == "/on/path/powershell"


# --------------------------------------------------------------------------- backup

@pytest.fixture
def backup_same_second(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(backup.time, "strftime", lambda *args: "20261006-120000")


def test_backup_same_second_keeps_every_snapshot(tmp_path, backup_same_second):
    src = tmp_path / "src"
    paths = []
    for i in range(12):
        make(src, {"save.dat": f"version {i}"})
        paths.append(backup.create(str(src), name="t", note=f"take {i}"))

    assert len(set(paths)) == 12
    assert paths[0].name == "20261006-120000.zip"  # keep the existing filename format when available
    assert backup.snapshots("t") == paths         # latest selection still works after ten collisions
    for i, path in enumerate(paths):
        with backup.zipfile.ZipFile(path) as z:
            assert z.read("save.dat") == f"version {i}".encode()
        assert backup._manifest(path)["note"] == f"take {i}"
    assert backup.diff("t")["changed"] == []
    make(src, {"save.dat": "modified"})
    backup.restore("t", yes=True)
    assert (src / "save.dat").read_text() == "version 11"


@pytest.mark.parametrize("removed", [0, 1])
def test_backup_after_deleted_snapshot_is_still_latest(tmp_path, backup_same_second, removed):
    src = tmp_path / "src"
    make(src, {"save.dat": "old"})
    paths = [backup.create(str(src), name="t") for _ in range(3)]
    paths[removed].unlink()
    make(src, {"save.dat": "new"})
    newest = backup.create(str(src), name="t")

    assert newest > paths[-1]
    assert backup.snapshots("t")[-1] == newest
    assert backup.diff("t")["changed"] == []


def test_backup_concurrent_creates_keep_every_snapshot(tmp_path, backup_same_second):
    from concurrent.futures import ThreadPoolExecutor

    src = tmp_path / "src"
    make(src, {"save.dat": "world"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda i: backup.create(str(src), name="t", note=str(i)), range(8)))

    assert len(set(paths)) == 8
    assert backup.snapshots("t") == sorted(paths)
    for i, path in enumerate(paths):
        with backup.zipfile.ZipFile(path) as z:
            assert z.read("save.dat") == b"world"
        assert backup._manifest(path)["note"] == str(i)


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_backup_failed_create_keeps_previous_snapshot(tmp_path, backup_same_second, monkeypatch, error):
    src = tmp_path / "src"
    make(src, {"save.dat": "pristine"})
    first = backup.create(str(src), name="t")
    original = first.read_bytes()

    def fail_write(*args, **kwargs):
        raise error("interrupted backup")

    monkeypatch.setattr(backup.zipfile.ZipFile, "write", fail_write)
    with pytest.raises(error, match="interrupted backup"):
        backup.create(str(src), name="t")
    assert backup.snapshots("t") == [first]
    assert first.read_bytes() == original


def test_backup_repeated_restores_keep_undo_snapshots(tmp_path, backup_same_second):
    src = tmp_path / "src"
    make(src, {"save.dat": "pristine"})
    backup.create(str(src), name="t")
    for state in ("first take", "second take"):
        make(src, {"save.dat": state})
        backup.restore("t", yes=True)
        assert (src / "save.dat").read_text() == "pristine"

    undo = backup.snapshots("t-pre-restore")
    assert len(undo) == 2
    for path, state in zip(undo, ("first take", "second take")):
        with backup.zipfile.ZipFile(path) as z:
            assert z.read("save.dat") == state.encode()


def test_backup_handles_pre_1980_timestamps(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(backup, "_root", lambda name: (tmp_path / "snaps" / name).mkdir(parents=True, exist_ok=True)
                        or tmp_path / "snaps" / name)
    src = tmp_path / "src"
    make(src, {"old.txt": "from 1970", "new.txt": "fresh"})
    os.utime(src / "old.txt", (0, 0))
    zp = backup.create(str(src), name="t")
    assert set(backup._manifest(zp)["files"]) == {"old.txt", "new.txt"}


def test_backup_diff_and_restore_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "_root", lambda name: (tmp_path / "snaps" / name).mkdir(parents=True, exist_ok=True)
                        or tmp_path / "snaps" / name)
    src = tmp_path / "src"
    make(src, {"save.dat": "v1", "sub/cfg.ini": "a=1"})
    backup.create(str(src), name="t")
    (src / "save.dat").write_text("v2")
    (src / "sub" / "cfg.ini").unlink()
    make(src, {"extra.log": "new"})
    d = backup.diff("t", str(src))
    assert (d["changed"], d["removed"], d["added"]) == (["save.dat"], ["sub/cfg.ini"], ["extra.log"])
    with pytest.raises(SystemExit):  # no --yes: report only, touch nothing
        backup.restore("t", str(src))
    assert (src / "save.dat").read_text() == "v2"
    backup.restore("t", str(src), clean=True, yes=True)
    assert (src / "save.dat").read_text() == "v1"
    assert (src / "sub" / "cfg.ini").read_text() == "a=1"
    assert not (src / "extra.log").exists()
    assert backup.snapshots("t-pre-restore")  # the state before the restore was kept


# --------------------------------------------------------------------------- comfy

from um import comfy  # noqa: E402


@pytest.fixture
def fake_comfy():
    """A stand-in for ComfyUI's HTTP API: /system_stats, /models, /object_info, /prompt, /history, /view."""
    import io
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    state = {"prompts": [], "polls": 0, "models_route": True}
    png = io.BytesIO()
    im = Image.new("RGBA", (16, 16), (255, 255, 255, 255))      # a red square on a white background
    im.paste((200, 30, 30, 255), (4, 4, 12, 12))
    im.save(png, "PNG")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body: bytes, code=200, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/system_stats":
                self._send(json.dumps({"system": {"comfyui_version": "0.9.0", "pytorch_version": "2.9.0"},
                                       "devices": [{"name": "fake", "type": "cpu"}]}).encode())
            elif u.path == "/models/checkpoints" and state["models_route"]:
                self._send(json.dumps(["sd15.safetensors", "sdxl_base.safetensors"]).encode())
            elif u.path == "/object_info/CheckpointLoaderSimple":
                spec = ["COMBO", {"options": ["v3.safetensors"]}]
                self._send(json.dumps({"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": spec}}}}).encode())
            elif u.path == "/history/p1":
                state["polls"] += 1                                  # the first poll finds it still running
                done = {"p1": {"status": {"status_str": "success", "completed": True},
                               "outputs": {"9": {"images": [{"filename": "um_00001_.png", "subfolder": "", "type": "output"}]}}}}
                self._send(json.dumps(done if state["polls"] > 1 else {}).encode())
            elif u.path == "/view" and parse_qs(u.query).get("filename") == ["um_00001_.png"]:
                self._send(png.getvalue(), ctype="image/png")
            else:
                self._send(b"404: Not Found", 404, "text/plain")

        def do_POST(self):
            wf = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["prompt"]
            state["prompts"].append(wf)
            if wf.get("4", {}).get("inputs", {}).get("ckpt_name") == "missing.safetensors":
                err = {"error": {"message": "Prompt outputs failed validation", "details": ""},
                       "node_errors": {"4": {"class_type": "CheckpointLoaderSimple", "errors": [
                           {"message": "Value not in list", "details": "ckpt_name: 'missing.safetensors' not in [...]"}]}}}
                self._send(json.dumps(err).encode(), 400)
            else:
                self._send(json.dumps({"prompt_id": "p1", "number": 0, "node_errors": {}}).encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()


def test_comfy_image_sprite(fake_comfy, tmp_path, monkeypatch):
    from um import cli
    monkeypatch.setattr(comfy.time, "sleep", lambda s: None)
    out = tmp_path / "gen"
    cli.main(["comfy", "image", "a red potion", "--url", fake_comfy["url"], "--out", str(out), "--seed", "7", "--sprite"])
    wf = fake_comfy["prompts"][-1]
    assert wf["4"]["inputs"]["ckpt_name"] == "sd15.safetensors"               # the first checkpoint listed
    assert (wf["5"]["inputs"]["width"], wf["3"]["inputs"]["seed"]) == (512, 7)  # SD 1.5 size, the given seed
    assert "plain flat white background" in wf["6"]["inputs"]["text"]
    assert Image.open(out / "a_red_potion.png").size == (16, 16)
    cut = Image.open(out / "a_red_potion_cut.png")                           # cut out and trimmed locally
    assert cut.size == (8, 8) and cut.getpixel((0, 0)) == (200, 30, 30, 255)
    rec = json.loads((out / "comfy_manifest.jsonl").read_text().splitlines()[-1])
    assert rec["prompt_id"] == "p1" and rec["seed"] == 7 and rec["workflow"]["9"]["class_type"] == "SaveImage"


def test_comfy_run_set_and_errors(fake_comfy, tmp_path, monkeypatch):
    monkeypatch.setattr(comfy.time, "sleep", lambda s: None)
    wf = comfy.txt2img("x", "sd15.safetensors")
    wf["6"]["_meta"] = {"title": "Positive"}
    (tmp_path / "wf.json").write_text(json.dumps(wf))
    wf = comfy.apply_set(comfy.load_workflow(tmp_path / "wf.json"), ["Positive.text=a v1.5 sword=sharp", "3.seed:=42"])
    assert (wf["6"]["inputs"]["text"], wf["3"]["inputs"]["seed"]) == ("a v1.5 sword=sharp", 42)
    assert [Path(f).name for f in comfy.generate(fake_comfy["url"], wf, tmp_path / "o", "sword")] == ["sword.png"]
    (tmp_path / "ui.json").write_text(json.dumps({"nodes": [], "links": []}))
    with pytest.raises(SystemExit):
        comfy.load_workflow(tmp_path / "ui.json")                            # UI format: needs Export (API)
    with pytest.raises(SystemExit):
        comfy.queue(fake_comfy["url"], comfy.txt2img("x", "missing.safetensors"))   # validation error reported
    fake_comfy["models_route"] = False
    assert comfy.checkpoints(fake_comfy["url"]) == ["v3.safetensors"]        # older servers: /object_info
    assert comfy.status(fake_comfy["url"])["version"] == "0.9.0"


def test_skill_copies_match():
    # .agents/skills and .claude/skills are real copies of skills/ (Windows clones turn symlinks into text files)
    root = Path(__file__).resolve().parents[1]
    def tree(d):
        return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}
    src = tree(root / "skills")
    for copy in (".agents/skills", ".claude/skills"):
        assert not (root / copy).is_symlink(), f"{copy} must be a folder, not a symlink"
        assert tree(root / copy) == src, (f"{copy} differs from skills/: rm -rf .agents/skills .claude/skills && "
                                          "cp -r skills .agents/skills && cp -r skills .claude/skills")
    assert not any((root / d).exists() for d in (".gemini/skills", ".github/skills")), "agents read .agents/skills"


# --------------------------------------------------------------------------- cheat engine bridge

PING_RESULT = {"success": True, "version": "12.0.0", "timestamp": 0, "process_id": 4242,
               "message": "CE MCP Bridge v12.0.0 alive"}


class FakePipe:
    """Stands in for pywin32's win32file: a named pipe that hands the reply over in pieces.

    Partial reads are the whole point. A byte-mode pipe answers with whatever is in its buffer at
    the time, so a client that asks for 171 bytes can get 167 and must ask for the rest. Reading the
    4-byte header twice - once to learn the length, once as part of a "header + body" read - hangs
    forever waiting for 4 bytes that were never sent.
    """

    class error(Exception):
        pass

    GENERIC_READ, GENERIC_WRITE, OPEN_EXISTING = 1, 2, 3

    def __init__(self, reply: bytes, pieces: list[int]):
        self.reply, self.pieces, self.sent = reply, pieces, b""
        self.writes: list[bytes] = []
        self.closed = 0

    def CreateFile(self, name, access, share, sec, disp, flags, tmpl):
        return "handle"

    def WriteFile(self, h, data):
        self.writes.append(bytes(data))
        return (0, len(data))

    def ReadFile(self, h, n):
        want = self.pieces.pop(0) if self.pieces else n
        want = min(want, n)
        if not self.reply or want == 0:
            raise self.error(109, "ReadFile", "The pipe has been ended.")
        chunk, self.reply = self.reply[:want], self.reply[want:]
        self.sent += chunk
        return (0, chunk)

    def CloseHandle(self, h):
        self.closed += 1


def fake_relay_reply(result: dict) -> bytes:
    body = json.dumps({"jsonrpc": "2.0", "id": 7, "result": result}).encode()
    return struct.pack("<I", len(body)) + body


@pytest.fixture
def pipe_env(monkeypatch):
    monkeypatch.delenv("CE_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("CE_MCP_PIPE", raising=False)
    monkeypatch.setenv("CE_MCP_TIMEOUT", "5")


def test_ce_pipe_reads_the_header_once(pipe_env, monkeypatch):
    # the reply arrives as header(4) + 100 + 67 bytes; a client that re-reads the header blocks forever
    reply = fake_relay_reply(PING_RESULT)
    pipe = FakePipe(reply, pieces=[len(reply), 100, 67])
    monkeypatch.setitem(sys.modules, "win32file", pipe)
    monkeypatch.setitem(sys.modules, "pywintypes", pipe)
    assert ce._Pipe().exchange(ce.encode_request("ping")) == reply
    assert pipe.writes and pipe.writes[0][:4] == struct.pack("<I", len(pipe.writes[0]) - 4)
    assert json.loads(pipe.writes[0][4:])["method"] == "ping" and pipe.closed == 1


def test_ce_pipe_without_pywin32_says_what_to_install(pipe_env, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "win32file", None)      # `import win32file` -> ImportError
    with pytest.raises(SystemExit):
        ce._Pipe().exchange(b"")
    err = capsys.readouterr().err
    assert "pywin32" in err and "CE_MCP_TRANSPORT=tcp" in err


class FakeRelay(threading.Thread):
    """Stands in for ce_tcp_relay.py: same framing over TCP, answers whatever we send it."""

    def __init__(self):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.asked: list[str] = []
        self.start()

    def run(self):
        while True:
            try:
                s, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(s,), daemon=True).start()

    def handle(self, s):
        with s:
            head = self._read(s, 4)
            req = json.loads(self._read(s, struct.unpack("<I", head)[0]))
            self.asked.append(req["method"])
            body = json.dumps({"jsonrpc": "2.0", "id": req.get("id"),
                               "result": dict(PING_RESULT, method=req["method"])}).encode()
            s.sendall(struct.pack("<I", len(body)) + body)

    @staticmethod
    def _read(s, n):
        out = b""
        while len(out) < n:
            chunk = s.recv(n - len(out))
            if not chunk:
                raise ConnectionError("closed")
            out += chunk
        return out

    def close(self):
        self.srv.close()


@pytest.fixture
def tcp_env(pipe_env, monkeypatch):
    relay = FakeRelay()
    monkeypatch.setenv("CE_MCP_TRANSPORT", "tcp")
    monkeypatch.setenv("CE_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("CE_MCP_PORT", str(relay.port))
    yield relay
    relay.close()


def test_ce_tcp_round_trip(tcp_env):
    assert ce.send("ping") == dict(PING_RESULT, method="ping")
    assert ce.send("get_process_info") == dict(PING_RESULT, method="get_process_info")
    assert tcp_env.asked == ["ping", "get_process_info"]


def test_ce_tcp_no_relay_is_a_sentence_not_a_traceback(tcp_env, monkeypatch):
    monkeypatch.setenv("CE_MCP_PORT", "1")          # nothing listens there
    with pytest.raises(ce.BridgeError) as e:
        ce.send("ping")
    assert "um ce relay" in str(e.value)
    assert ce._Tcp("127.0.0.1", 1).reachable() is False


def test_ce_framing_round_trip_and_rejects_junk():
    frame = ce.encode_request("aob_scan", {"value": 100})
    assert frame[:4] == struct.pack("<I", len(frame) - 4)
    req = json.loads(frame[4:])
    assert req["jsonrpc"] == "2.0" and req["method"] == "aob_scan" and req["params"] == {"value": 100}
    assert ce.decode_frame(frame) == frame[4:]
    with pytest.raises(ce.BridgeError):
        ce.decode_frame(b"\x01\x02")
    with pytest.raises(ce.BridgeError):
        ce.decode_frame(struct.pack("<I", 4) + b"ab")          # truncated body
    with pytest.raises(ce.BridgeError):
        ce.decode_frame(struct.pack("<I", ce.MAX_FRAME + 1) + b"x" * 8)
    assert ce.unwrap({"result": {"ok": 1}}) == {"ok": 1}
    assert ce.unwrap({"error": "boom"})["success"] is False


def test_ce_with_timeout_gives_up_and_says_why(monkeypatch):
    monkeypatch.setenv("CE_MCP_TIMEOUT", "0.2")
    import time
    with pytest.raises(ce.BridgeError, match="no answer within"):
        ce._with_timeout(lambda: time.sleep(2), ce.timeout_seconds())


def test_ce_env_parsing(monkeypatch, capsys):
    for raw, want in [("pipe", "pipe"), ("named_pipe", "pipe"), ("np", "pipe"), ("socket", "tcp"),
                      (" TCP ", "tcp")]:
        monkeypatch.setenv("CE_MCP_TRANSPORT", raw)
        assert ce.transport() == want
    monkeypatch.setenv("CE_MCP_TRANSPORT", "carrier-pigeon")
    with pytest.raises(SystemExit):
        ce.transport()
    assert "carrier-pigeon" in capsys.readouterr().err
    monkeypatch.delenv("CE_MCP_TRANSPORT")
    monkeypatch.delenv("CE_MCP_TIMEOUT", raising=False)
    assert ce.timeout_seconds() == 30.0
    monkeypatch.setenv("CE_MCP_TIMEOUT", "0")
    assert ce.timeout_seconds() is None
    monkeypatch.setenv("CE_MCP_TIMEOUT", "nonsense")
    assert ce.timeout_seconds() == 30.0
    monkeypatch.delenv("CE_MCP_TIMEOUT")
    assert ce.tcp_port() == 9876
    monkeypatch.setenv("CE_MCP_PORT", "not-a-port")
    with pytest.raises(SystemExit):
        ce.tcp_port()
    monkeypatch.setenv("CE_MCP_PORT", "70000")
    with pytest.raises(SystemExit):
        ce.tcp_port()


def test_ce_tools_reads_the_bridge_source(tmp_path, monkeypatch):
    src = tmp_path / "MCP_Server" / "mcp_cheatengine.py"
    src.parent.mkdir(parents=True)
    src.write_text("@mcp.tool()\ndef aob_scan(pattern: str) -> str:\n    pass\n\n"
                   "@mcp.tool(name='read memory')\ndef read_memory() -> str:\n    pass\n\n"
                   "def helper():\n    pass\n", encoding="utf-8")
    monkeypatch.setenv("UM_CE_DIR", str(tmp_path))
    assert ce.tools() == ["aob_scan", "read_memory"]
    assert ce.tools("AOB") == ["aob_scan"]
    monkeypatch.setenv("UM_CE_DIR", str(tmp_path / "nope"))
    with pytest.raises(SystemExit) as e:
        ce.tools()


def windows(monkeypatch):
    """`um ce` is a Windows tool and CI runs on Linux, so tests simulate the platform instead of skipping."""
    monkeypatch.setattr(ce, "is_windows", lambda: True)
    monkeypatch.setattr(ce, "is_wsl", lambda: False)


def test_ce_doctor_walks_every_step(pipe_env, tmp_path, monkeypatch, capsys):
    windows(monkeypatch)
    script = tmp_path / "MCP_Server" / "mcp_cheatengine.py"
    script.parent.mkdir(parents=True)
    script.write_text("@mcp.tool()\ndef ping() -> str:\n    pass\n", encoding="utf-8")
    monkeypatch.setenv("UM_CE_DIR", str(tmp_path))
    monkeypatch.setattr(ce, "script_path", lambda: script)
    monkeypatch.setattr(ce, "requirements_path", lambda: script.parent / "requirements.txt")
    monkeypatch.setattr(ce, "ce_processes", lambda: [{"Id": 101, "ProcessName": "cheatengine-x86_64"}])
    monkeypatch.setattr(ce, "send", lambda method, params=None: dict(PING_RESULT))
    monkeypatch.setattr(ce.importlib.util, "find_spec", lambda name: "spec")
    monkeypatch.setattr(ce, "endpoint", lambda: type("E", (), {"reachable": staticmethod(lambda: True),
                                                               "exchange": staticmethod(lambda p: b"")})())
    report = ce.doctor()
    assert report["ok"] is True
    out = capsys.readouterr().out
    assert "everything the bridge needs" in out
    assert "attached process id 4242" in out

    monkeypatch.setattr(ce, "send", lambda method, params=None: (_ for _ in ()).throw(ce.BridgeError("boom")))
    with pytest.raises(SystemExit):
        ce.doctor()
    out = capsys.readouterr().out
    assert "boom" in out and "Query memory region routines" in out
    monkeypatch.setattr(ce, "script_path", lambda: tmp_path / "not-installed.py")
    with pytest.raises(SystemExit):
        ce.doctor()
    assert "um ce install" in capsys.readouterr().out


def test_ce_doctor_reports_unattached_cheat_engine(pipe_env, tmp_path, monkeypatch, capsys):
    windows(monkeypatch)
    script = tmp_path / "MCP_Server" / "mcp_cheatengine.py"
    script.parent.mkdir(parents=True)
    script.write_text("x", encoding="utf-8")
    monkeypatch.setenv("UM_CE_DIR", str(tmp_path))
    monkeypatch.setattr(ce, "script_path", lambda: script)
    monkeypatch.setattr(ce, "ce_processes", lambda: [{"Id": 7, "ProcessName": "cheatengine-i386"}])
    monkeypatch.setattr(ce, "endpoint", lambda: type("E", (), {"reachable": staticmethod(lambda: True)})())
    monkeypatch.setattr(ce, "send", lambda method, params=None: dict(PING_RESULT, process_id=0))
    with pytest.raises(SystemExit):
        ce.doctor()
    assert "attached to nothing" in capsys.readouterr().out


def test_ce_clone_pins_one_commit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("UM_CE_DIR", str(tmp_path / "bridge"))
    monkeypatch.setattr(ce, "bridge_dir", lambda: tmp_path / "bridge")
    monkeypatch.setattr(ce, "run", lambda cmd: calls.append(cmd) or type("R", (), {"stdout": ""})())
    monkeypatch.setattr(ce.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or
                        type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    ce.clone(force=True)
    fetch = [c for c in calls if "fetch" in c][0]
    assert fetch[:2] == ["git", "-C"] and "--depth" in fetch and "1" in fetch
    assert ce.BRIDGE_REF in fetch
    assert any("remote" in c and "add" in c for c in calls)
    (tmp_path / "bridge" / ".git").mkdir(parents=True, exist_ok=True)      # what a real clone leaves behind
    ce.clone()                                   # already cloned: no second fetch
    assert len([c for c in calls if "fetch" in c]) == 1
    monkeypatch.setenv("UM_CE_REF", "main")
    ce.clone(force=True)
    assert [c for c in calls if "fetch" in c][1][-1] == "main"


def test_ce_install_refuses_off_windows(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ce, "is_windows", lambda: False)
    monkeypatch.setattr(ce, "is_wsl", lambda: False)
    with pytest.raises(SystemExit):
        ce.install(type("A", (), {"ref": None, "force": False, "write": False})())
    assert "um ce relay" in capsys.readouterr().err


def test_ce_install_pins_and_points_at_the_lua_script(tmp_path, monkeypatch, capsys):
    windows(monkeypatch)
    d = tmp_path / "bridge"
    (d / "MCP_Server").mkdir(parents=True)
    for name in ("ce_mcp_bridge.lua", "mcp_cheatengine.py", "requirements.txt", "ce_tcp_relay.py"):
        (d / "MCP_Server" / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(ce, "bridge_dir", lambda: d)
    monkeypatch.setattr(ce, "lua_path", lambda: d / "MCP_Server" / "ce_mcp_bridge.lua")
    monkeypatch.setattr(ce, "script_path", lambda: d / "MCP_Server" / "mcp_cheatengine.py")
    monkeypatch.setattr(ce, "requirements_path", lambda: d / "MCP_Server" / "requirements.txt")
    monkeypatch.setattr(ce, "clone", lambda ref=None, force=False: d)
    monkeypatch.setattr(ce, "run", lambda cmd: type("R", (), {"stdout": "Requirement already satisfied"})())
    monkeypatch.setattr(ce.subprocess, "run", lambda cmd, **kw: type("R", (), {"stdout": ce.BRIDGE_REF})())
    monkeypatch.setattr(ce, "write_configs", lambda: [])
    ce.install(type("A", (), {"ref": None, "force": False, "write": False})())
    out = capsys.readouterr().out
    assert ce.BRIDGE_REF[:12] in out and "dofile([[" in out and "um ce doctor" in out


def test_ce_config_writes_every_agent_config(tmp_path, monkeypatch):
    import shutil
    repo = tmp_path / "repo"
    (repo / ".cursor").mkdir(parents=True)
    (repo / ".codex").mkdir(parents=True)
    (repo / ".vscode").mkdir(parents=True)
    for rel in (".mcp.json", "mcp.json", ".cursor/mcp.json", ".vscode/mcp.json", "opencode.json",
                "gemini-extension.json"):
        shutil.copy(ce.REPO / rel, repo / rel)
    (repo / ".codex" / "config.toml").write_text('command = "python"\n[mcp_servers.fal]\nurl = "x"\n',
                                                 encoding="utf-8")
    (repo / "MCP_Server").mkdir(parents=True)
    (repo / "MCP_Server" / "mcp_cheatengine.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ce, "REPO", repo)
    monkeypatch.setattr(ce, "script_path", lambda: repo / "MCP_Server" / "mcp_cheatengine.py")
    monkeypatch.setattr(ce, "data_dir", lambda: tmp_path / "data")

    written = ce.write_configs()
    assert set(written) == {r for r, _, _ in ce.CONFIG_FILES}
    for rel, key, style in ce.CONFIG_FILES:
        text = (repo / rel).read_text(encoding="utf-8")
        if style == "codex":
            assert "[mcp_servers.cheatengine]" in text and "mcp_cheatengine.py" in text
            assert "[mcp_servers.fal]" in text                      # the other server survived
        else:
            entry = json.loads(text)[key]["cheatengine"]
            assert [str(p) for p in entry.get("args", entry.get("command", []))][-1].endswith("mcp_cheatengine.py")
            assert "fal" in text                                     # fal's entry is still there
    codex = (repo / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert codex.index("[mcp_servers.fal]") < codex.index("[mcp_servers.cheatengine]")

    before = {rel: (repo / rel).read_bytes() for rel in written}
    assert ce.write_configs() == []                                 # idempotent: nothing to do
    assert {rel: (repo / rel).read_bytes() for rel in written} == before
    assert len(list((tmp_path / "data" / "config-backups").glob("*.bak"))) == len(ce.CONFIG_FILES)


def test_ce_config_updates_a_stale_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"fal": {"url": "x"},
                                                                "cheatengine": {"args": ["/old/path.py"]}}}),
                                    encoding="utf-8")
    (repo / "MCP_Server").mkdir()
    (repo / "MCP_Server" / "mcp_cheatengine.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ce, "REPO", repo)
    monkeypatch.setattr(ce, "script_path", lambda: repo / "MCP_Server" / "mcp_cheatengine.py")
    monkeypatch.setattr(ce, "data_dir", lambda: tmp_path / "data")
    assert ce.write_configs() == [".mcp.json"]
    entry = json.loads((repo / ".mcp.json").read_text())["mcpServers"]["cheatengine"]
    assert entry["args"] == [str(repo / "MCP_Server" / "mcp_cheatengine.py")]
    assert "fal" in (repo / ".mcp.json").read_text()


def test_ce_config_lists_the_snippets(pipe_env, tmp_path, monkeypatch, capsys):
    (tmp_path / "MCP_Server").mkdir(parents=True)
    (tmp_path / "MCP_Server" / "mcp_cheatengine.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ce, "script_path", lambda: tmp_path / "MCP_Server" / "mcp_cheatengine.py")
    assert ce.config() == [rel for rel, _, _ in ce.CONFIG_FILES]
    out = capsys.readouterr().out
    for rel, _, _ in ce.CONFIG_FILES:
        assert rel in out
    assert "um ce install --write" in out


def test_ce_relay_needs_the_bridge(pipe_env, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ce, "is_windows", lambda: True)
    monkeypatch.setattr(ce, "relay_path", lambda: tmp_path / "ce_tcp_relay.py")
    with pytest.raises(SystemExit):
        ce.relay()
    assert "um ce install" in capsys.readouterr().err


def test_ce_cli_ping_uses_the_bridge(pipe_env, monkeypatch, capsys):
    from um import cli
    asked = []
    monkeypatch.setattr(ce, "send", lambda method, params=None: asked.append(method) or PING_RESULT)
    cli.main(["ce", "ping"])
    assert asked == ["ping"]
    assert json.loads(capsys.readouterr().out) == PING_RESULT
    monkeypatch.setattr(ce, "send", lambda m, p=None: (_ for _ in ()).throw(ce.BridgeError("nope")))
    with pytest.raises(SystemExit):
        cli.main(["ce", "ping"])
    assert "nope" in capsys.readouterr().err


# --------------------------------------------------------------------------- hooks

@pytest.mark.skipif(not shutil.which("cygpath"), reason="Git Bash / MSYS only")
def test_path_hook_writes_a_posix_root(tmp_path):
    # Claude Code passes ${CLAUDE_PLUGIN_ROOT} as C:/...; written as is, bash splits PATH at the drive colon
    import os
    root = tmp_path / "um root"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "um").write_text("#!/bin/sh\n")
    env_file = tmp_path / "env.sh"
    bash = str(Path(shutil.which("cygpath")).with_name("bash.exe"))
    hook = Path(__file__).resolve().parents[1] / "hooks" / "add-to-path.sh"
    subprocess.run([bash, str(hook), root.as_posix()], env={**os.environ, "CLAUDE_ENV_FILE": str(env_file)}, check=True)
    value = env_file.read_text().split('"')[1]
    prefix = value[:value.index("/bin:$PATH")]
    assert prefix.startswith("/") and ":" not in prefix, value
