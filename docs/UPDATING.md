# Updating Coreshell, and the GitHub Actions behind it

Coreshell updates itself from this repository's GitHub releases with one command. The update brings everything it
carries:

- the code and tools
- the core profiles (`systems/`)
- the quirks tables (`quirks/quirks.json`)
- the ARM programs (discfs, corepatch)
- the Mains manifest, and the shim build of every Main on the MiSTer

```
python3 -m coreshell update --check     what's there
python3 -m coreshell update             install the latest release, then fetch shim builds for this MiSTer's Mains
python3 -m coreshell update --mains     only fetch the shim builds (a new Main from Update All)
python3 -m coreshell update --rollback  put the install from before the last update back
python3 -m coreshell update --file coreshell.tar.gz   install a release archive copied over by hand
```

**What an update never touches:**
- `settings/` and `state/`
- `launch/`, `written/` and `dumps/`
- `mains/stock/` (the stock Mains Coreshell kept)
- the user's saves, `MiSTer.ini` and core `.CFG` files

**How an install is protected:**
- The install it replaces is kept in `backups/update/` (the last 3).
- A release has to load before it replaces anything.
- If anything goes wrong part way, the backup is put back.
- A profile this MiSTer re-dumped for a core newer than the release's is kept.

Three GitHub Actions workflows feed this:

| Workflow | When | What it makes |
|---|---|---|
| `release.yml` | you push a tag `vYYYY.MM.DD[.n]` (or run it by hand) | runs the tests, builds the ARM programs, and makes the release with `coreshell.tar.gz` and its `.sha256` |
| `shim_mains.yml` | every day at 08:41 UTC, or by hand | the shim build of every Main fork's new release, in the release `shim-mains` (each build plus `mains.json`) |
| `update_profiles.yml` | every day at 07:23 UTC | core profiles for cores Update All changed, plus an issue when a Main or Linux change would break Coreshell |

## Setting up the Actions (once)

1. **Let workflows write to the repository.** In the repository, go to Settings → Actions → General → Workflow
   permissions, and choose "Read and write permissions". The workflows make releases and upload builds.

2. **The shim's patch.** It lives in `f3bandit/super-memcard`, a public repository: the shim build checks it out with
   no token.

3. **Let the MiSTers download.** `core_shell` is public: a MiSTer reads its releases with no token. (A fork kept
   private needs a read-only token on each MiSTer, in `/media/fat/coreshell/settings/update.json`:
   `{"repo": "<owner>/<repo>", "token": "github_pat_..."}`; `settings/` is never touched by updates.)

4. **Make the first shim builds.** Go to Actions → Shim Mains → Run workflow, with forks `all`.
   - The first run builds every fork: about 30–60 minutes.
   - After that, a release already built is skipped (its stock md5 is already in `mains.json`), so a quiet day costs
     a few minutes.
   - A new shim (a change to the super-memcard patch): run it by hand with "rebuild" ticked.

5. **Release.** Tag and push:
   ```
   git tag v2026.11.20 && git push origin v2026.11.20
   ```
   Every MiSTer gets it at its next `python3 -m coreshell update`.

## When a shim build fails

A fork's new release can change the code the shim touches. The builder settles the conflicts it knows on its own:
- includes
- the OSD's "Saves" row, and its selection beside the fork's own rows
- the poll call and input timing

Anything else fails the workflow, and the workflow opens an issue (GitHub emails you) with the conflicts listed. To fix it:
- Add a rule to `_settle()` in `coreshell/devtools.py`, with a test in `tests/test_devtools.py`.
- Or change the patch in super-memcard.

Then run the workflow again. To try a fork on a PC first:
```
python3 -m coreshell mains build <fork> --patch <main-mister-super-memcard.patch>
```
That needs Linux with git, make, patchelf, and `arm-none-linux-gnueabihf-gcc` 10.2 on PATH.

## Adding a fork

Add an entry to `FORKS` in `coreshell/devtools.py` with:
- the repository
- the Main's file name on the SD card
- how it builds: `main` (a Main_MiSTer fork), `dvd`, `wrapper` (neofreno's ports) or `3s`
- the release file holding the fork's own build, so its md5 can be read

Then `python3 -m coreshell mains build <fork>`. The workflow picks it up from then on.

## Quirks

What Coreshell knows about particular cores that their own menus don't say is data in `quirks/quirks.json`, added to
the built-in tables in `coreshell/quirks.py`. A fix for an odd core can ship as a data change in the next release.

| Table | What it holds |
|---|---|
| `ra_console` | RetroAchievements console ids, by the RA core's file name |
| `ra_by_ext` | a game's extension that's another console on that core (`"NES/FDS": 81`) |
| `core_folders` | the folders Coreshell keeps core profiles of |
| `core_skip` | core files with nothing to set (menu, memtest, ...) |
| `card_no_picker` | systems the Super MemCard keeps memory cards for itself |

The rest of the per-core knowledge is data already, and comes with each update:
- each core's profile (from its own menu, matched by md5)
- the core patches (by the stock core's md5)
- the Mains manifest (by md5)

Two quirks live in the shim itself:
- A Main that replaced another one (Quake's or Duke3D's wrapper going back to the menu) waits on the processes it
  took over, so none are left as zombies.
- The shim is fitted into each fork's own menu.
