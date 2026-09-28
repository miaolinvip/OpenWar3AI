# OpenWar3 — write AI for Warcraft III 1.27 in any language

**English** · [简体中文](README_ZH.md) · [Website & docs](https://war3ai.com/en/)

**An open API for Warcraft III, built for AI agents. One runtime, one protocol, any model, any language.**

OpenWar3 is a runtime injected into the game (a DLL) plus a Python SDK. Every 50 ms the runtime pushes the
**complete state of the whole map** into shared memory — every player's resources; every unit's HP / mana, order,
current target, cooldowns, buffs and inventory; items on the ground; trees — together with an **event stream**.
External programs issue semantic commands (move, attack, gather, build, train, cast, learn skills, revive, use items…)
with **about one frame of latency**, and every command gets a receipt saying whether it was accepted and, if not, why.
The whole contract between the runtime and your code is a documented protocol ([W3P](https://war3ai.com/en/docs/protocol/)).

The repository also ships a complete reference AI (expanding, creeping, attacking, hero spells, staying alive),
a web console (**Farsight**), automatic camera direction and in-game chat bubbles.

> ⚠ Only for a Warcraft III 1.27 client **you legally own**, on your own machine, LAN or self-hosted games.
> `Game.dll` on disk is never modified and no Blizzard files are distributed.
> **Never use it on Battle.net or on any server with anti-cheat.**

## Quick start

You need 64-bit Windows 10 / 11 and your own Warcraft III **1.27a** (The Frozen Throne, `Game.dll` 1.27.0.52240).
Nothing else has to be installed first.

**Double-click `start.bat`.** The first time, it will:

1. Download Python 3.13 (the official portable package, about 14 MB) into the repository's `bin\env\` —
   **the only thing it installs**. No administrator rights, no changes to the system PATH; on networks in mainland China it switches to mirrors automatically.
2. Install the Python packages, verify the injected runtime in `prebuilt\` (DLL and launcher, checked by SHA-256) and copy it into `bin\`.
3. Download AMAI and generate the strategy data the reference brain needs.
4. Open Farsight's home page, **Control Center**, at http://127.0.0.1:8866.

PowerShell is the Windows PowerShell 5.1 that comes with Windows, and the web UI ships prebuilt (`console\web\dist\`), so no Node.js is needed.
`start.bat` only fetches Node.js if you change the web UI source; run `start.bat node` if you want the website preview.

**Then set your Warcraft III folder at the top of Control Center** (**Find automatically**, or **Browse…**).
Farsight checks the game version and extracts the data it needs from your own copy of the game (Blizzard files are not distributed).
If the version isn't 1.27a you get a notice — please use 1.27.0.52240; more versions are coming, follow [War3AI.com](https://war3ai.com/en/).
Maps and next-game settings are relative to this folder (maps in any folder under `<game folder>\Maps` can be picked);
change it later on the **Settings** page.

After that, every double-click runs a one- or two-second check and opens Farsight. Starting games, switching AIs, the gateway,
chat bubbles, the local LLM and the website preview are all in Farsight — no other scripts to hunt for.
On startup Farsight asks War3AI.com whether there is a new version; problems and suggestions go to us from its **Feedback** page.

The black window closes by itself after a few seconds: Farsight keeps running in the background, and closing the browser doesn't stop it.
**To stop everything**, double-click `stop.bat` or click **Stop all** at the top right of Control Center — game instances
(games, farm, AI, director), the gateway, chat bubbles, the website preview, the local model this system loaded and the Farsight backend
are stopped in order. MCP servers belong to their clients (Claude and others) and are left alone; LM Studio itself is not closed.

```bat
start.bat              :: check the setup + open Farsight
start.bat setup        :: full check: reinstall the Python packages, retry AMAI
stop.bat               :: stop everything (= start.bat stop); stop.bat --keep-llm keeps the local model in VRAM
start.bat restart      :: restart only the Farsight backend (games and services keep running)
start.bat node         :: also fetch a private Node.js (only the website preview needs it)
start.bat 5 6          :: also start tests on instances 5 and 6 (game + reference AI)
```

Start a game from the command line and let an example bot take over (`python` is the one recorded in `openwar3.json`;
the one `start.bat` installed is `bin\env\python\python.exe`):

```bash
python tools/play.py --bot brains/examples/hello_bot.py
```

## Write your own AI

```python
from openwar3 import Bot

class MyBot(Bot):
    def on_tick(self, g):                     # about 5 times a second
        for w in g.idle_workers():
            g.gather(w, g.nearest(g.gold_mines(), w))
        for hero in g.my_heroes():
            foe = g.nearest(g.enemies(fighters_only=True), hero)
            if foe and g.dist(foe, hero) < 800 and g.cooldown(hero, "AHtb") == 0:
                g.cast(hero, "thunderbolt", target=foe)      # Storm Bolt
```

- **Can't program?** Describe your strategy and let an LLM write the bot: [Write a bot with an LLM](https://war3ai.com/en/docs/ai-bot/).
- **Writing a bot:** start with [Your first bot](https://war3ai.com/en/docs/first-bot/) and the [mental model](https://war3ai.com/en/docs/concepts/),
  then add tricks one by one from the [cookbook](https://war3ai.com/en/docs/cookbook/). Every API is listed in the [API catalog](https://war3ai.com/en/api/).
- **Examples, simple to complex:** `hello_bot` (economy) → `rush_bot` (army) → `macro_bot` (macro) → `micro_bot` (micro + creeping);
  game-mode mods: `mod_hero_roguelike` (hero roguelike) and `mod_endless_defense` (endless defense), all in `brains/examples/`.
- **Other languages:** use the [gateway](https://war3ai.com/en/docs/gateway/) (WebSocket / JSON, one click in Control Center);
  LLM agents connect through [MCP](https://war3ai.com/en/docs/mcp/) (`tools/war3_mcp.py`).
- **Schemes:** a finished bot becomes a scheme — switch schemes with one click on Farsight's **AI Schemes** page,
  export them as a zip, import other people's to test them ([Schemes](https://war3ai.com/en/docs/schemes/)).
- **Beyond melee:** in RPG / custom maps you can give players a **companion** (follows, fights, heals, chats, can use a local LLM),
  built on a channel that calls any of 1291 JASS natives by name
  ([Companion](https://war3ai.com/en/docs/companion/), [JASS](https://war3ai.com/en/docs/jass/), example `brains/examples/buddy.py`).

## Documentation

English documentation is on the website: **https://war3ai.com/en/docs/**. The `docs/*_ZH.md` files in this repository are the Chinese originals:

| In the repository (Chinese) | What it covers |
|---|---|
| [Bot handbook](docs/BOT_HANDBOOK_ZH.md) | Running in five minutes, the mental model, finding APIs by what you want to do, fifteen rules, debugging and performance |
| [Pro cookbook](docs/PRO_COOKBOOK_ZH.md) | 21 recipes with code: worker saturation, queue one at a time, never supply-blocked, scouting memory, creeping at night, focus fire, pulling back the wounded… |
| [AI schemes](docs/SCHEMES_ZH.md) | Switching schemes, exporting / importing (trust and safety), match statistics |
| [Canvas](docs/CANVAS_ZH.md) | Drawing on the game screen at runtime: text boxes, panels, progress bars, images, rings and paths on the ground |
| [UI & input](docs/UI_INPUT_ZH.md) | Clickable buttons and tabs, hotkeys, picking a point on the ground, what the mouse is over, selection; new events |
| [Game-mode mods](docs/MODS_ZH.md) | A scheme can be a set of rules (`kind: mod`): hero roguelike and endless defense examples |
| [Gateway](docs/GATEWAY_ZH.md) | WebSocket / JSON for any language, browsers and remote machines; JS client and demo page |
| [MCP](docs/MCP_ZH.md) | MCP server: agents such as Claude look at the game, give orders, ask the player, take screenshots |
| [JASS API](docs/JASS_API_ZH.md) | Call any of the game's 1291 JASS natives from outside: Farsight console, CLI, HTTP, Python |
| [Farsight i18n](docs/I18N_ZH.md) | Chinese / English UI; how to translate and how to add a language |
| [RPG helpers & companion](docs/COMPANION_ZH.md) | The JASS channel, spawning units, alliances, custom-map unit names, the companion framework |
| [Architecture](docs/ARCHITECTURE_ZH.md) | Layers, directories, why it is low-latency, the five API tiers |
| [API catalog](docs/API_ZH.md) / [api.json](docs/api.json) | Test status and underlying mechanism of every API (generated from the code) |
| [W3P protocol](docs/W3P_PROTOCOL_ZH.md) | The full contract between the runtime and external programs (shared-memory layout, opcodes, receipts) |
| [Arena](docs/ARENA_ZH.md) | Letting different players' AIs fight each other: referee, fog-of-war filtering, protocol |

## Repository layout

```
start.bat      the only entry point: set up from scratch + open Farsight (logic in setup.ps1, runs on Windows PowerShell 5.1); stop.bat stops everything
prebuilt/      release binaries of the injected runtime (render_hook.dll / war3_launch.exe / war3_ui_command.exe) + SHA-256 manifest
sdk/python/    SDK: w3world world state and events / w3fast fast lane / w3canvas canvas / w3input input / w3cmd verified recipes /
               w3claim arbitration / w3chan control channel (fallback) / w3state legacy snapshot
               openwar3/  public facade: Game + Bot (start here)
               w3paths.py the single source of paths
brains/        decision layer
  xwar3/         reference brain: strategy (seconds) + reflex (milliseconds, 4 processes) + worldmodel (win-probability model)
  examples/      hello_bot, rush_bot, macro_bot, micro_bot, buddy (companion), mod_hero_roguelike / mod_endless_defense (mods)
console/       Farsight web console (FastAPI + React; the built UI in web/dist/ is committed — after changing the source, npm run build and commit both)
gateway/       gateway (WebSocket / JSON, any language) + clients/js client and demo page
director/      automatic camera direction, health bars above units
speech/        in-game chat bubbles + local LLM (LM Studio)
runtime/       farm.py: multi-instance orchestration (restarts each game from next_game.json)
data/          order-ids.txt; tools/ extract data from your game; game/ (extracted data, not in git)
tools/         run_tests.py, play.py, run_scheme.py (scheme runner), war3_mcp.py (MCP), i18n_check.py
VERSION        current version (Farsight compares it with war3ai.com/version.json)
bin/           runtime binaries + per-instance runtime data + the private Python (and Node.js when needed) in bin/env/; not in git
```

## Tests

```bash
python tools/run_tests.py          # sdk / reference brain / reflex layer / console / chat bubbles / examples / gateway, one subprocess each
python tools/run_tests.py gateway  # only suites whose name contains "gateway"
```

None of these needs the game.

## Third-party software and trademarks

- [AMAI](https://github.com/SMUnlimited/AMAI) has its own license; `start.bat` downloads it and generates the reference brain's
  strategy data locally, and the generated files are not committed.
- Warcraft III is a trademark of Blizzard Entertainment. This project is not affiliated with or endorsed by Blizzard.

## License

The source code is licensed under the [Apache License 2.0](LICENSE). The runtime binaries in `prebuilt/` are not covered by it:
they are free to use and may be redistributed unmodified together with this project, but no source code is provided. See [NOTICE](NOTICE).
