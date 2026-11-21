/* csdisplay: a frontend's screen on MiSTer's Linux, opened the way the kernel says it is now (Coreshell's display layer).
 *
 * A frontend (Phosphor, Console Mode, any other) draws straight to Linux's framebuffer. Opened through this, it never
 * assumes the kernel's display: the size, format, row length and memory come from the kernel's own answers
 * (FBIOGET_VSCREENINFO / FSCREENINFO), MiSTer's framebuffer driver mode is set and read back, vsync and the console's
 * graphics mode are used only if they work. What it found is checked against Coreshell's known-good state
 * (/media/fat/coreshell/state/kernel/display.json, coreshell/kernel.py) and reported (/tmp/csdisplay.json), so a new
 * kernel shows up as a clear report, never a black screen.
 *
 *   csd_t d;
 *   if (csd_open(&d, 1920, 1080)) { fprintf(stderr, "%s\n", d.err); ... tell corehost: the OSD keeps the screen ... }
 *   uint32_t *px = d.pixels;  d.stride (bytes), d.width, d.height, d.format ("8888": ARGB, as MiSTer_fb)
 *   csd_vsync(&d);  ...  csd_close(&d);
 *
 * Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (core_shell/LICENSE, NOTICE).
 */
#ifndef CSDISPLAY_H
#define CSDISPLAY_H

#include <stdint.h>
#include <stddef.h>

typedef struct {
	int fd;                     /* /dev/fb0 */
	void *pixels;               /* the framebuffer, mapped */
	size_t map_len;
	int width, height, bpp;     /* as the kernel says now */
	int stride;                 /* bytes a row (the kernel's line_length: never width * 4 assumed) */
	char format[8];             /* "8888" (bits of transp, red, green, blue), "565"... */
	unsigned long smem_start;   /* where the kernel's framebuffer is */
	int has_mode_param;         /* MiSTer's framebuffer driver takes a mode (/sys/module/MiSTer_fb/parameters/mode) */
	int has_vsync;              /* FBIO_WAITFORVSYNC works (else csd_vsync sleeps a frame) */
	int has_graphics_mode;      /* the console can be put in graphics mode (KDSETMODE) */
	int matches_known_good;     /* 1 what Coreshell knew to work, 0 changed (adapted to), -1 no known-good state */
	char changed[256];          /* what's different from the known-good state, for the log */
	char err[256];              /* why csd_open failed */
} csd_t;

/* 0: open, everything from the kernel's answers. want_w/want_h: the size to ask MiSTer's driver for (0: as it is).
   <0: the screen can't be used (d->err says why): stay on MiSTer's own menu. */
int csd_open(csd_t *d, int want_w, int want_h);
void csd_vsync(csd_t *d);
int csd_graphics_mode(csd_t *d, int on);   /* 0 done, -1 not possible here (harmless: the console may draw over) */
int csd_report(const csd_t *d, const char *path);   /* what was found, as JSON (default /tmp/csdisplay.json) */
void csd_close(csd_t *d);

/* Console switches, the way stock MiSTer leaves them working (docs/stock-behaviour.md; ported from Phosphor's shell).
 * Scripts and add-ons switch consoles (Update All's device check: chvt 3, F9, then its own terminal on tty7). A frontend
 * drawing on the framebuffer with its console in graphics mode makes Linux refuse those switches. With these, Linux
 * asks the frontend first (VT_PROCESS): it lets each switch go, draws nothing while away, and draws everything again
 * when its console is back.
 *   csd_vt_init();                       once, at start (after the console is put in graphics mode)
 *   if (csd_vt_tick()) redraw_all();     every turn of the frontend's loop
 *   if (csd_vt_away()) { idle; }         don't draw while another console is shown
 * It uses SIGRTMIN+1 and SIGRTMIN+2. */
int csd_vt_init(void);      /* 0, or -1 (no /dev/tty1, or VT_SETMODE refused: switches then work as before) */
int csd_vt_tick(void);      /* 1 when the frontend's console is shown again: draw it all */
int csd_vt_away(void);      /* 1 while another program's console is shown */
int csd_vt_shown(void);     /* the frontend's console (tty1) is the one shown now */

#endif
