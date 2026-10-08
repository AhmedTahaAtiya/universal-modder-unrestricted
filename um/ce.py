"""Cheat Engine as the agent's eyes on live process memory (Windows; the agent itself may run anywhere).

    um ce install [--write] [--ref SHA]      # clone the pinned bridge + install its Python deps
    um ce doctor [--json]                    # CE up? Lua bridge loaded? pipe/relay reachable? ping? what to fix
    um ce ping [--json]                      # one JSON-RPC round trip: bridge version + attached process id
    um ce tools [filter]                     # the MCP tool names, read out of the bridge's own source
    um ce relay [--host H] [--port P]        # TCP->pipe relay when the MCP server runs on WSL/Linux/elsewhere
    um ce config [--write]                   # the `cheatengine` MCP entry for this repo's agent configs

Why this exists: `um scan` says a game is native, the reverse-engineering skill says "find what writes to this
address", and this is the thing that finds it. cheatengine-mcp-bridge (MIT, (c) 2025 miscusi-peek and
contributors) puts ~180 MCP tools on top of Cheat Engine: memory reads, pointer chains, AOB scans,
disassembly, RTTI class names, hardware breakpoints and DBVM ring -1 watches.

It stays a separate project on purpose: we pin a commit SHA, so `um ce install` is reproducible, upstream
fixes flow in with `um ce install --force`, and nothing here needs vendoring. `um ce` is the installer,
the doctor and the config writer around it.

Env: UM_CE_DIR (clone dir, default ~/.universal-modder/ce-bridge), UM_CE_REF (ref/SHA to install),
CE_MCP_PIPE, CE_MCP_TRANSPORT (pipe|tcp), CE_MCP_HOST, CE_MCP_PORT, CE_MCP_TIMEOUT (seconds, default 30).

Two things that bite, both hard:
- Cheat Engine -> Settings -> Extra -> **disable "Query memory region routines"**. With it on, memory scans
  over protected pages take the machine down (CLOCK_WATCHDOG_TIMEOUT BSOD), and DBVM makes it more likely.
- Leave CE_MCP_ALLOW_SHELL unset. It enables run_command/shell_execute: arbitrary code execution inside
  whatever Cheat Engine is attached to.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from um.common import data_dir, die, emit, is_windows, is_wsl, run

REPO = Path(__file__).resolve().parents[1]
BRIDGE_REPO = "https://github.com/miscusi-peek/cheatengine-mcp-bridge.git"
BRIDGE_HOME = "https://github.com/miscusi-peek/cheatengine-mcp-bridge"
BRIDGE_REF = "6bd7ce90479250a9b4b75e7944df9c105ecb2572"   # v12.0.0 main, the copy this module was written against
BRIDGE_CREDIT = "MIT, (c) 2025 miscusi-peek and contributors"
DEFAULT_PIPE = r"\\.\pipe\CE_MCP_Bridge_v99"
DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 9876
MAX_FRAME = 32 * 1024 * 1024
TOOLS_RX = re.compile(r"@mcp\.tool\([^)]*\)\s*\ndef\s+(\w+)")
TRANSPORT_ALIASES = {"named_pipe": "pipe", "named-pipe": "pipe", "np": "pipe", "socket": "tcp"}

# Where an agent reads its MCP servers from, and the shape each one wants. Paths are relative to the repo
# root; only files that exist are touched, and `um ce install` says so if none of them do.
CONFIG_FILES = [
    (".mcp.json", "mcpServers", "stdio"),
    ("mcp.json", "mcpServers", "stdio"),
    (".cursor/mcp.json", "mcpServers", "plain"),
    (".vscode/mcp.json", "servers", "stdio"),
    ("opencode.json", "mcp", "opencode"),
    ("gemini-extension.json", "mcpServers", "plain"),
    (".codex/config.toml", None, "codex"),
]


class BridgeError(Exception):
    """The bridge did not answer, or answered with something we cannot use."""


# --------------------------------------------------------------------------- paths and env


def bridge_dir() -> Path:
    """Where the pinned bridge lives: $UM_CE_DIR, else ~/.universal-modder/ce-bridge."""
    return Path(os.environ.get("UM_CE_DIR") or (data_dir() / "ce-bridge")).expanduser()


def lua_path() -> Path:
    return bridge_dir() / "MCP_Server" / "ce_mcp_bridge.lua"


def script_path() -> Path:
    return bridge_dir() / "MCP_Server" / "mcp_cheatengine.py"


def relay_path() -> Path:
    return bridge_dir() / "MCP_Server" / "ce_tcp_relay.py"


def requirements_path() -> Path:
    """pywin32 only helps on the machine that owns the pipe; everywhere else the TCP set is the whole story."""
    name = "requirements.txt" if is_windows() else "requirements-tcp.txt"
    return bridge_dir() / "MCP_Server" / name


def python_cmd() -> str:
    """The interpreter that has the bridge's deps: the one running `um`, when its path is absolute."""
    return sys.executable if os.path.isabs(sys.executable) else "python"


def timeout_seconds() -> float | None:
    """CE_MCP_TIMEOUT: seconds per call, <=0 disables it. Cheat Engine answers on its own GUI thread,
    so a busy CE looks exactly like a dead one - a timeout is the only way to tell them apart."""
    raw = os.environ.get("CE_MCP_TIMEOUT")
    if raw is None or not str(raw).strip():
        return 30.0
    try:
        v = float(raw)
    except ValueError:
        return 30.0
    return None if v <= 0 else v


def transport() -> str:
    t = (os.environ.get("CE_MCP_TRANSPORT") or "pipe").strip().lower()
    t = TRANSPORT_ALIASES.get(t, t)
    if t not in ("pipe", "tcp"):
        die("CE_MCP_TRANSPORT must be 'pipe' or 'tcp' (got "
            f"{os.environ.get('CE_MCP_TRANSPORT')!r}); TCP is for a MCP server that cannot open the Windows pipe")
    return t


def pipe_name() -> str:
    return os.environ.get("CE_MCP_PIPE") or DEFAULT_PIPE


def tcp_host() -> str:
    return (os.environ.get("CE_MCP_HOST") or DEFAULT_TCP_HOST).strip() or DEFAULT_TCP_HOST


def tcp_port() -> int:
    raw = (os.environ.get("CE_MCP_PORT") or "").strip()
    if not raw:
        return DEFAULT_TCP_PORT
    try:
        port = int(raw)
    except ValueError:
        die(f"CE_MCP_PORT must be a TCP port (got {raw!r})")
        return DEFAULT_TCP_PORT
    if not 1 <= port <= 65535:
        die(f"CE_MCP_PORT must be between 1 and 65535 (got {port})")
    return port


# --------------------------------------------------------------------------- wire protocol
# The bridge speaks length-prefixed JSON-RPC over a named pipe (or the TCP relay): 4-byte little-endian
# length, then a UTF-8 JSON body. Same framing on the way back.


def encode_request(method: str, params: dict | None = None) -> bytes:
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {},
                       "id": int(time.time() * 1000)}).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def decode_frame(raw: bytes) -> bytes:
    """(length, body) of one response frame, checked against the bridge's own 32 MB cap."""
    if len(raw) < 4:
        raise BridgeError(f"short frame: {len(raw)} bytes, expected a 4-byte length header")
    n = struct.unpack("<I", raw[:4])[0]
    if n > MAX_FRAME:
        raise BridgeError(f"frame claims {n} bytes, over the {MAX_FRAME // (1 << 20)} MB cap")
    if len(raw) - 4 < n:
        raise BridgeError(f"truncated frame: {len(raw) - 4} of {n} bytes")
    return raw[4:4 + n]


def unwrap(resp: dict) -> dict:
    """JSON-RPC envelope -> the tool's own result dict, which is what every handler returns."""
    if not isinstance(resp, dict):
        raise BridgeError(f"the bridge answered with {type(resp).__name__}, not a JSON-RPC object")
    if "error" in resp:
        return {"success": False, "error": str(resp["error"])}
    return resp.get("result", resp)


class _Pipe:
    """Named pipe transport, exactly as mcp_cheatengine.py opens it."""

    def __init__(self, name: str = DEFAULT_PIPE, timeout: float | None = None):
        self.name, self.timeout = name, timeout

    def exchange(self, payload: bytes) -> bytes:
        try:
            import win32file
            import pywintypes
        except ImportError:
            die("the named pipe transport needs pywin32 on Windows: "
                f"{python_cmd()} -m pip install pywin32, or run the MCP server with CE_MCP_TRANSPORT=tcp "
                "against `um ce relay` on the Windows machine")
        try:
            h = win32file.CreateFile(self.name, win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None,
                                     win32file.OPEN_EXISTING, 0, None)
        except pywintypes.error as e:
            raise BridgeError(f"{self.name} is not listening: {e}. Load the bridge in Cheat Engine "
                              f"(dofile([[{lua_path()}]])) - `um ce doctor` walks the whole chain") from e
        try:
            win32file.WriteFile(h, payload)
            return _read_pipe(win32file, pywintypes, h)
        except pywintypes.error as e:
            raise BridgeError(f"pipe conversation failed: {e}") from e
        finally:
            try:
                win32file.CloseHandle(h)
            except Exception:
                pass

    def reachable(self) -> bool:
        try:
            return os.path.exists(self.name)
        except OSError:
            return False


def _read_pipe(win32file, pywintypes, h) -> bytes:
    """One whole response frame (header + body). The header is read once: byte pipes answer with whatever
    is in the buffer at the time, so the loop must ask for the rest, never re-read the header."""
    def exact(n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = win32file.ReadFile(h, n - len(out))[1]
            if not chunk:
                raise BridgeError("the bridge closed the pipe mid-answer")
            out += chunk
        return out

    head = exact(4)
    n = struct.unpack("<I", head)[0]
    if n > MAX_FRAME:
        raise BridgeError(f"the bridge wants to send {n} bytes, over the {MAX_FRAME // (1 << 20)} MB cap")
    return head + exact(n)


class _Tcp:
    """TCP relay transport (ce_tcp_relay.py): same framing, so the MCP server can live on WSL or Linux."""

    def __init__(self, host: str = DEFAULT_TCP_HOST, port: int = DEFAULT_TCP_PORT, timeout: float | None = None):
        self.host, self.port, self.timeout = host, port, timeout

    def _connect(self):
        return socket.create_connection((self.host, self.port), timeout=self.timeout)

    def exchange(self, payload: bytes) -> bytes:
        try:
            with self._connect() as s:
                s.settimeout(self.timeout)
                s.sendall(payload)
                head = self._read(s, 4)
                n = struct.unpack("<I", head)[0]
                if n > MAX_FRAME:
                    raise BridgeError(f"the relay wants to send {n} bytes, over the {MAX_FRAME // (1 << 20)} MB cap")
                return head + self._read(s, n)
        except OSError as e:
            raise BridgeError(f"no relay at {self.host}:{self.port}: {e}. Start it on the Windows machine with "
                              "`um ce relay` (CE still runs there)") from e

    @staticmethod
    def _read(s, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = s.recv(n - len(out))
            if not chunk:
                raise BridgeError(f"the relay closed the connection with {n - len(out)} bytes still to come")
            out += chunk
        return out

    def reachable(self) -> bool:
        try:
            with self._connect():
                return True
        except OSError:
            return False


def endpoint():
    """The client for the transport this environment asks for."""
    return _Tcp(tcp_host(), tcp_port(), timeout_seconds()) if transport() == "tcp" else _Pipe(pipe_name(), timeout_seconds())


def _exchange(payload: bytes) -> bytes:
    return endpoint().exchange(payload)


def _with_timeout(fn, timeout: float | None):
    """A named pipe read has no timeout, and a blocked CE GUI thread blocks it forever."""
    if not timeout:
        return fn()
    box: dict = {}

    def work():
        try:
            box["out"] = fn()
        except BaseException as e:      # re-raised on the calling thread below
            box["err"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise BridgeError(f"no answer within {timeout:g}s (CE_MCP_TIMEOUT): Cheat Engine is busy, its GUI "
                          "thread is blocked, or the bridge stopped. Reload the Lua script and try again")
    if "err" in box:
        raise box["err"]
    return box["out"]


def send(method: str, params: dict | None = None) -> dict:
    """One JSON-RPC round trip to the bridge. Raises BridgeError with a sentence that says what to do."""
    payload = encode_request(method, params)
    work = (lambda: _with_timeout(lambda: _exchange(payload), timeout_seconds())) if transport() == "pipe" \
        else (lambda: _exchange(payload))
    try:
        return unwrap(json.loads(decode_frame(work()).decode("utf-8")))
    except BridgeError:
        raise
    except Exception as e:
        raise BridgeError(f"{method} failed: {e}") from e


# --------------------------------------------------------------------------- install


def _check_platform():
    if not (is_windows() or is_wsl()):
        die("`um ce` drives Cheat Engine, which is a Windows program. On Linux/macOS you can still run the MCP "
            "server here against a relay on the Windows box: `um ce relay` there, CE_MCP_TRANSPORT=tcp here.")


def _fetch(d: Path, ref: str):
    """Shallow-fetch one ref. Fails loudly rather than falling back to a moving branch."""
    r = subprocess.run(["git", "-C", str(d), "fetch", "-q", "--depth", "1", "origin", ref],
                       capture_output=True, text=True)
    if r.returncode:
        die(f"could not fetch {ref} from {BRIDGE_REPO}: {(r.stderr or r.stdout).strip()[-800:]}\n"
            "UM_CE_REF=<another ref> picks a different one; a SHA keeps the install reproducible")


def clone(ref: str | None = None, force: bool = False) -> Path:
    """One pinned commit, so two installs of the same ref are the same software. `force` re-fetches in
    place (never deletes the directory) - a `git clean` first drops untracked files, not your edits' history."""
    d = bridge_dir()
    ref = (ref or os.environ.get("UM_CE_REF") or BRIDGE_REF).strip()
    if (d / ".git").is_dir():
        if not force:
            return d
        run(["git", "-C", str(d), "clean", "-fdq"])
        _fetch(d, ref)
        run(["git", "-C", str(d), "checkout", "-qf", "FETCH_HEAD"])
        return d
    d.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q", str(d)])
    run(["git", "-C", str(d), "remote", "add", "origin", BRIDGE_REPO])
    _fetch(d, ref)
    run(["git", "-C", str(d), "checkout", "-q", "FETCH_HEAD"])
    return d


def install(args=None) -> int:
    _check_platform()
    d = clone(getattr(args, "ref", None), bool(getattr(args, "force", False)))
    ref = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    print("bridge", d, f"(pinned {ref[:12]})")
    print("source", BRIDGE_HOME, "-", BRIDGE_CREDIT)
    if not lua_path().is_file():
        die(f"the clone at {d} has no MCP_Server/ce_mcp_bridge.lua: it is not the bridge. UM_CE_DIR elsewhere?")
    req = requirements_path()
    if not req.is_file():
        die(f"no {req.name} in {d}: not the bridge, or a layout change upstream")
    out = run([python_cmd(), "-m", "pip", "install", "-r", str(req)]).stdout.splitlines()
    print("deps  ", out[-1] if out else f"{python_cmd()} -m pip install -r {req}")
    print()
    print("next   load it in Cheat Engine:  dofile([[" + str(lua_path()) + "]])")
    print("       (or File -> Execute Script -> that file). Look for 'MCP Server Listening on: CE_MCP_Bridge_v99'")
    print("check  um ce doctor")
    if getattr(args, "write", False):
        print()
        write_configs()
    return 0


# --------------------------------------------------------------------------- doctor


def ce_processes() -> list[dict]:
    """Cheat Engine processes, through the same PowerShell path `um win ps` uses. [] when we cannot look."""
    if not (is_windows() or is_wsl()):
        return []
    try:
        from um import win
        return [p for p in win.processes() if "cheatengine" in str(p.get("ProcessName", "")).lower()]
    except Exception:
        return []


def doctor(as_json: bool = False) -> dict:
    """Walk the whole chain - platform, clone, deps, CE process, pipe/relay, ping - and say what to fix."""
    rows: list[dict] = []

    def add(check: str, ok, detail: str, fix: str | None = None):
        rows.append({"check": check, "ok": ok, "detail": detail, "fix": fix})

    win_ok = is_windows() or is_wsl()
    add("platform", win_ok, "windows" if is_windows() else "wsl" if win_ok else sys.platform,
        None if win_ok else "Cheat Engine and its pipe are Windows-only. Run `um ce relay` on the Windows "
                            "machine and CE_MCP_TRANSPORT=tcp here")
    sp = script_path()
    add("bridge", sp.is_file(), str(sp) if sp.is_file() else "not installed", "um ce install")
    need = ["mcp"] + ([] if transport() == "tcp" else ["win32file"])
    missing = [m for m in need if importlib.util.find_spec(m) is None]
    add("deps", not missing, "+".join(need) if not missing else "missing: " + ", ".join(missing),
        None if not missing else f"{python_cmd()} -m pip install -r {requirements_path()}")
    procs = ce_processes()
    if procs:
        add("cheat_engine", True, ", ".join(f"{p.get('ProcessName')} (pid {p.get('Id')})" for p in procs))
    elif win_ok:
        add("cheat_engine", False, "no cheatengine*.exe running", "start Cheat Engine, then load the bridge")
    else:
        add("cheat_engine", None, "cannot look from this OS", None)
    ep = endpoint()
    reach, detail = ep.reachable(), pipe_name() if transport() == "pipe" else f"{tcp_host()}:{tcp_port()}"
    add("endpoint", reach, detail,
        None if reach else ("um ce relay" if transport() == "tcp" else
                            f"load it in CE: dofile([[{lua_path()}]])"))
    try:
        res = send("ping")
        ok = bool(res.get("success"))
        attached = res.get("process_id") or 0
        add("ping", ok, f"bridge v{res.get('version')}, attached process id {attached}" + ("" if ok else f": {res.get('error')}"),
            None if ok and attached else ("CE is running but attached to nothing: open the game in it and press "
                                          "Attach (or use the open_process tool)" if ok else
                                          "reload the Lua script in CE, then um ce ping"))
    except BridgeError as e:
        add("ping", False, str(e), "um ce doctor; if scans BSOD the machine: CE -> Settings -> Extra -> disable "
                                   "'Query memory region routines'")

    ok = next((r for r in rows if r["ok"] is False), None) is None
    report = {"ok": ok, "transport": transport(), "endpoint": detail, "checks": rows}
    if as_json:
        emit(report, as_json=True)
    else:
        print(f"um ce doctor ({transport()} -> {detail})")
        for r in rows:
            flag = "ok  " if r["ok"] else ("?   " if r["ok"] is None else "FAIL")
            print(f"  {flag} {r['check']:<12} {r['detail']}")
            if r["fix"]:                       # what to do about it, whether it failed or just needs a nudge
                print(f"       -> {r['fix']}")
        print("  ok  " if ok else "  FAIL", "everything the bridge needs is in place" if ok else "one or more checks failed")
        print()
        print("reminder: CE -> Settings -> Extra -> disable 'Query memory region routines' (scans on protected")
        print("          pages can otherwise BSOD the machine), and keep CE_MCP_ALLOW_SHELL unset.")
    if not ok:
        raise SystemExit(1)
    return report


# --------------------------------------------------------------------------- the rest


def tools(pattern: str | None = None) -> list[str]:
    """Tool names the bridge exposes, straight from its @mcp.tool() definitions."""
    src = script_path()
    if not src.is_file():
        die(f"bridge not installed: um ce install (looked in {src})")
    names = TOOLS_RX.findall(src.read_text(encoding="utf-8", errors="replace"))
    if pattern:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)
        names = [n for n in names if rx.search(n)]
    return names


def relay(host: str = DEFAULT_TCP_HOST, port: int = DEFAULT_TCP_PORT):
    """Run ce_tcp_relay.py in the foreground: pipe on this machine, TCP for the MCP server elsewhere."""
    _check_platform()
    script = relay_path()
    if not script.is_file():
        die(f"bridge not installed: um ce install (looked in {script})")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding {host} exposes Cheat Engine control to everyone who can reach it", file=sys.stderr)
    return run([python_cmd(), str(script), "--host", host, "--port", str(port)]).returncode


def server_entry(script: str | None = None) -> dict:
    script = script or str(script_path())
    return {"type": "stdio", "command": python_cmd(), "args": [script]}


def write_configs() -> list[str]:
    """Add the `cheatengine` MCP server to each agent config in this repo. Backs every file up first."""
    script = str(script_path())
    if not Path(script).is_file():
        die(f"bridge not installed: um ce install (no {script})")
    backups = data_dir() / "config-backups"
    backups.mkdir(parents=True, exist_ok=True)
    changed = []
    for rel, key, style in CONFIG_FILES:
        path = REPO / rel
        if not path.is_file():
            continue
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bak = backups / f"{rel.replace('/', '_').replace('.', '_')}-{stamp}.bak"
        bak.write_bytes(path.read_bytes())
        if style == "codex":
            body = path.read_text(encoding="utf-8")
            block = (f'[mcp_servers.cheatengine]\ncommand = "{python_cmd()}"\n'
                     f"args = ['{script.replace(chr(92), chr(92) * 2)}']\n")
            # replace our own block only, and stop at the next TOML section header ("args = ['..." holds brackets)
            rx = re.compile(r"^\[mcp_servers\.cheatengine\].*?(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
            new = rx.sub(lambda _m: block, body, count=1) if rx.search(body) else body.rstrip("\n") + "\n\n" + block
        else:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if style == "opencode":
                entry = {"type": "local", "command": [python_cmd(), script], "enabled": True}
            elif style == "stdio":
                entry = server_entry(script)
            else:
                entry = {"command": python_cmd(), "args": [script]}
            cfg.setdefault(key, {})["cheatengine"] = entry
            new = json.dumps(cfg, indent=2) + "\n"
        if new == path.read_text(encoding="utf-8"):
            continue
        path.write_text(new, encoding="utf-8")
        changed.append(rel)
        print("wrote", rel, "(backup:", bak, ")")
    if not changed:
        print("every agent config already has the cheatengine server; nothing to do")
    print()
    print("note: the entry holds this machine's paths. Commit them only if that is what you want;")
    print("      `git checkout -- <file>` drops one, or `um ce config` prints them for hand-editing.")
    return changed


def config(as_json: bool = False) -> list[str]:
    """The exact `cheatengine` entry for every agent config, with the real script path."""
    script = str(script_path())
    if not Path(script).is_file():
        die(f"bridge not installed: um ce install (no {script})")
    out = {"script": script, "command": python_cmd(), "files": {}}
    for rel, key, style in CONFIG_FILES:
        if style == "codex":
            snippet = (f'[mcp_servers.cheatengine]\ncommand = "{python_cmd()}"\n'
                       f"args = ['{script}']\n")
            out["files"][rel] = snippet
        else:
            entry = ({"type": "local", "command": [python_cmd(), script], "enabled": True} if style == "opencode"
                     else server_entry(script) if style == "stdio"
                     else {"command": python_cmd(), "args": [script]})
            out["files"][rel] = {key or "mcpServers": {"cheatengine": entry}}
    if as_json:
        emit(out, as_json=True)
        return []
    print(f"script: {script}")
    print(f"then run `um ce install --write` to add the entry to {', '.join(r for r, _, _ in CONFIG_FILES)}\n")
    for rel, key, style in CONFIG_FILES:
        print(f"# {rel}" + (f"  ({key})" if key else ""))
        if style == "codex":
            print(out["files"][rel], end="")
        else:
            print(json.dumps(out["files"][rel], indent=2))
    return list(out["files"])


def main(a):
    c = a.cmd
    if c == "install":
        install(a)
    elif c == "doctor":
        doctor(a.json)
    elif c == "ping":
        try:
            res = send("ping")
        except BridgeError as e:
            die(str(e))
        emit(res, as_json=a.json)
    elif c == "tools":
        names = tools(a.pattern)
        if not names:
            die(f"no tool matches {a.pattern!r} in {script_path()}")
        print("\n".join(names) if not a.json else json.dumps(names, indent=2))
        print(f"{len(names)} tools", file=sys.stderr)
    elif c == "relay":
        relay(a.host, a.port)
    elif c == "config":
        config(a.json)
        if getattr(a, "write", False):
            print()
            write_configs()


def register(sub):
    import argparse
    p = sub.add_parser("ce", help="Cheat Engine bridge: install, doctor, ping, tool list, TCP relay, MCP config",
                       description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cs = p.add_subparsers(dest="cmd", metavar="<cmd>")
    q = cs.add_parser("install", help="clone the pinned bridge and install its Python deps")
    q.add_argument("--force", action="store_true", help="replace the clone with a fresh one")
    q.add_argument("--ref", help=f"git ref/SHA to install (default {BRIDGE_REF[:12]}, or $UM_CE_REF)")
    q.add_argument("--write", action="store_true", help="also add the cheatengine MCP server to the agent configs")
    q.set_defaults(func=main)
    q = cs.add_parser("doctor", help="check the whole chain: CE, bridge, pipe/relay, ping")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=main)
    q = cs.add_parser("ping", help="one round trip: bridge version and the attached process id")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=main)
    q = cs.add_parser("tools", help="list the bridge's MCP tool names (optionally filtered)")
    q.add_argument("pattern", nargs="?", help="substring, case-insensitive")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=main)
    q = cs.add_parser("relay", help="TCP->pipe relay for a MCP server running off Windows")
    q.add_argument("--host", default=DEFAULT_TCP_HOST)
    q.add_argument("--port", type=int, default=DEFAULT_TCP_PORT)
    q.set_defaults(func=main)
    q = cs.add_parser("config", help="show (or --write) the cheatengine entry for each agent config")
    q.add_argument("--json", action="store_true")
    q.add_argument("--write", action="store_true")
    q.set_defaults(func=main)
