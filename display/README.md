# csdisplay: a frontend's screen, as the kernel says it is now

A frontend (Phosphor, Console Mode, any other) draws straight to Linux's framebuffer. Nothing about that framebuffer
is a versioned interface: a MiSTer Linux update can change its size, format, row length, address or the driver's
parameters (5.15 to 6.18, Sept 2026, broke frontends that assumed them).

`csdisplay.c` opens the screen from the kernel's own answers and never assumes:

- size, format, row length (stride) and memory come from `FBIOGET_VSCREENINFO` / `FBIOGET_FSCREENINFO`;
- MiSTer's framebuffer driver mode (`/sys/module/MiSTer_fb/parameters/mode`) is set if asked, then read back;
- vsync and the console's graphics mode are used only if they work (`csd_vsync` falls back to a timed wait);
- what it found is compared with Coreshell's known-good settings (`/media/fat/coreshell/state/kernel/display.json`,
  written by `coreshell/kernel.py`) and reported in `/tmp/csdisplay.json`.

```c
#include "csdisplay.h"
csd_t d;
if (csd_open(&d, 1920, 1080)) { /* d.err says why; corehost keeps the screen on the OSD */ }
uint32_t *px = d.pixels;      /* d.width, d.height, d.stride (bytes a row), d.format ("8888": ARGB) */
csd_vsync(&d);
csd_close(&d);
```

### Console switches

Scripts and add-ons switch Linux consoles: Update All's device check does `chvt 3`, presses F9, then runs itself on
tty7. A frontend drawing with its console in graphics mode makes Linux refuse those switches. Stock MiSTer never has this
problem, and with these three calls a frontend doesn't either. Linux asks the frontend first, the frontend lets the
switch go and draws nothing while away, and redraws everything when its console is back:

```c
csd_vt_init();                         /* once, at start */
if (csd_vt_tick()) redraw_everything(); /* every turn of the loop */
if (csd_vt_away()) continue;            /* another program's console is shown: don't draw */
```

`csd_test vt` shows it working: it watches for 10 s while you `chvt 3` and `chvt 1` from a shell.

Add `csdisplay.c` and `csdisplay.h` to the frontend's build. Apache-2.0, so any frontend can use it.

## csd_test

`make` builds `csd_test` for the MiSTer (static, musl). On the MiSTer, `csd_test [report.json]` opens the screen the
way a frontend would. It writes a small pattern into one corner, reads it back and restores the corner, so the screen
isn't disturbed. Exit 0 means a frontend can draw on this kernel; exit 1 means it can't, and the report says why.

Tested on the SS1 (kernel 6.18.38): 1920×1080, 32 bits, 7680 bytes a row, at 0x22001000; mode setting, vsync and
console graphics mode all work.
