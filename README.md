<sub>Images: Naruto Shippuden © Masashi Kishimoto / Shueisha · TV Tokyo · Pierrot (via Narutopedia).</sub>

# Coreshell

One unified menu and launcher over stock MiSTer cores, by f3bandit.

![傀儡の術: every stock core on Coreshell's strings](https://static.wikia.nocookie.net/naruto/images/a/a1/Akahigi_Hyakki_no_S%C5%8Den.png/revision/latest?cb=20150907032051)

*傀儡の術 (kugutsu no jutsu): every stock core on Coreshell's strings. Ask it yourself:
`{"v":1,"cmd":"kugutsu_no_jutsu"}`*

Every MiSTer core carries its own menu (its `CONF_STR`). Coreshell takes
that menu out of the core's hands: each system gets a **profile** (a settings
template) and Coreshell turns the user's choices into what Main already
understands — the core's `.CFG`, an MGL file and one `load_core` command. The
cores stay exactly as Update All installs them. Nothing is compiled per core
and no Main_MiSTer code is in here.

```
frontend (Console Mode, Phosphor)  --socket-->  Coreshell  --files + MiSTer_cmd-->  Main  -->  core
```

## How settings reach the core

| mode  | when | how |
|-------|------|-----|
| handoff | the installed `.rbf` is the profile's build, and the frontend's Main has Coreshell's handoff (Phosphor's Main; see [Console Mode](docs/CONSOLE_MODE.md#your-main-the-handoff)) | every setting goes in the launch's MGL as status bits and Main applies it in memory over the core's `.CFG`; the user's `.CFG` and `MiSTer.ini` are only ever read |
| exact | the profile's build, a Main without the handoff (a stock MiSTer with no frontend) | every setting is written into the core's `.CFG`; Main loads it at core start |
| name  | newer/unknown build, and the Main that runs it has MGL options (upstream, after Oct 20 2026) | up to 16 settings sent as `<option name= value=>`; Main matches them by name against the live core |
| core  | neither | the core starts with its own saved settings; nothing is written, the launch says why |

So a core update can never put a setting on the wrong bit.

The Main that runs a core comes from `MiSTer.ini` (`[RA_*] main=MiSTer_RA`,
`[A0CD-*] main=MiSTer_Physical-CD`, ...), so RetroAchievements cores, the
physical-CD Main and the ARM ports are launched the same way as everything
else: Coreshell only writes files and sends a command.

## Profiles

Made two ways, both landing in `systems/`:

* **from source** — the daily Action (`tools/update_db.py`) takes `CONF_STR` from a core's
  repo at the commit that built its newest release (no SS1 needed), worked out as Quartus would:
  the build's macros (the `.qsf` and the Tcl it sources), `` `ifdef ``, localparams and `? :`
  (`coreshell/vconst.py`). What it can't read goes to `needs_dump.json`.
* **from the live core** — `tools/dump_confstr.py` runs on the MiSTer, loads
  each installed core and reads the menu string out of the running Main's
  memory (read-only, works on stock Main, forks and `MiSTer_RA` alike), with
  the core file's md5. `tools/profiles_from_dumps.py` turns the dumps into
  profiles. This covers closed-source cores and any menu the source can't
  give. To do it yourself and send it in: [Reading a core or a Main yourself](docs/MANUAL_DUMPS.md).

`systems/overrides/<profile>.json` hides options and sets defaults on top of
a generated profile:

```json
{"hide": ["Savestate Slot"], "defaults": {"Audio & Video/Aspect Ratio": "Full Screen"}}
```

Variants of one system live side by side: `snes.json` (stock),
`snes@ra.json` (odelot's RA core, launched with `<setname>RA_SNES</setname>`
like Update All's own MGLs), `psx@dualram.json`, ...

## Settings

`/media/fat/coreshell/settings/<system>.json` and per game
`settings/<system>/<rom file>.json`, as `{"Page/Option": "Value"}`.
Backups of `.CFG` files and replaced saves go to `/media/fat/coreshell/backups`.

## API (v1)

Unix socket `/tmp/coreshell.sock`, one JSON object per line:

```
{"v":1,"cmd":"systems"}
{"v":1,"cmd":"menu","profile":"snes","game":"Super Metroid (Japan, USA).sfc"}
{"v":1,"cmd":"set","profile":"snes","key":"Audio & Video/Aspect Ratio","value":"Full Screen"}
{"v":1,"cmd":"launch","profile":"snes","rom":"/media/fat/games/SNES/Super Metroid (Japan, USA).sfc"}
```

`hello`, `systems`, `menu`, `set`, `reset`, `plan`, `launch`, `saves`, `sync`, `stale`, `refresh`, `caps` (below).
Disc drive games have their own profiles (`psx@disc`...), settings saved in a core's own OSD come back into
Coreshell's, and debug options are left out of the menu (docs/CONSOLE_MODE.md). With Super
MemCard, `menu` also carries its Card entry (see below). A request for
another API version is refused, never guessed at.

## Menus for what's installed (caps)

Build every menu into the frontend and show each once what it needs is here. A frontend can ship its own music player,
disc screen or DVD row with nothing installed; when a user later installs the add-on that feeds it (MiSTer DVD, MiSTer
Physical Disc, Zaparoo, a port) or plugs in the hardware (the dock's drive), the menu shows on its own, with no rebuild
and nothing merged.

```
{"v":1,"cmd":"caps"}
-> {"ok":true,"caps":{"disc_drive":{"present":true,"kind":"hardware",...},
    "dvd_video":{"present":true,"kind":"addon","by":"owenb321 (MiSTer_DVD)","needs":["disc_drive"],
                 "launch":{"rbf":"/media/fat/_Other/DVD_20261102.rbf"},"via":[...]}, ...}}
```

Each capability is an entry in `caps/` (git-stored, like `addons/`; a dev adds one without touching code): what to
look for (a file, a glob, a MiSTer.ini section, a USB id), what it needs, and the core to load. Coreshell looks every
10 s and sends the event `caps` with the ones that changed; frontends that read files can use `state/caps.json`.
A capability's core is loaded the stock way (Main, and MiSTer.ini's own `[SECTION] main=` for add-ons that bring
their Main).

## One owner of the screen

Whatever needs the screen gets all of it: a script in MiSTer's terminal, an add-on that takes the system over
(DreamSTer), another console (Update All's device check on tty7), MiSTer's own menu. The frontend and everything it
drives yield completely until that's done: it draws nothing (it follows the console actually shown, not the order
the switch signals arrive in), its FPGA layer (the offload module's background, alpha map and TV look) goes
neutral, its menu button and leftover presses are dropped, and the controllers type into that console as on stock
MiSTer (the Mains: Main as stock after F9). It gets the screen back when that's done and draws everything again.
Phosphor's shell does this (frontend/src/main.c, fxmenu.c `fxm_yield`/`fxm_resume`); `display/csdisplay.c`'s
`csd_vt_*` calls give any frontend the console part.

## Older Mains and MiSTer.ini (the CEC errors)

Old Main forks (the Quake, Duke3D and 3S-ARM ports' own Mains) show "<option>: unknown option" for every newer
MiSTer.ini line they don't know, like the HDMI CEC options, for 5 seconds as the game starts. The user's
MiSTer.ini is never edited for them:

* **With a frontend Main that has the handoff** (Phosphor's), the frontend's Main stays in charge: the ports run
  inside it or on its own up-to-date build of their Main, which know every option. No errors, nothing to hide.
* **Otherwise** Coreshell shows that old Main, just while it starts, a view of the active INI without the lines it
  doesn't know (a bind mount over the file: the file on the SD card is never written, and a reboot drops the
  mount), and takes the view away once that Main is up. Only lines at Main's default value are left out; a real
  setting is never touched. What each Main doesn't know is read from its binary and kept per Main in
  `state/ini_fixes.json`.

Tested on the SS1 (Nov 13 2026) with every file the stock Update All one (md5-checked against its databases),
launched from the stock Main:

| port | launched directly | launched through Coreshell |
|------|-------------------|----------------------------|
| Quake (`MiSTer_Quake`, neofreno, 20260614) | CEC errors | none |
| Duke3D (`Mister_duke3d`, neofreno, 20260615) | CEC errors | none |
| 3S-ARM (`MiSTer_3S-ARM`, kimchiman52, v20260417) | CEC errors | none |

`ini-check` lists which INI lines each installed Main would complain about (`--undo` puts back lines an older
Coreshell commented out; nothing comments them out any more):

```
python3 -m coreshell ini-check [--undo]
```

## RetroAchievements

As on any MiSTer: odelot's `MiSTer_RA` and RA cores run the achievements (his login, in `retroachievements.cfg`),
and the RA Viewer add-on (Anime0t4ku's) has the user's progress. Coreshell keeps the add-on hidden and gives the
frontend its data (`raviewer.py`: `ra_viewer`), fetched again by the add-on's own code while a game runs on
MiSTer_RA (the game: the user's last played on RetroAchievements, asked a few seconds after `/tmp/CORENAME` says a
core started) and when it ends. Nothing reads from inside MiSTer_RA. It sets the account up for the frontend and the PC app: the login checked with
RetroAchievements' own login, the Web API key with the web API, then the RA Viewer's `config.ini` written as the
user would.

## Games from the disc drive (discfs)

`discfs/` (C, static, no libfuse) serves the disc in the drive as an ordinary `<name>.cue` and `<name>.bin` under
`/tmp/coreshell/disc`, reading the drive's raw sectors (READ TOC / READ CD) on demand. A `<system>@disc` launch
mounts it, names it after the game on it (PS1: volume and serial, `WIPEOUT2 (SCUS-94351)`; Saturn and Mega CD:
title and product number), and launches the system's own profile with the cue. The frontend's Main runs it like a
game on the SD card, with Coreshell's settings, Super MemCard and the in-game menu: no `MiSTer_Physical-CD`.
A game from the SD card unmounts it. Installed at `coreshell/bin/discfs`; `discfs --toc` prints the disc's tracks.
A disc swapped during a game is seen at the next launch from the drive.

## Display sets

Each INI MiSTer can switch to keeps its own settings layer, so CRT and HDMI
can have different video options. Coreshell reads the active INI the way
Main does (its shared-memory slot), and `displays.json` can group INIs:
`{"MiSTer_RGsB.ini": "crt", "MiSTer_YPbP.ini": "crt", "MiSTer.ini": "hdmi"}`.

## Super MemCard

Super MemCard's own entries used to mean building every core with them in its
menu. Coreshell adds them to the unified menu instead, so stock cores from
Update All work with the card as they are.

What you need on the MiSTer:

- smcd running (`/tmp/smcd.sock`) and a Super MemCard card in a front port.
- A Main with the Super MemCard shim (the test bench's). It's the only way to
  the card: it passes the front ports to smcd and tells smcd when a game starts
  and when the menu is back, also for games handed to MiSTer_RA or
  MiSTer_Physical-CD by `main=`. Coreshell doesn't tell smcd anything itself.

What you get: a **Super MemCard** page with a **Card** entry on every system the
card keeps saves for (NES, SNES, Game Boy, Mega Drive, N64, Saturn, ...; the
system is the profile's saves folder name). Its values are that system's cards
on the card, plus the first blank one marked `(new)`. Picking one makes the
system play from that card from now on; its saves move over, which can take
several seconds. PSX has no Card entry (it keeps its own cards), and nor do
systems smcd doesn't handle. The pick is kept on the card, not in Coreshell's
settings, so `reset` leaves it alone.

From the command line:

```
python3 -m coreshell menu nes                          # ... "Super MemCard/Card": values 1, 3, 2 (new)
python3 -m coreshell set nes "Super MemCard/Card" 3    # NES plays from card 3
python3 -m coreshell plan nes /media/fat/games/NES/game.nes   # "supermemcard": "NES"
```

Over the API it's an ordinary option (`"extra": "supermemcard"`, with a `cards`
list of games/newest per card); `plan` and `launch` answer `supermemcard`: the
system when the save goes to the card, `"own cards"` for PSX, `null` plus a
warning when the running Main can't reach the card. Errors: `card busy` while a
game of that system runs, `PSX keeps its own cards`. Details for frontends:
[docs/CONSOLE_MODE.md](docs/CONSOLE_MODE.md#super-memcard-page).

## Keeping up with Update All

Two parts, so a console never waits on the cloud:

- **On the console, `refresh`.** At every start (and on request) Coreshell
  checks each installed core against its profile by md5 (hashes are cached, so
  the check takes seconds). Cores Update All changed, and new cores with no
  profile yet, are dumped right there and their profiles rebuilt: a few
  seconds per core, only while the menu is up, and a game launch cancels it.
  Saved settings carry over (they're kept by option name). A core whose dump
  fails isn't retried until it changes again.
- **In the cloud, the daily Action.** It rebuilds what it can from source,
  then `tools/watch.py` checks every Main Update All installs, MiSTer Linux,
  and Main_MiSTer's source for each thing Coreshell relies on (load_core, MGL
  options and setname, the .CFG layout, /tmp/CORENAME, the active-INI slot,
  main=). When something changed it opens an issue in this repo (GitHub
  emails you): `[BREAKING]` when a saved setting would be lost or an
  interface Coreshell uses is gone.

Until a profile is rebuilt, games still launch: by option name when Main
supports MGL options, otherwise with the core's own settings, with a warning.

```
python3 -m coreshell stale       # what's out of date here
python3 -m coreshell refresh     # dump and rebuild it now
```

## A new kernel (self-healing display)

Frontends draw straight to Linux's framebuffer, and a MiSTer Linux update can change it. At every start Coreshell
asks the kernel how its display is set up (`coreshell/kernel.py`) and compares that with the last start where
everything worked:

- **Same:** nothing to do.
- **Values changed** (size, format, row length, address, the driver's parameters): Coreshell writes the new values to
  `state/kernel/display.json` and keeps them as the new known-good state. Frontends that open the screen through
  `display/` (csdisplay) draw correctly on the new kernel.
- **Something a frontend needs is gone** (the framebuffer, its mode setting, the memory set aside at boot, `/dev/mem`,
  `/dev/uinput`): the state becomes `degraded`, with a plain report. The frontend stays in charge. corehost passes the
  verdict on, and only keeps the screen on the OSD if the framebuffer itself can't be drawn on.

`kernel hold` stops Update All bringing that Linux back. It changes only `[mister] update_linux` in `downloader.ini`,
keeps a copy first, and `unhold` puts the old value back (ported from Phosphor's update-guard, and it leaves a hold
made by update-guard alone). corehost's kernel watch reports changes to MiSTer's kernel before a MiSTer Linux
carries them.

```
python3 -m coreshell kernel              # status: ok, adapted or degraded, and why
python3 -m coreshell kernel check        # check again now
python3 -m coreshell kernel accept       # the state now is the known-good one
python3 -m coreshell kernel hold|unhold
```

## Updating, and the developer command line

`python3 -m coreshell update` brings the whole install up to the latest release: code, core profiles, quirks, ARM
programs, and the shim build of every Main on the MiSTer. GitHub Actions make the releases and the shim builds; see
[Updating](docs/UPDATING.md) for how to set them up. `python3 -m coreshell status`, `mains scan` and `cores scan`, plus
the rest of the developer command line, are in [Console Mode](docs/CONSOLE_MODE.md#the-developer-command-line).

## Run

```
python3 -m coreshell serve                 # on the MiSTer (python3 is already there)
python3 -m coreshell plan snes /media/fat/games/SNES/game.sfc
python3 -m unittest discover -s tests      # tests, on any PC
```

## Credits

Coreshell is f3bandit's. It drives cores and Mains written by their own
authors — MiSTer-devel, odelot (RetroAchievements cores and `MiSTer_RA`),
neofreno (Quake, Duke3D), kimchiman52 (3S-ARM) and everyone whose cores Update
All installs — without changing or bundling them; and add-ons such as
Anime0t4ku's RA Viewer, kept as installed.

Copyright (c) 2026 f3bandit. Licensed under the [Apache License 2.0](LICENSE):
anyone may use, change, sell and ship it, in a product too, as long as they keep
the copyright lines and pass [NOTICE](NOTICE) on with it. Coreshell runs as a
separate program from the GPL Mains and cores it drives, isn't linked with them,
and incorporates no GPL source; core patches change a stock core only on the
user's device as it loads. Other people's work it uses, and their licences, are
in [NOTICE](NOTICE).
