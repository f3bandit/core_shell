# Coreshell for Console Mode

Everything Console Mode needs to run any core through Coreshell: one socket,
JSON lines, no Coreshell or MiSTer code linked in.

## Start it

On the SS1, copy the repo to `/media/fat/coreshell` and `scripts/coreshell.sh` to
`/media/fat/coreshell/coreshell.sh`, then start it at boot, before Console Mode, from
`/media/fat/linux/user-startup.sh`:

```
# coreshell: BEGIN
[ "${1:-start}" = start ] && [ -x /media/fat/coreshell/coreshell.sh ] && /media/fat/coreshell/coreshell.sh start
[ "$1" = stop ] && [ -x /media/fat/coreshell/coreshell.sh ] && /media/fat/coreshell/coreshell.sh stop || :
# coreshell: END
```

`coreshell.sh start|stop|restart|status`. It serves with `--refresh-at-start` (see Core updates).

Socket: `/tmp/coreshell.sock`. Every request carries `"v": 1`; a Coreshell
that doesn't speak that version answers `{"ok": false, "api": <its version>}`
instead of guessing.

## Your Main: the handoff

The one change Console Mode's Main needs. With it, Coreshell never writes the user's files: every launch's
settings travel in the MGL Coreshell already sends, and your Main applies them **in memory, for that launch
only**. The user's core `.CFG` and `MiSTer.ini` are only ever read. Without it, Coreshell falls back to writing
the core's `.CFG` (fine on a stock MiSTer with no frontend, not on Console Mode). Phosphor's Main has had it
since Nov 13 2026; this is the same code.

**1. Two MGL tags** (a Main without the handoff ignores them):

```xml
<mistergamedescription>
	<rbf>_RA_Cores/Cores/NES</rbf>
	<setname same_dir="1">RA_NES</setname>
	<file delay="1" type="f" index="0" path="/media/fat/games/NES/Zelda.nes"/>
	<status mask="2e06ffdffff33f003f00000000000000" value="06000000000000000000000000000000"/>
	<setting key="vsync_adjust" value="2"/>
</mistergamedescription>
```

* `<status mask= value=>`: the core's settings, the 16-byte status block (bytes in hex, byte 0 first, the `.CFG`
  layout). Each bit set in `mask` takes its value from `value`; every other bit stays what the user's `.CFG`
  has. Applied after Main loads the `.CFG`, before `user_io_status_set("[0]", 1)` sends the status to the core.
  No 16-option limit and no name look-ups: one tag carries every setting.
* `<setting key= value=>`: an INI option for this launch, read after the active INI on every `cfg_parse()`
  (so a later re-parse keeps it), one tag per option. From the profile's `"ini"` entry: a core that needs, say,
  its own `vsync_adjust` gets it without touching the user's MiSTer.ini or any other core.

**2. In your Main** (Phosphor's diff, five files):

* `support/arcade/mra_loader.h`: in `struct mgl_struct`, `char cs_ini[2048]; unsigned char cs_mask[16],
  cs_value[16]; int cs_status;` and `void mgl_apply_coreshell_status(unsigned char *status, int size);`
* `support/arcade/mra_loader.cpp`, in `scan_mgl()` beside `<option>` (MGL only, not MRA): `<setting>` appends
  `key=value\n` to `cs_ini` (no `=` or newline in the key); `<status>` hex-decodes `mask`/`value` into
  `cs_mask`/`cs_value` and sets `cs_status`. And:

```c
void mgl_apply_coreshell_status(unsigned char *status, int size)
{
	if (!mgl.cs_status) return;
	for (int i = 0; i < size && i < (int)sizeof(mgl.cs_mask); i++)
		status[i] = (unsigned char)((status[i] & ~mgl.cs_mask[i]) | (mgl.cs_value[i] & mgl.cs_mask[i]));
	printf("Coreshell handoff: the core's settings applied (its .CFG untouched)\n");
}
```

* `cfg.cpp` / `cfg.h`: `cfg_set_overrides(const char *lines)` keeps a copy; at the end of `cfg_parse()`, after
  the `ini_parse()` passes, each line goes through the same `ini_parse_var()` the INI's lines do.
* `user_io.cpp`, `user_io_init()`: just before `cfg_parse(); cfg_print();`

```c
if (xml && isXmlName(xml) == 2) cfg_set_overrides(mgl_parse(xml)->cs_ini);
else cfg_set_overrides(NULL);
```

  and where the core's `.CFG` was just loaded (`FileLoadConfig(name, cur_status, ...)`), before
  `mgl_apply_options()`:

```c
if (xml && isXmlName(xml) == 2) mgl_apply_coreshell_status((unsigned char *)cur_status, sizeof(cur_status));
```

**3. How Coreshell knows:** it looks for the string `Coreshell handoff: ` in the running Main's binary (the
`printf`s above put it there). Nothing to configure.

**4. Keep your Main in charge.** A `main=` line in MiSTer.ini hands a core to another Main, and that Main
doesn't have the handoff. Phosphor's Main keeps itself for those, and Console Mode's should too:

| `main=` | what Phosphor's Main does |
|---------|---------------------------|
| `MiSTer_RA` (odelot) | runs the RA cores and their achievements itself (its own login, `retroachievements.cfg`), as on any MiSTer |
| `MiSTer_Quake`, `Mister_duke3d` | runs the ports itself (their games started from Main, the wrappers' job done from their code) |
| `MiSTer_3S-ARM`, `MiSTer_SonicMania` | its own build of those Mains from its own source (with the handoff), for the release it was tested with; a newer game gets the game's own Main back |

A Main you hand off to that's older than an INI option shows "unknown option" (the CEC errors). If it does run,
Coreshell shows it a view of the INI without those lines just while it starts (README: "Older Mains and
MiSTer.ini"); tested with the stock Quake, Duke3D and 3S-ARM Mains: errors directly, none through Coreshell.

## The five calls Console Mode needs

**Which systems can I show?**
```
-> {"v":1,"cmd":"systems"}
<- {"ok":true,"systems":[{"profile":"snes","system":"snes","variant":"stock",
     "name":"SNES","installed":true,"rom_exts":["SFC","SMC","BIN","BS"],
     "games_dir":"games/SNES"}, {"profile":"snes@ra","variant":"ra", ...}, ...]}
```
`variant` is `stock`, `ra` (RetroAchievements core), `dualram`, `other`...
List the variants as "core version" choices for the same system.

**Draw the settings menu** (system-wide, or for one game):
```
-> {"v":1,"cmd":"menu","profile":"snes","game":"Super Metroid (Japan, USA).sfc"}
<- {"ok":true,"menu":[{"key":"Audio & Video/Aspect Ratio","page":"Audio & Video",
     "name":"Aspect Ratio","values":["Original","Full Screen","[ARC1]","[ARC2]"],
     "value":"Original","default":"Original","per_game":false}, ...]}
```
Group by `page`, show `name`, cycle through `values`. `[ARC1]`/`[ARC2]` are
the user's custom aspect ratios from MiSTer.ini.

**User changed a setting:**
```
-> {"v":1,"cmd":"set","profile":"snes","key":"Audio & Video/Aspect Ratio","value":"Full Screen"}
-> {"v":1,"cmd":"set","profile":"snes","key":"...","value":"...","game":"<rom file name>"}   (this game only)
-> {"v":1,"cmd":"reset","profile":"snes","game":"<rom file name>"}                           (back to system settings)
```

**Play:**
```
-> {"v":1,"cmd":"launch","profile":"snes","rom":"/media/usb0/games/SNES/Super Metroid (Japan, USA).sfc",
     "save":"/optional/path/to/chosen.sav"}
<- {"ok":true,"plan":{"mode":"exact","main":"MiSTer","warnings":[], ...}}
```
Coreshell writes the core's settings, puts the save in place (the old one is
backed up), picks the right slot for the file (cart vs. CD on the same core),
and has Main load it. RA cores go to `MiSTer_RA` through MiSTer.ini exactly
like Update All's own RA shortcuts.

**Before launching, to show a warning:** `{"v":1,"cmd":"plan", ...same as launch}`
does everything except launch. `mode` is:

| mode | meaning |
|------|---------|
| `exact` | profile matches this exact core build: all settings applied |
| `name`  | newer core, Main supports MGL options: changed settings applied by name |
| `core`  | newer core, older Main: the core starts with its own settings (show `warnings`) |

## Super MemCard page

When smcd runs and the card is in a front port, `menu` adds one entry to every system Super MemCard keeps saves
for (NES, SNES, Game Boy, Mega Drive, N64, Saturn...), on its own page — no core is built for it:

```json
{"key": "Super MemCard/Card", "page": "Super MemCard", "name": "Card", "values": ["1", "3", "2 (new)"],
 "value": "1", "extra": "supermemcard", "system": "NES",
 "cards": [{"card": 1, "games": 0, "newest": 0, "current": true, "new": false}, ...]}
```

Draw it like any other option. `set` with that key picks the card the system plays from (`{"card": 3}`); it can
take up to a few seconds while that system's saves move over, so don't block the screen on it. A system's game
running answers `card busy`. PSX has no Card entry (it keeps its own cards), nor does a system smcd doesn't know.

`plan` / `launch` answer `supermemcard`: the system's name when the game's save comes from and goes to the card,
`"own cards"` for PSX, `null` with a warning when the Main running now can't reach the card (only a Main with the
Super MemCard shim passes the front ports; it tells smcd about every game itself, also one handed to MiSTer_RA or
MiSTer_Physical-CD by `main=`).

## PSX controller setup (Auto)

The PSX menu has `Controllers/Setup` (`Manual` / `Auto`, Coreshell's own: `"live": false`, so don't hand it to the
running core; it counts from the game's next start). On Auto, a launch gives each port the controller type the game
takes and SNAC MemCard by what's in the front ports, for that launch only (the .CFG is never written).

While that game runs, `menu` (with the game's `rom`) shows Pad1 / Pad2 / SNAC MemCard as the core runs them, the
user's own setting in their `note` (`note_plain`: the note to show once the user changes it). Coreshell asks the running Main (`opt` on `/tmp/phosphor-main.sock` or
`/tmp/corehost.sock`), so a change in the core's own menu counts too. Under a Main without that API it goes by what
Auto gave the core, and by what you tell it: when the user changes one of them in your in-game menu and you give the
running core the change too, say so with `"live": true`:
```
-> {"v":1,"cmd":"set","profile":"psx","key":"Pad1","value":"Analog","rom":"<the game's path>","live":true}
```
Without `live` the change is saved for the next start and the menu keeps showing what the core runs.

## Core updates (refresh)

After Update All changes cores, Coreshell rebuilds their profiles on the console itself: it starts doing so
15 s after the service starts, while the menu is up. Doing that, it loads each changed core for a few seconds,
so the screen shows those cores briefly.

- `{"v":1,"cmd":"hello"}` carries `"refresh": {"state": "idle|running|done|cancelled|skipped", "done": 2,
  "total": 5, "core": "_Console/SNES", "updated": [...], "failed": [...]}`: show "Updating core menus 2/5"
  while it's `running` if you like.
- `{"v":1,"cmd":"stale"}` lists what's out of date; `{"v":1,"cmd":"refresh"}` starts it (for example after
  Update All finishes, behind your own progress screen); `{"v":1,"cmd":"refresh","cancel":true}` stops it.
- `launch` cancels a running refresh first: a game always wins.
- If you'd rather run it only on demand, start the service without `--refresh-at-start`.

## Disc drive games

Update All's Physical-CD MGLs (`_Physical Disc Cores/*.mgl`) become profiles of their own: `psx@disc`,
`saturn@disc`, `megacd@disc`, `turbografx16@disc`, `neogeocd@disc`, `3do@disc`, `cdi@disc`, `mdplus@disc`,
`snes@disc` (MSU-1). They're the stock core's menu under the `A0CD-<name>` set name, so they keep their own
settings, and MiSTer.ini's `[A0CD-*] main=MiSTer_Physical-CD` runs them. `systems` marks them `"disc_drive": true`:
launch with no `rom`, the game comes from the drive:

```
-> {"v":1,"cmd":"launch","profile":"psx@disc","game":"SCUS-94351"}
```

`game` is optional: pass the disc's ID (as your disc detection reads it) to give each disc its own settings.

## Saves

```
-> {"v":1,"cmd":"saves","profile":"snes","rom":"/media/fat/games/SNES/Super Metroid (Japan, USA).sfc"}
<- {"ok":true,"saves":[{"path":"/media/fat/saves/SNES/Super Metroid (Japan, USA).sav","from":"current",
     "time":1763000000,"bytes":8192,"name":"..."}, {"from":"backup",...}, {"from":"library",...}]}
```

`current` is the save Main loads; `backup` is one Coreshell replaced earlier; `library` is any file in
`coreshell/saves/<system>/<game name>/` (a save copied from a PC, say). Launch with one's `path` as `save` and
it's put in place, the current one backed up first. Super MemCard saves are picked with its Card entry.

## Settings changed in the core's own menu

If the user opens the core's OSD in game and picks "Save settings", the core's `.CFG` changes. The next `menu`,
`plan` or `launch` for that system picks those changes up into Coreshell's settings (into the layer each came
from: the game's, the display's, else the system's), so both menus agree. `{"v":1,"cmd":"sync","profile":"psx"}`
does it on demand. Only after a launch Coreshell handed (or, without the handoff, wrote) settings for, and only
on the profile's own core build. With the handoff a `.CFG` the user never saved is left alone: only a real "Save
settings" is taken.

## Debug options

Developer aids (a "Debug" page, debug overlays, test patterns) are left out of `menu` on every core. A profile
override's `"show"` list brings any back (ZX Next's Machine and Issue are shown that way).

## Tools (the Scripts folder)

The stock menu's Scripts page, for your menu:

```
-> {"v":1,"cmd":"tools"}
<- {"ok":true,"tools":[{"name":"update_all.sh","title":"Update All: cores, Main, Linux and extras",
     "path":"/media/fat/Scripts/update_all.sh","interactive":false,"refresh_after":true},
     {"name":"wifi.sh","title":"wifi","interactive":true,...}, ...]}
```

- **On the TV, like the stock menu** (any script, interactive ones included): your frontend runs it through
  your Main the way MiSTer's Scripts menu does (Linux terminal on tty2, "Press any key to continue"). Only the
  frontend can: it's drawing on the screen, so it stops drawing and hands the console back first (text mode on
  tty1, or the kernel ignores the switch to tty2). Phosphor's shell does this with Phosphor's Main's
  `script <path>`. When it ends, tell Coreshell:
  `{"v":1,"cmd":"tool_done","name":"update_all.sh","exit":0}`; after Update All, Coreshell refreshes the profiles
  of the cores it changed.
- **In the background** (scripts with `interactive: false`): `{"v":1,"cmd":"tool_run","name":"..."}`, then
  `{"v":1,"cmd":"tool_status"}` gives `{"tool":{"state":"running|done|failed|stopped","exit":0},"output":[...]}`,
  the last lines for you to show; `tool_stop` ends it.

## Switching INI (CRT / HDMI)

The OSD's INI choice:

```
-> {"v":1,"cmd":"inis"}
<- {"ok":true,"inis":[{"ini":"MiSTer.ini","active":true,"display":"hdmi"},
     {"ini":"MiSTer_RGHV.ini","active":false,"display":"crt"}, ...]}
-> {"v":1,"cmd":"set_ini","ini":"MiSTer_RGHV.ini"}
```

Done the way Main's OSD does it: the slot goes into Main's shared page and the menu core is loaded again, so
Main restarts reading that INI (video mode, scaler, everything). From the menu only. The list is the one Main
can switch to: MiSTer.ini and the first three `MiSTer_*.ini` Main finds. Each INI's settings set (`display`)
comes from `displays.json`.

## More than one file (second disk, memory cards)

```
-> {"v":1,"cmd":"slots","profile":"c64"}
<- {"ok":true,"slots":[{"id":"f1","label":"","exts":["PRG","CRT","REU","TAP"],"game":true},
     {"id":"s0","label":"Mount #8","exts":["D64","G64","T64","D81"],"game":true},
     {"id":"s1","label":"Mount #9",...}, {"id":"f8","label":"System ROM C64+C1541","game":false}, ...]}
-> {"v":1,"cmd":"launch","profile":"c64","files":{"s0":".../Disk 1.d64","s1":".../Disk 2.d64"}}
-> {"v":1,"cmd":"launch","profile":"psx","rom":".../Game.cue","files":{"s2":".../card1.sav"}}
```

Every file the core's menu can load or mount, by slot id (`f`/`s` + its index), each with its label and
extensions; `game` marks the slots a game goes in. `files` loads them in the same launch, next to `rom` (up to 6
files in all, Main's limit). Tested on the SS1: C64 with drives 8 and 9 mounted at once.

## Example (C, POSIX)

```c
int fd = socket(AF_UNIX, SOCK_STREAM, 0);
struct sockaddr_un a = { .sun_family = AF_UNIX };
strcpy(a.sun_path, "/tmp/coreshell.sock");
connect(fd, (struct sockaddr *)&a, sizeof a);
const char *req = "{\"v\":1,\"cmd\":\"systems\"}\n";
write(fd, req, strlen(req));
/* read until '\n', parse JSON */
```

## Add-ons (Zaparoo, MiSTer SAM, MiSTer Extensions, MiSTer Companion, MiSTer Monitor...)

Add-ons send Main commands on `/dev/MiSTer_cmd`. Coreshell has that FIFO (`coreshell/cmdpipe.py`): Main's own FIFO
gets a second name, `/dev/MiSTer_cmd_main`, which Main keeps reading, and Coreshell's goes in its place in one rename
(each time Main makes a new one: Main does when it starts a core). Your Main needs nothing for this. Coreshell's own
launches write `/dev/MiSTer_cmd_main`; when Coreshell stops, Main's FIFO gets its name back.

* `load_core` of a game's `.mgl` (Zaparoo's and MiSTer Extensions' `/media/fat/.LASTLAUNCH.mgl`, MiSTer SAM's
  `/tmp/SAM_Game.mgl`), an `.mra` or a core's `.rbf`: a Coreshell launch, like one from your frontend (the game's
  settings, RetroAchievements, Super MemCard). An MGL Coreshell can't launch (several files, no core)
  goes to Main as sent.
* every other command (`load_core` of the menu, `volume`, `screenshot`, `fb_cmd`...): to Main as sent.
* every launch (yours or an add-on's) is announced the way Main does when a file is picked with `log_file_entry=1`:
  `/tmp/FULLPATH`, `/tmp/CURRENTPATH`, then `/tmp/FILESELECT` = `selected`, and `/tmp/ACTIVEGAME` (emptied when the
  menu is back). Zaparoo's, MiSTer Extensions' and MiSTer Monitor's trackers read them: they know the game whoever
  started it.
* your frontend's own screenshot: `{"cmd": "frontend", "screenshot": {"socket": "/tmp/yours.sock", "send":
  "<the line it takes>"}}` (kept; `"screenshot": null` takes it away). An add-on's `screenshot` while the menu core is
  loaded (your frontend on screen) is sent there instead of to Main.

`serve --no-cmdpipe` leaves `/dev/MiSTer_cmd` to Main.

## What happened: events

Whatever started the game (your frontend, an add-on, Main's own menu, a stock Main without the shim), Coreshell tells
whoever asks (`coreshell/events.py`):

| event | when |
|---|---|
| `game_started` | a game starts: `game`, `system`, `rbf`, `by` (`frontend`, `add-on`, `main`) |
| `rom_loaded` | a file picked in a core's OSD (Main's card event): `game` (announced in `/tmp` too) |
| `game_ended` | the menu is back: `game`, `core` |
| `saves` | the Super MemCard synced the game's saves: `to` `sd` (starting) or `card` (the game over) |
| `core_changed` | `/tmp/CORENAME` changed (every Main writes it: a core nobody told Coreshell about is seen too) |

* `{"cmd": "events", "since": <seq>, "wait": <s>}`: the events after `seq`, waiting up to `wait` seconds (30 at most)
  for one; `{"events": [...], "seq": <last>}`.
* `{"cmd": "subscribe"}`: `{"ok": true, "seq": n}`, then each event as a JSON line as it happens, until you close.
* `/tmp/coreshell/events.jsonl`: the same, one per line (the last 500), to tail from a script.

A game Coreshell didn't start but can see (a file picked in an RA core's OSD with no game running, a game Main's own
menu started that Main wrote in `/tmp/FULLPATH`) gets its achievements too.

## Every Main carries the shim

Coreshell knows a game started, a file picked in the OSD, the menu back, and passes the Super MemCard's frames through
the shim in Main (super-memcard's `main/`). Phosphor's and Console Mode's Mains have it built in; for the others
Coreshell keeps a build of the same Main with the shim (`coreshell/mains.py`), in `/media/fat/coreshell/mains/`,
listed in `mains.json` by the stock build's md5. A Main starts itself again as the file MiSTer.ini's `main=` names
(`MiSTer` without one) on every core load, so the shim build is put at that path (the stock one kept in
`mains/stock/`), and again whenever an update puts a stock build back (looked at every 15 s). A Main running from a
stock build while the menu is up is started again from the shim build. A stock build with no shim build yet is left
as it is, and said (`main` event). `python3 -m coreshell mains restore` puts the stock builds back, and they stay until `python3 -m coreshell mains check`;
`serve --no-mains` leaves the Main files alone.

## What Coreshell never does

* change a core, Main, or MiSTer.ini (an older Main is shown a view of the INI while it starts; the file is never
  written);
* write the user's core `.CFG` when your Main has the handoff;
* write a setting to a core build it hasn't been matched to (`core` mode);
* overwrite a save without a backup in `/media/fat/coreshell/backups`.

## The developer command line

`python3 -m coreshell` on the MiSTer (the full list: `python3 -m coreshell status -h`):

| Command | What it gives |
|---|---|
| `status` | Coreshell, the Main running (and whether it has the shim), the core, the game, smcd, who has the command pipe, RetroAchievements |
| `events [--follow]` | game started / ended, saves, achievements, core changes (`--follow`: live) |
| `mains scan [--json]` | every Main on the SD card: md5, fork, version, shim or not, the stock build kept |
| `mains info <file>` | one Main |
| `mains probe <file> [--out <file.json>]` | what Coreshell relies on, looked for in that Main itself, for a Main the daily watch can't check from source ([Reading a core or a Main yourself](MANUAL_DUMPS.md#a-main)) |
| `mains check` / `mains restore` | the shim builds put in place now / the stock builds back, left alone until `mains check` |
| `cores scan [--stale]` | every core's profile (`--stale`: the ones out of date with their core) |
| `core info <rbf or id>` | a core's profile: options, slots, settings Coreshell keeps |
| `core profile <rbf>` | a core's profile built again from its own menu (loads the core, then the menu again; [Reading a core or a Main yourself](MANUAL_DUMPS.md#a-core)) |

On a PC (Linux with git, make, patchelf and `arm-none-linux-gnueabihf-gcc` 10.2 on PATH):

| Command | What it does |
|---|---|
| `mains forks` | the forks it can build: Main_MiSTer, MiSTer_RA (odelot), MiSTer_Physical-CD (Anime0t4ku), MiSTer_Zaparoo (Zaparoo Project), MiSTer_DVDcss (owenb321), MiSTer_Quake and Mister_duke3d (neofreno), MiSTer_3S-ARM (kimchiman52) |
| `mains build <fork or all> [--ref <tag>] [--patch <file>] [--out <dir>]` | the fork's release (the latest, or `--ref`) with the Super MemCard shim, and its `mains.json` entry (keyed by the release binary's md5). Copy both into `/media/fat/coreshell/mains/` and Coreshell puts it in place. |

The build applies `main-mister-super-memcard.patch` with `git apply --3way` and settles the conflicts it knows
(includes, the OSD's "Saves" row and its selection, the poll and input timing beside each fork's own); anything else is
listed for a person. Wrapper Mains older than the patch (Quake, Duke3D, 3S-ARM) get the same changes put in by hand.
The shim and the builds are GPL-3.0 like Main_MiSTer; Coreshell only runs git and make.
