---
kind: technique
title: Running Windows DirectX 11/12 games on Apple Silicon from the command line with CrossOver's CLI
status: in-progress
agents:
- Claude Code (Opus 5.5 and Sonnet 5.5)
humans: ["DiegzM"]
date: '2026-10-14'
links:
- https://www.codeweavers.com/crossover
- https://developer.apple.com/games/
- https://github.com/KhronosGroup/MoltenVK
- https://github.com/Gcenx/macOS_Wine_builds
- https://developer.valvesoftware.com/wiki/SteamCMD
- https://github.com/BepInEx/BepInEx
- https://github.com/GDRETools/gdsdecomp
tags: [macos, apple-silicon, crossover, wine, d3dmetal, moltenvk, directx-11, directx-12, vulkan, steamcmd, steam, godot, unity, unreal, bepinex, verification, oracle]
---
# Running Windows DirectX 11/12 games on Apple Silicon from the command line with CrossOver's CLI

> `um win` only drives Windows games on Windows or WSL, so on a Mac a Windows-only game had no route. This note is
> a method to **try** one with CrossOver's command-line tools (`cxbottle`, `wine`), Apple's D3DMetal for DirectX
> 11/12 and MoltenVK for Vulkan, and to observe it like any other game: logs, window screenshots, input, a mod
> loader. It was validated **once, on one machine** (Apple M5, macOS 26.5.1, CrossOver 26.3) with **three games**:
> a Unity Direct3D 11 game, an Unreal Engine 5 DirectX 12 game and a non-Steam Godot Vulkan game. **It does not
> mean any Windows game will run.** Written by an AI agent with a human contributor's guidance; nobody else has
> reproduced it.

## When to use it
- The user is on an Apple Silicon Mac, owns a Windows-only game (no Mac build) and wants it running so the agent
  can study or mod it with the real game as the oracle.
- You need to know *quickly and honestly* whether a given game can run, and what is blocking it.

**Scope: what this does and does not mean**

| Situation | Status |
|---|---|
| Unity (Mono) game, Direct3D 11, Steam, no anti-cheat | **Validated** to gameplay, with keyboard/mouse driving and a mod loader |
| Unreal Engine 5 game, DirectX 12, Steam, no anti-cheat | **Validated** to the title screen on DirectX 12 (D3DMetal loaded); gameplay not seen |
| Godot 4.3 single-exe game, Vulkan, **no Steam**, clean bottle | **Validated** to a running race (Vulkan through MoltenVK); scripts recovered; a patched exe was not built |
| Other 64-bit DirectX 11/12 games | Expected to be possible, **untested**, per game |
| DirectX 9/10, OpenGL, other Vulkan games | Other translation paths. **Untested** here (the Halo Trial note under Seen in ran a D3D9 game through mtld3d) |
| 32-bit games | D3DMetal is described as 64-bit Direct3D 11/12 only (Gcenx's Sikarugir README; no Apple or CodeWeavers statement found). **Untested** |
| Kernel anti-cheat (EasyAntiCheat, BattlEye, Vanguard, ACE, ...) | Generally will not run. **Stop and tell the user; never bypass** |
| Launchers other than Steam, installers (`setup.exe`) | **Untested** |
| Intel Macs, other Apple Silicon chips | **Untested** (D3DMetal is Apple Silicon only) |
| Online features that use Epic Online Services | Login **failed** in both games that use it (cause unknown) |

Every end-to-end result came from **CrossOver's command-line tools** (a paid app; no GUI was opened). Free Gcenx
Wine builds passed the graphics probe and booted a Unity game but never gave a usable Windows Steam client (not
re-tested after the audio finding in Gotcha 9).

## Setup (what was used)
- Apple M5 (a MacBook Pro), 16 GB, macOS 26.5.1, Rosetta 2. CrossOver 26.3 (only `cxbottle`, `wine`, `wineserver`;
  bottle template `win11_64`; the bottle's graphics setting was left as created, and D3DMetal is what loaded.
  CodeWeavers documents the setting's "Auto" mode as a per-game choice from its database, falling back to wined3d,
  so another game may get DXMT, DXVK or wined3d instead: check with `lsof` (step 6)).
- Gcenx's Game Porting Toolkit 3.0-3 build (the `game-porting-toolkit` cask from `Gcenx/homebrew-wine`: a binary
  build of Apple's GPTK 1.1 Wine recipe, based on CrossOver 22.1.1 sources; its Wine reports 7.7; Apple's current
  release is GPTK 4, not tested), Gcenx Wine stable 11.0_1 and devel/staging 11.18 (sha256 checked), Homebrew
  `mingw-w64` 14.0.0_3 (to build probes as Windows exes).
- SteamCMD (Homebrew cask, build 1791918946), Windows Steam client build 1791918634, BepInEx 5.4.23.5, GDRE Tools
  2.7.4, ILSpy `ilspycmd` 11.1 on .NET SDK 10.
- Rules that apply (repo safety rules): only games the user owns; never bypass anti-cheat, DRM or ownership checks;
  never cheat other players; **ask first** before installing anything, editing a bottle's registry, adding files to
  a game folder, driving the user's mouse and keyboard (it steals focus) or publishing; the user types their own
  Steam login; kill processes by exact PID.

## How
1. **Pre-flight, in this order.** (a) Anti-cheat files (`EasyAntiCheat*`, `BattlEye*`, `vanguard`, `Denuvo`) in the
   game folder? If yes, stop. (b) `file *.exe` should say `PE32+ ... x86-64`. (c) Engine and graphics API from the
   store requirements and folder markers (`UnityPlayer.dll`; `Engine/` + `.pak`; a PE section named `pck` is a
   Godot export). (d) Does it need Steam? Look for `steam_api64.dll`, `steam_appid.txt`, `Steamworks.NET.txt`,
   `Engine/Binaries/ThirdParty/Steamworks`. **If none, skip Steam entirely.** (e) macOS permissions the user grants
   once: Screen Recording (window screenshots) and Accessibility (input).
2. **A clean private bottle**, inside a lab folder, never the user's own:
   `CX_BOTTLE_PATH=$LAB/bottles cxbottle --bottle NAME --create --template win11_64`. Run Windows programs with
   `CX_BOTTLE_PATH=$LAB/bottles wine --bottle NAME /absolute/path/prog.exe` (the path must be absolute).
3. **Run a graphics probe first.** A ~350-line C++ program (cross-compiled with
   `x86_64-w64-mingw32-g++ ... -ld3d12 -ldxgi -ld3dcompiler -ldxguid`) creates a D3D12 device, renders a triangle
   through a flip-model swap chain, reads the back buffer with the READBACK-heap pattern in
   [D3D12 frame readback: buffers only](d3d12-frame-readback-buffers-only.md), checks a corner and the centre pixel
   and exits 0 on PASS. It needs no screenshot permission. Observed: feature level 12_2, shader model 6.6, adapter
   `AMD Compatibility Mode` (D3DMetal) under CrossOver; the same under the GPTK 3.0-3 build's Wine 7.7; only
   feature level 11_0 under the free Gcenx Wine 11.x builds (Wine's own DX12 over MoltenVK, no D3DMetal).
4. **Get the game (Steam, no Mac build).** SteamCMD can download the Windows depot of a game the account owns by
   forcing the platform:
   ```
   steamcmd +@sSteamCmdForcePlatformType windows +force_install_dir "$LAB/games/<name>" \
            +login <username> +app_update <appid> validate +quit < /dev/null > download.log 2>&1
   ```
   `+@sSteamCmdForcePlatformType windows` must come first (here, anything else gave `Invalid platform`; Valve's
   SteamCMD wiki example also puts it first); `+force_install_dir` before `+login`, as the wiki asks. **The user
   logs in once in their own terminal**; later runs use the cached login
   (`steamcmd +login <username> +quit < /dev/null` should say `Logging in using cached credentials`). If it reports
   no subscription, stop: no other account, no entitlement tricks. Find the app id from the store URL or the public
   `store.steampowered.com/api/storesearch/?term=<name>&cc=us&l=en`. Observed: 1.8 GB and 5.4 GB games downloaded
   in under a minute each.
5. **Windows Steam, only if the game loads Steamworks.** Valve's installer (the store's download page links
   `cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe`; this run used the `cdn.fastly` mirror), run as
   `wine --bottle NAME "$PWD/SteamSetup.exe" /S` (an absolute path, Gotcha 1; `/S` is the NSIS silent switch),
   then start `steam.exe -no-cef-sandbox -cef-disable-gpu -cef-disable-gpu-compositing` from
   `C:\Program Files (x86)\Steam` with the shell's open-files limit raised (`ulimit -Sn 61440`). The first launches
   self-update (a 336 MB client package, then a 64-bit one) and each ends with
   `Update complete, launching Steam... Shutdown`; launch again until nothing is pending (3 launches). The user
   types the login once; "Remember me" makes later starts sign in alone (`SetLoginState: Success`). The Steam
   window may sit on its spinner forever; games only need Steam running and logged on. Launch the game **in the
   same bottle**, and when starting a game exe yourself set `SteamAppId`/`SteamGameId` to its app id. If Steam
   crashes at startup, read Gotcha 9 before anything else.
6. **Run the game windowed and read the evidence.** Pass the engine's own log option (Unity `-logFile <path>`,
   Godot `--log-file <path>`). Check which graphics modules loaded:
   `lsof -p <pid> | grep -i -E "d3d12|d3d11|D3DMetal|MoltenVK|DirectML"`. Screenshot the window by id
   (`screencapture -x -o -l <id> out.png`; ids and owner PIDs from `CGWindowListCopyWindowInfo`, which needs no
   permission). A black window can simply be loading (the Godot game stayed black for about a minute): wait and
   capture again, comparing frames numerically if an animation could fake a change.
7. **Drive it (ask first).** Details in Gotchas 19-21. What worked: focus the game's process (System Events
   `frontmost`), then post key and mouse events to the HID tap; held `W` moved the character, relative mouse deltas
   turned the camera, clicks at `window_origin + screenshot_pixel / 2` pressed menu buttons.
8. **Mod loader and a command bridge (offline sessions only).** BepInEx 5.4.23.5 loaded under CrossOver on a Unity
   6 Mono game once its `winhttp.dll` proxy was made the native DLL **through the bottle registry** (Gotcha 12). A
   plugin that polls a text file and feeds each line to the game's own command API gave a scriptable test console,
   the same idea as
   [Driving a game from a mod-side command file](driving-a-game-from-a-mod-side-command-file-lab-profile-wake.md).
   Never use it against other players.
9. **Stop by exact PID**, then record what worked, what did not, the versions and what you did not verify.

A non-Steam, non-DirectX example (a Godot 4.3 export, one 174 MB exe): `file` says `PE32+ ... x86-64`; the `pck`
section (90 MB) starts with `GDPC` and its header gives pack format 2 and Godot 4.3.0 with an unencrypted directory
of 446 files (scripts as compiled `.gdc`). In a clean bottle with no Steam and nothing else installed it logged
`Vulkan 1.2.290 - Forward+ - Using Device #0: Apple - Apple M5` (Vulkan through MoltenVK and Metal) and reached a
race with its HUD. GDRE Tools' headless mode (`--recover=<exe> --scripts-only`) decompiled all 45 scripts; its help
lists `--compile ... --bytecode=4.3.0` and `--pck-patch` as the route to a modded exe, which was **not built or
run**. Keep decompiles outside any repository.

## Verification
What was observed, per game (one session, 13-14 October 2026):

| Game | Result |
|---|---|
| **PEAK** (Steam app 3527290, Unity 6000.3.15f1 Mono, v2.6.b) | GPTK 3.0-3 build (Wine 7.7): crashed right after input init. Gcenx 11.x: booted on Vulkan (Apple M5 via MoltenVK), stalled at the Steam check. **CrossOver CLI + signed-in Windows Steam:** Direct3D 11 (`Direct3D 11.0 [level 11.1]`, renderer `AMD Compatibility Mode`; Unity's own filter denied DX12), reached gameplay (airport, island), keyboard and mouse driven, BepInEx plus a plugin loaded, a command-file bridge ran the game's console commands with verified on-screen effects (Gotcha 17). Epic login failed |
| **MECCHA CHAMELEON** (Steam app 4704690, Unreal Engine 5.6 by its config folder; an online PvP game, with no single-player mode on its store page: nothing was injected into it, and the mod-loader step 8 doesn't apply to it) | SteamCMD download 5.4 GB. The launcher stub refused to start (Gotcha 5); the real shipping exe ran: **title screen on DirectX 12** (`lsof` showed `d3d12.dll`, `dxgi.dll`, `DirectML.dll`, D3DMetal, `libmetalirconverter`). Epic login failed. Gameplay not seen; one Enter key press was inconclusive |
| **A small Godot racing game** (the contributor's own export) | no Steam, no extra install: Vulkan 1.2 Forward+ via MoltenVK, reached a race with HUD (position, lap, timer, leaderboard, boost). Scripts recovered 45 of 45. A patched exe was not built |

Checked only weakly: sound (the game process loaded `winecoreaudio.drv`; nobody listened), performance (no frame
rate or load times), input outside PEAK, any online feature.

## Gotchas
1. **Symptom.** `cannot execute L"game.exe"` from CrossOver's `wine`. **Cause:** the wrapper wants an absolute
   path. **Fix:** pass `"$PWD/game.exe"`.
2. **Symptom.** A Unity 6 game logs `Crash!!!` right after `<RI> Initializing input.` (stack in `UnityMain2`).
   **Cause:** Wine too old; the GPTK 3.0-3 build is Wine 7.7. **Fix:** use CrossOver's Wine or
   Gcenx Wine 11.x. Do not debug the game.
3. **Symptom.** `[D3D12 Device Filter] ... D3D12 API denied by user filter` (Unity). **Cause:** the game's own
   filter (feature level below 12.1 or 4 cores or fewer). **Fix:** none needed; Unity falls back to D3D11 or
   Vulkan. Do not force DX12.
4. **Symptom.** `d3d12: could not load DirectML.dll`, `failed to create D3D11On12 device`. **Cause:** D3DMetal path
   without DirectML. **Fix:** harmless; the game continues on D3D11.
5. **Symptom.** An Unreal game's launcher shows a window titled `Error`: "The following component(s) are required
   to run this program: Microsoft Visual C++ 2015-2022 Redistributable (x64)". **Cause:** the root `<Project>.exe`
   is a prerequisite stub. Its machine code and a registry trace show it reads
   `HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64` (`Major`, `Minor`, `Bld`; needs 14.42.34438 or
   newer), then loads `system32\msvcp140_2.dll` and checks its version. In the tested bottle the registry and every
   runtime DLL were 14.51.36247 and the DLL loaded, yet the dialog stayed; which step fails was **not found**.
   **Fix:** stop installing redistributables. Run
   `<Project>/Binaries/Win64/<Name>-Win64-Shipping.exe -windowed -ResX=1280 -ResY=720` directly, or use a 280 KB
   launcher that starts that exe with the same arguments. This is a prerequisite check, not DRM, but check for
   anti-cheat files first, and swapping a stub inside Steam's library edits a game file (Steam's "verify files"
   restores it): ask the user.
6. **Symptom.** A Godot game shows a black window for the first minute. **Cause:** loading and first-run shader
   compilation. **Fix:** wait and re-capture; the Godot log should show `Vulkan ... Forward+ ... Apple`.
7. **Symptom.** `SteamAPI_Init() failed` / endless `Steamworks is not initialized`. **Cause:** no Steam client in
   the game's bottle. **Fix:** install and sign in to Windows Steam in the **same** bottle (How 5).
8. **Symptom.** "Updating Steam..." never advances at 0% CPU; the bootstrap log says
   `Update complete, launching Steam... Shutdown`. **Cause:** the updater hands off and hangs. **Fix:** kill that
   `steam.exe` by exact PID and launch again until no update is pending.
9. **Symptom.** Windows Steam crashes about 20 s after start: the web helper logs `Quit message loop`,
   `assert_steam.exe_*.dmp` files appear (`strings -a <dump> | grep Assertion`; quote the path, it has spaces), and
   the **first** line of the launcher output is
   `Assertion failed: !status, file ../../wine/dlls/mmdevapi/mmdevapi_private.h, line 120`, followed by
   `Illegal termination of worker thread 'CFileWriterThread'`, `Failed to create thread (error 0x5)` and
   `Waited 6000 iterations in CFileWriter::Flush`. **Cause (mechanism):** `wine_unix_call()` in mmdevapi calls the
   Mac-side CoreAudio driver and then does `assert(!status)`, so any CoreAudio error aborts; the same code is in
   upstream Wine 11.0 and 11.18 (line 120 in 11.0, which CrossOver 26 is based on; line 118 in 11.18). It is
   **intermittent and environment-dependent**: Steam started fine with audio on for about 45 minutes, then failed
   on every start until audio was disabled, with nothing changed in Wine or the bottle. The Mac had 9 output and 7
   input devices including virtual ones (BlackHole, Zoom, Teams drivers) and a USB default output; none was proven
   to matter. **Not found:** the failing call. A probe that lists every device, reads each mix format and starts a
   real shared-mode stream ran 14 times without failing. **Rejected as the cause:** the open-files limit (it
   crashed with the limit raised), a cold Wine server, the Visual C++ runtime install, a bottle-level
   `WINEDLLOVERRIDES` entry. **Fix (workaround only, apply only when the first line above is present; ask first, it
   edits the registry):** back up the bottle's `user.reg`, set `HKCU\Software\Wine\Drivers` value `Audio` to empty
   (`wine --bottle NAME reg add 'HKCU\Software\Wine\Drivers' /v Audio /t REG_SZ /d '' /f`), start Steam, and when
   it says `Logged On` delete the value (`reg delete ... /v Audio /f`). Steam keeps running without sound and
   **games started afterwards get sound** (their process loads `winecoreaudio.drv`; check with `lsof`). If the
   mmdevapi line is absent, it is a different problem.
10. **Symptom.** The same Steam crash-loop but with file-writer assertions only. **Cause:** not the audio issue.
    **Fix:** check the open-files limit (`ulimit -Sn`, macOS Terminal defaults to 256; raise to 61440 for the
    launching shell), a hung updater, or a bottle-level `WINEDLLOVERRIDES` entry. None of these fixed the audio
    crash.
11. **Symptom.** Windows Steam shows blank white boxes (stable 11.0) or a solid black login window (devel 11.18)
    under free Gcenx Wine builds. **Cause:** not established; Steam creates the window and loads the page but the
    picture is not presented. A Wine virtual desktop made it worse (the web helper quit); the GPU flags gave no
    window. **Fix:** CrossOver's CLI `wine`, where the login window drew correctly. Not re-tested under the free
    builds after Gotcha 9, so it may be the same crash.
12. **Symptom.** BepInEx never starts (no `BepInEx/LogOutput.log`). **Cause:** the `WINEDLLOVERRIDES=winhttp=n,b`
    environment variable is ignored by CrossOver's wrapper, and so is the bottle's `[EnvironmentVariables]` entry.
    **Fix:**
    `wine --bottle NAME reg add 'HKCU\Software\Wine\DllOverrides' /v winhttp /t REG_SZ /d native,builtin /f` (back
    up `user.reg` first). Proof: the log appears (`BepInEx 5.4.23.5 - <Game> ... Running under Unity v6000.3.15`).
13. **Symptom.** `WINEDEBUG=+loaddll` prints nothing through CrossOver. **Cause:** the wrapper ignores the plain
    variable. **Fix:** `wine --bottle NAME --debugmsg +reg,+loaddll game.exe`. CodeWeavers' own support pages
    document the `CX_DEBUGMSG` and `CX_LOG` environment variables for the same job.
14. **Symptom.** Steam and the game die together. **Cause:** `wineserver -k` stops every process in the bottle.
    **Fix:** kill the game's exact PID instead; only run `wineserver -k` when nothing else shares the bottle.
15. **Symptom.** The Mac Steam app shows "Refresh Sign In" after a SteamCMD login, or a Windows Steam download is
    disturbed. **Cause:** SteamCMD logging in as the same account ends other sessions, and it shares
    `~/Library/Application Support/Steam` with the Mac app. **Fix:** do not run SteamCMD while a Steam download or
    game is running; warn the user.
16. **Symptom.** Unreal title screen: "Failed due to invalid or missing authentication token for user (e.g. not
    logged in)"; Unity: `LoginAsync result: ConnectExternalTokenValidationFailed`. **Cause:** Epic Online Services
    rejected the Steam login in both engines while Steam was signed in; **unknown**. **Fix:** none found; not
    blocking for rendering or offline tests. Untested: launching through Steam's Play button.
17. **Symptom.** A console command is "found and run" but nothing changes. **Cause:** the game's logic ignored it
    (PEAK ignores status effects in its airport scene; the same `AddPoison` put a poison segment on the stamina bar
    on the island, `ClearAll` removed it, and `SetTimeOfDay 2` turned the sky to night, mean brightness 183 to 66).
    The console command's domain can also be a renamed class (`CharacterAfflictions` is exposed as `Afflictions`;
    the wrong name returns "NOT FOUND"). **Fix:** verify every effect by screenshot or log, in the right scene. See
    [Oracles](oracles-how-agents-know-a-mod-works.md).
18. **Symptom.** `screencapture`: `could not create image from window/display/rect`. **Cause:** Screen Recording
    permission missing for the terminal's host app. **Fix:** ask the user to enable it in System Settings, Privacy
    & Security, Screen & System Audio Recording, then quit and reopen the host app.
19. **Symptom.** Synthetic keys do nothing. **Cause:** `CGEvent.postToPid` is ignored while another app is
    frontmost; Wine's macOS driver only reacts when its window is active. **Fix:** focus the game first
    (`tell application "System Events" to set frontmost of (first process whose unix id is <pid>) to true`), then
    post to the HID tap. Needs Accessibility (check with `AXIsProcessTrusted`); it steals focus, so tell the user
    first ([Driving real games safely](driving-real-games-safely.md)). The HL2-under-Wine note (gotcha 13) saw the
    same in a Sikarugir wrapper: `NSRunningApplication.activate` can't bring Wine to the front, System Events can.
20. **Symptom.** A click lands in the wrong place. **Cause:** screen points, not pixels (Retina screenshots are
    2x), and zsh does not word-split unquoted variables (`set -- $VAR`). **Fix:** compute
    `window_origin + pixel / 2` and parse window bounds in Python.
21. **Symptom.** Escape does not close a pause menu. **Cause:** not found. **Fix:** click its Back button; confirm
    with before/after screenshots because the game's log rarely records menu actions.
22. **Symptom.** `brew install --cask wine-stable` says "disabled ... Gatekeeper". **Cause:** Homebrew disabled the
    WineHQ casks on 2026-09-01. **Fix:** use the Gcenx release tarballs (check the sha256 GitHub lists) or
    CrossOver.
23. **Symptom.** `Error: spawn Unknown system error -86` or x86_64 programs will not start. **Cause:** Rosetta
    missing. **Fix:** ask, then `softwareupdate --install-rosetta --agree-to-license`.
24. **Symptom.** `zsh: no matches found: https://...?per_page=3`; or `grep` rejects `-P` / a file named
    `-Something.cs`. **Cause:** `?` is a glob in zsh; the shell may alias `grep` to `ugrep`, and leading-dash
    filenames read as options. **Fix:** quote URLs; call `/usr/bin/grep` and use `./*.cs`.
25. **Symptom.** Leftover `winewrapper.exe` processes after a run. **Cause:** wrapper processes outlive the game.
    **Fix:** kill those exact PIDs (see Gotcha 14).

## Seen in
- PEAK, MECCHA CHAMELEON and a small Godot racing game (above); no per-game notes exist yet.
- Related notes:
  [Running xEdit and other Windows modding tools on Linux](running-xedit-and-windows-modding-tools-on-linux.md)
  (the Wine/Proton counterpart),
  [Half-Life 2 guns in Minecraft (HL2 Demo under Wine on macOS)](../games/minecraft/half-life-2-guns-in-minecraft-passthrough-hl2-demo-under-win.md)
  (Windows Steam in a Sikarugir wrapper, Wine 10 with DXVK),
  [Max Payne bullet time in Halo Trial on macOS/Wine](../games/halo-trial/max-payne-bullet-time-shootdodge-and-voice-in-halo-trial-via.md)
  (a D3D9 game under GameHub's Wine with mtld3d, D3D9 to Metal),
  [D3D12 frame readback: buffers only](d3d12-frame-readback-buffers-only.md),
  [Oracles](oracles-how-agents-know-a-mod-works.md), [Driving real games safely](driving-real-games-safely.md). The
  mashup case list in `skills/mashup-mods/references/mashup-cases.md` also links CrossOver-with-D3DMetal bridges
  for two other games.
- Difference from the HL2 note: this one uses CrossOver's command-line tools end to end and adds SteamCMD
  downloads, Windows Steam in the bottle, a DirectX 12 game and a non-Steam game.

## Open questions
- **Epic Online Services rejecting the Steam login** in two engines (Gotcha 16). Untested: Steam's own Play button,
  the ticket identity EOS expects, clock or network.
- **The Steam audio crash trigger** (Gotcha 9): run Steam under a plain Wine build with `WINEDEBUG=+mmdevapi,err`,
  with and without the virtual audio drivers and with Zoom/Teams/Logic running.
- **Why the Unreal stub rejects an installed C++ runtime** (Gotcha 5): both checks pass on paper; a debugger or
  deeper trace is needed.
- **Free Wine builds and Windows Steam** were not re-tested after Gotcha 9.
- **Untested:** other chips and macOS versions, Intel Macs, DirectX 9/10, OpenGL, other Vulkan games, 32-bit games,
  installers, launchers other than Steam, anti-cheat games, controllers, performance, actually listening to the
  sound, a Steam game started from Steam's Play button, a patched Godot export.
- **Tooling the repo lacks:** a `um mac` command group mirroring `um win` (launch in a bottle, list windows,
  screenshot, focus, key/mouse, kill by PID) and `um scan` detecting CrossOver bottles. Suggested after maintainer
  discussion, with tests. Versions here were current in October 2026 and will age (CrossOver, Wine builds,
  SteamCMD, the Steam client): re-check the current release.
