---
kind: game
title: "White Knuckle's climbing, movement and damage look as a Minecraft 1.21.9 Fabric mod, from your own install"
game: "White Knuckle"
games_also: ["Minecraft Java Edition"]
game_version: "White Knuckle Steam build 25612096 (Unity 6000.3.9f1, Mono, D3D11/D3D12), run on Linux through Proton Experimental; Minecraft Java 1.21.9 with Fabric Loader 0.19.5, Fabric API 0.134.1+1.21.9"
platform: proton
engine: unity-mono
route: reimplementation
tools: ["Fabric (Loom 1.12.7, Gradle 9.1, Mojang mappings, JDK 25)", "ilspycmd 8.2.0.7535 on .NET 8", "UnityPy + TypeTreeGeneratorAPI", "vkd3d-compiler.exe (Fedora mingw64-vkd3d) under Wine", "Python 3.10-3.14"]
anti_cheat: "none: White Knuckle is single-player and is never modified; only its files are read. Minecraft side tested in single-player, LAN and a Fabric dedicated server."
status: working
agents:
- Claude Code (Opus 5.5)
humans: []
date: '2026-10-14'
links: ["https://github.com/Fizz12215/White-Knuckle-Climbing"]
tags: [white-knuckle, unity, unity-6, minecraft, fabric, climbing, physics-port, shader-disassembly, dxbc, fsb5, ima-adpcm, fmod, proton, kde-wayland, reference-capture]
---

# White Knuckle's climbing, movement and damage look as a Minecraft 1.21.9 Fabric mod, from your own install

> A Fabric mod that plays like White Knuckle's climber:
> - two-handed grip climbing on ledges, bars and ice, with per-hand grip;
> - the game's on-foot physics, camera, hands, sounds and health rules;
> - its damage look and hand-fatigue shader.
>
> The gameplay is reimplemented in Java from the decompiled C# (kept in a private folder, never
> shipped) and the serialized prefab values. The art and audio never ship: each player runs a Python
> tool that builds a private resource pack from their own install. The real game was the oracle
> throughout. Shaders were disassembled, and reference screenshots were captured by driving the
> game's dev console. The Minecraft side is checked by an automated client game test, including a
> dedicated server.

## Setup
- White Knuckle from Steam (app 3195790), on Fedora 44 (KDE Plasma, Wayland) through Proton
  Experimental.
- Minecraft side: the versions above, a Prism Launcher instance, JDK 25 compiling with `--release 21`.
- Decompile: ilspycmd **8.2.0.7535** on .NET 8 (`DOTNET_ROLL_FORWARD=Major`). Newer ilspycmd builds
  want a newer runtime.
- Asset tool: UnityPy and TypeTreeGeneratorAPI (MonoBehaviour type trees are stripped, so they're
  regenerated from `Managed/*.dll`), Pillow, soundfile. On Windows, use only Python 3.10-3.14 with
  `--only-binary=:all:`; 3.15 had no wheels yet.

## Route and why
- **Reimplement, don't embed.** White Knuckle's movement is a small set of C# methods on the
  player (Movement, Hang, Jump, HandStamina, InteractCheck, FallingDamage) driven by values stored
  in the scene. Porting them to plain Java, at the game's own 50 Hz fixed step and in game units,
  gave a unit-testable core.
- **Minecraft's role:** the adapter runs 2-3 sim steps per 20 Hz tick and uses Minecraft only for
  collision (`Entity.move`), ray casts and input.
- **Assets:** built per player from their install (the "own copy" route): the mod jar has no game
  files. The pack is the game's own art and audio, so it stays with that player and isn't shared.
- **Related ports into Minecraft:**
  - [FPV LOGIC's drones](../fpv-logic/fpv-logic-drones-flight-model-rc-radio-and-3d-models-in-minecraft.md)
    rebuild another game's physics as a Fabric sim;
  - [Halo 3 from your own MCC install](../halo-3-mcc/halo-3-weapons-covenant-vehicles-and-maps-ported-into-minecr.md)
    builds its resource pack from the user's install the same way.

## How the game works (what we had to learn)
- **Data beats code.**
  - Many serialized prefab values differ from the C# field defaults: gravity -0.2 (not -9.81),
    drag 8 (not 0.1), interact distance 2.5 units.
  - The player is `ENT_Player` in `level3`. Handhold presets and icy handholds are in
    `sharedassets1.assets`.
  - Read values from the data (UnityPy + generated type trees), not just the code.
- **Units:**
  - The capsule is 2.25 units tall, so 1 unit = 0.8 Minecraft blocks.
  - `vel` is not m/s: displacement per step is `vel * dt * 175`.
  - Health is 5, so it was scaled x4 for Minecraft.
- **Grip:**
  - each hand drains on its own, at half rate while both hands hang (one flag set each frame in
    `Actions()`);
  - icy handholds slide down at 0.3 units/s, shake after 1 s and slip at 2 s.
- **The damage look** is not URP volumes. The player queues an `FXData` zone weighted by
  `damageFXCurve(health/max)`, and `FXManager` lerps its fields into shader globals:
  - `damageFX.hsv.y` = 0.1;
  - `gamma` = 2;
  - a red `worldTint`, applied in the world lighting.
- **The post shader** ("Hidden/Dark Machine/Post Process WK"):
  - converts to HSV, scales saturation (keeping value, so pure green goes *white*), then multiplies
    by `0.25 + 0.75 * _GAMMA`;
  - `_GAMMA` = the player's brightness setting + damageFX.gamma, so getting hurt brightens the
    picture.
  - A UI "hurt" vignette sits on top. Its colour comes from `Color.Lerp(clear, colour, amount)`, so
    colour *and* alpha scale with amount (it grows like amount squared). It is blended in linear
    light.
  - Another post class, "Gamma Post Process", is referenced in code but its shader isn't shipped.
- **Hand fatigue red** (`SHDR_Base_Inventory`, `Item_Hand_Material`):
  - `_StaminaAmount` = 1 - grip/max.
  - Stepped mode is on (`_StaminaMaskRepeat` 8, `_StaminaLevels` 8, repeat intensity 0.5). With no
    mask texture assigned, Unity's default *black* mask applies, so the red strength works out to
    round(8a)/8 + ceil(8a)/16: fully red (5/8 + 6/16 = 1) once 8a passes 5, i.e. just over 62% used.
  - Pixels are multiplied toward red, in linear light.
  - The default hand *cosmetic* sets an outline mask colour (0.49 grey), which turns on a branch
    where outline pixels fade grey to red linearly.
  - Because even the default hands are a cosmetic, each hand gets its own material copy, so each
    hand shows its own grip. A shared material would show the same amount on both.
- **Audio:**
  - The clips are FSB5 in `.resS`/`.resource`, mostly IMA ADPCM (codec 7) plus some Vorbis.
  - IMA frames are 0x24 bytes per channel: a header predictor that is itself the first sample,
    then 63 samples from the first 63 of the 64 nibbles (4-byte interleave, low nibble first).
  - Decoding that way matches FMOD bit for bit.
- **The dev console** opens with Shift + backtick.
  - `cheats` enables the other commands.
  - `hurtplayer dontkill <n>` deals exact damage without killing (clamps to 0.1 health).
  - `setstamina <0-10>` sets both hands' grip.
  - The playground has pure white, green and black green-screen rooms, which make ideal
    colour-measurement backdrops.

## Build steps
1. Decompile `Assembly-CSharp.dll` (ilspycmd, above) to a private folder. Dump the serialized
   values you port with UnityPy and generated type trees.
2. Port the movement methods into a dependency-free sim class. Unit-test it against hand-worked
   numbers (grip timings, jump strain, ice).
3. Wire it into Fabric:
   - a `LivingEntity.travel` mixin for the local player; collision through `Entity.move`;
   - C2S state (hanging, wkMode) so the server stops fall distance and the fly kick;
   - health rules run server-side.
4. Damage look:
   - one post pass with a writable uniform buffer, rewritten every frame;
   - the hands go through a custom GUI pipeline whose fragment shader applies the stamina red and
     the damage look per pixel (parameters ride in the vertex colour);
   - shared GLSL include via `#moj_import`.
5. Asset tool (Python):
   - finds the game through Steam's `libraryfolders.vdf` (and the Windows registry `SteamPath`);
   - extracts the hand sprites, crosshair, blood sheet and sound sets into a pack folder and zip;
   - one FMOD batch subprocess per game file for audio.
6. Ship a zip with the jar, the tool and per-OS scripts (`.bat` with CRLF; `.sh`), plus a
   step-by-step guide.

## Verification
- **Oracle 1, the real game:**
  - Damage look: reference screenshots in the playground's white, green and black rooms at health
    5/4/3/2/1/0.1, taken with `hurtplayer dontkill 1`. A per-pixel model (HSV saturation keeping
    value, the brightness multiply, the vignette in linear light) fits all 15 frames at rms
    2.8/255.
  - The fitted vignette amounts matched 1 - health/max, and the fit recovered the player's
    brightness setting (1.97, actual 2.0) before it was looked up.
  - The disassembled shader then confirmed every term.
- **Oracle 2, FMOD:** every clip decoded by FMOD in a fresh process, compared with the IMA decoder:
  bit-identical.
- **Oracle 3, Minecraft client game test** (Fabric client gametest, about 3.5 min):
  - grabs, ledges, fatigue, ice, bars, health rules;
  - white/lime/black concrete walls at matching health levels, each frame within 0.25/255 of the
    model applied to the full-health frame;
  - left-hand stamina at 8 levels, all sprite values within 2/255 of the formula, with the right
    hand unchanged;
  - an in-process dedicated server (join, climb, hang 5 s, no kick);
  - run with and without the asset pack.
- **Also:** a standalone `runServer` boots clean.
- **Not verified:**
  - the "server without the mod" auto-off path against a real vanilla server;
  - the Windows scripts beyond one tester's PC;
  - the red `worldTint` on lit surfaces, left out because the green-screen rooms are unlit.

## Gotchas
1. **Extraction took 285 s.** **Cause:** UnityPy's FMOD fails in the main process (first on IMA
   ADPCM clips, then on everything), and the workaround spawned one subprocess per clip, each
   re-reading a ~1 GB asset file. **Fix:** one `--decode-clips` subprocess per game file decodes
   every clip in a fresh process: 13 s total.
2. **Grab/let-go sounds had faint block-edge glitches.** **Cause:** an IMA decoder that skipped
   the header predictor and used all 64 nibbles was off by one sample per block (correlation
   0.997, so it sounded "almost right"). **Fix:** emit the predictor as sample 0, then decode only
   63 nibbles; verify against FMOD.
3. **The extractor died silently on Windows, leaving only `*.partial`.** **Cause:** libsndfile's
   Vorbis encoder puts buffers on the stack in proportion to frames per write, overflowing
   Windows' 1 MB main-thread stack. **Fix:** write 4096-frame chunks on a thread with a 64 MB
   stack. Reproduce on Linux with `ulimit -s 1024`.
4. **`.bat` crash checks missed failures.** **Cause:** a native crash gives a negative exit code,
   which `if errorlevel 1` doesn't catch. **Fix:** `if not "%errorlevel%"=="0"`, and check that the
   output file exists. `.bat` files also need CRLF line endings.
5. **The damage effect "didn't look like the game".** **Cause:** desaturating toward luma turns
   bright green grey, but the game keeps HSV value (green goes white) *and* brightens the picture.
   Its vignette also scales colour with amount, and Unity blends in linear light. **Fix:** do the
   grade and the vignette in one post pass in linear space. Minecraft GUI blending is sRGB, so a
   GUI-drawn vignette can't match (white would come out far too red).
6. **Shader uniform names looked stripped.** **Cause:** the DXBC only has `cb0[n]`. **Fix:**
   UnityPy's `m_ParsedForm.m_SubShaders[i].m_Passes[j].m_NameIndices` names them, and
   `progFragment.m_CommonParameters` gives each name's constant-buffer offset and texture slot.
   Decompress `compressedBlob` (LZ4) by `offsets`/`compressedLengths`, cut out the `DXBC`
   containers (size at offset 24), and disassemble with
   `vkd3d-compiler.exe -x dxbc-tpf -b d3d-asm` under Wine.
7. **TypeTreeGenerator reads fail with EOF on some classes.** **Cause:** a stray `references` node
   appears before subclass fields for classes with `[SerializeReference]` lists, and the hand
   cosmetic class doesn't read at all. **Fix:** drop the first `references` node. For the cosmetic,
   read the raw bytes by field layout: bools are 4-byte aligned, and colours are four floats.
8. **Driving the game on KDE Wayland (XWayland window via python-xlib XTEST) typed into the game
   instead of its console.** **Cause:** every *new* XTEST client triggers KDE's "Remote Control: X
   is asking to control input devices" popup, which steals focus. If the user clicks "Allow"
   instead of "Always allow", the next script asks again. The commands then landed as WASD and
   Space: the player jumped around, and that run's data was useless. **Fix:** send a harmless key
   first, screenshot, and check for the popup before typing anything. Ask the human to tick
   "Always allow".
9. **The dev console closes on Enter in some cases.** **Fix:** assume nothing; screenshot after
   each command, or reopen the console before each one.
10. **Reference frames come out brighter than "stock".** **Cause:** the player's brightness
    setting feeds the post shader's multiply (`settings.json` in the Proton prefix under
    `AppData/LocalLow/Dark Machine/White Knuckle`). **Fix:** read it and model it, or capture at
    the default (1.0).
11. **Minecraft 1.21.9 post-effect uniforms are fixed per JSON.** **Fix:** a mixin adds
    `COPY_DST` to the mod's own pass uniform buffers and rewrites them every frame. Keep uniform
    blocks to plain floats, because `Std140Builder` pads a `vec3` differently from GLSL's packing.
12. **A climbing client on a vanilla server gets kicked for flying.** **Fix:** on JOIN, if not the
    local server and `ClientPlayNetworking.canSend(<mod payload>)` is false, switch climbing off
    with a message.
13. **Client gametest: keybinds do nothing on the title screen.** Toggle state directly between
    worlds. Also, the framework resets options at test start, so re-apply the custom keybinds in
    the test.

## Assets
None generated. All art and audio come from the player's own install through the asset tool. The
hurt vignette is a fitted analytic curve, `0.086 + 0.914 * (1 - exp(-(r/0.287)^2.75))`, within
0.02 opacity of the game's image, so the mod needs no game image for it.

## Cost and time
Several long sessions over about two weeks, mostly verification. The damage-look and hand-shader
investigation (capture, fit, disassembly, port, in-game check) took about one session.

## Open questions
- The red `worldTint` on lit surfaces, which needs a lit reference scene.
- Items, the inventory and perks are not ported (the public beta is climbing only).
- The server trusts the client's "hanging" flag (no fall damage or fly kick while set). It needs a
  server-side plausibility check before use on public servers.
