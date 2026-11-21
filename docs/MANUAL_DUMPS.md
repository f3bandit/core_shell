# Reading a core or a Main yourself

The daily Action keeps Coreshell's core profiles and its checks on Main up to date from source. When it can't,
you can do the same on your own MiSTer and send the result in. Nothing here changes a core, a Main, MiSTer.ini or any
.CFG: the tools only load a core the stock way (`load_core`, as Main's own menu does) and read.

## When it's needed

- **A core:** the Action lists it in [`needs_dump.json`](../needs_dump.json), or its daily issue says a core
  "can't be read from source". The Action works a core's menu (`CONF_STR`) out of its source as Quartus would: the
  build's macros from the `.qsf` and the Tcl files it sources, `` `ifdef ``/`` `define ``, localparams and `? :`
  ([`coreshell/vconst.py`](../coreshell/vconst.py)). A core it still can't read (no public source, a macro with
  arguments, a menu put together by code) is read from the running core instead.
- **A Main:** the watch only checks Main_MiSTer's own source. A fork without public source, or one you built
  yourself, is checked from the program itself.

Your MiSTer doesn't wait for any of this: at each start Coreshell reads every core whose profile doesn't match it
(`python3 -m coreshell stale` lists them). These steps put the result in the repo, so everyone gets it.

## A core

On the MiSTer (SSH, or the console), with the core where Update All puts it:

```
cd /media/fat/coreshell
python3 -m coreshell core profile _Computer/PCXT_20261117.rbf
```

It loads the core, reads the menu Main got from it (out of Main's memory, read-only: it works the same on stock Main,
Phosphor's Main, MiSTer_RA and the other forks), brings the menu back, and builds that core's profile. You get:

- the dump: `/media/fat/coreshell/dumps/_Computer__PCXT_20261117.json` (the menu string, the core file's md5, which
  Main read it, when);
- the profile: `/media/fat/coreshell/systems/pcxt.json`.

Check it before sending it in:

```
python3 -m coreshell core info _Computer/PCXT_20261117.rbf    # name, .CFG, options, slots
```

The name must be the core's own (what its OSD shows), the `.CFG` must be the file Main saves its settings in, and the
options and file slots should be what the core's menu shows.

Several cores at once, or every core:

```
python3 tools/dump_confstr.py /media/fat/_Console/NES_20260823.rbf /media/fat/_Computer/PCXT_20261117.rbf
python3 tools/dump_confstr.py --all [--resume]
```

Then build profiles from just those dumps (copy them to a folder of their own first: the dumps folder keeps older
builds' dumps too):

```
python3 tools/profiles_from_dumps.py <folder with the dumps>
```

**Where the core sits matters.** The folder sets the profile's variant: `_Console`/`_Computer`/`_Utility` stock,
`_RA_Cores` RA, `_Console (Dual SDRAM)` dual RAM, anything else "other". To read a core you don't want to install, put
it anywhere and, before building the profile, set the dump's `"rbf"` to where Update All installs it
(`"_Computer/PCXT_20261117.rbf"`). The md5 is of the file itself, so it still matches.

### Sending it in

Open a pull request on this repo with:

- `systems/<profile>.json` (built as above; `systems/overrides/` changes are separate and by hand);
- the core taken off `needs_dump.json`;
- the dump itself, in the pull request's description or as an attachment, so it can be checked.

Or open an issue and attach the dump. Run `python3 tools/update_db.py --dry-run --only <core>` on a PC with this repo:
it should no longer list the core as needing a dump.

## A Main

On the MiSTer:

```
cd /media/fat/coreshell
python3 -m coreshell mains probe /media/fat/MiSTer_SomeFork --out /tmp/probe.json
```

It reports the Main (md5, fork, version, whether it carries the Super MemCard / Coreshell shim) and looks inside the
program for each thing Coreshell relies on, the same list the daily watch checks in Main_MiSTer's source
([`tools/watch.py`](../tools/watch.py) `PROBES`): `load_core` on the command pipe, `/dev/MiSTer_cmd`, MGL `<setname>`
and `same_dir`, `<option>`, the `.CFG` name and its `_v%d` suffix, `/tmp/CORENAME`, `main=`. `ok` on every line: the
Main has everything Coreshell uses. A `MISSING` line names what to look at in that Main; the exit code is 1.

A string being there doesn't prove the feature works, so try it too: a game launched through Coreshell under that
Main (`main=` in the core's MiSTer.ini section), and back to the menu.

Send the report (`/tmp/probe.json`) in an issue on this repo. For a Main Coreshell should build with the shim, see
`mains forks` and `mains build` in [Console Mode](CONSOLE_MODE.md#the-developer-command-line); corehost's own Main is
in [corehost](https://github.com/f3bandit/corehost#when-the-watch-cant-tell).
