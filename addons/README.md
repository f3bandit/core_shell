# addons: add-ons that take the system over, and how they come back to the frontend

Some add-ons stop Main and run their own program and menu (DreamSTer; a game port's own engine). On stock MiSTer they
come back to MiSTer's menu. Under a frontend, Coreshell makes them come back to that frontend, whichever it is,
without anything written for them in the frontend (`docs/stock-behaviour.md`):

- **Their own menu names the frontend.** Each file here says which texts in the add-on's menu name MiSTer
  ("Exit To MiSTer"). When the add-on is started from the Scripts menu, Coreshell runs a copy with those texts naming
  the frontend that registered with it ("Exit To Phosphor"). The installed add-on is never changed, so Update All
  keeps updating it. If a text isn't found (a new release), the add-on runs as it is.
- **"MiSTer" means the running Main.** `compat/killall` and `compat/pidof` are on the scripts' PATH, so an add-on that
  stops `MiSTer` stops whichever Main runs (MiSTer_phosphor, MiSTer_corehost...).
- **Back to the frontend's Main.** MiSTer.ini's `[MiSTer] main=` does it, as on stock. If stock MiSTer is still the one
  running in the menu 6 s later, Coreshell starts the frontend's registered Main in its place.
- **The way out, always.** While any add-on has the system (no Main running), holding the menu button (a pad's
  Home/guide, or F12) for 3 s stops it and brings the frontend back. This covers add-ons with no file here yet.

A frontend tells Coreshell who it is once: `{"cmd": "frontend", "name": "Phosphor", "main": "/media/fat/phosphor/bin/MiSTer_phosphor"}`.
`{"cmd": "addons"}` lists what's here, what's installed, and whether each menu change still matches. The events
`addon_started` and `addon_ended` (`how`: `exit` or `escape`) say when one runs.

## An entry

```json
{
 "name": "DreamSTer",
 "script": "DreamSTer.sh",
 "how": "what it does to Main and how it exits, for people",
 "menu": [{"from": "(\"exit\", \"Exit To MiSTer\")", "to": "(\"exit\", \"Exit To {frontend}\")"}],
 "checked": {"md5": "the release the texts were checked against", "on": "2026-11-16"}
}
```

`from` is the exact text in the add-on's source, with enough around it to be unique. Every `from` must be found, or
none is changed.

## Checked

| Add-on | Release (md5) | Tested on the SS1 |
|---|---|---|
| DreamSTer | `ac0ac2f0f082664afcea976508ecb97b` | Its menu said "Exit To Phosphor". Choosing it came back to Phosphor (via MiSTer.ini's main=). Home held 3 s stopped it and came back to Phosphor (Nov 16 2026). |
