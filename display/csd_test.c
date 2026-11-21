/* csd_test: Coreshell's display layer on this kernel, checked without disturbing the screen (csd_test vt: the
 * console-switch handling, for 10 s): opens it as a frontend
 * would, writes a small test pattern into a corner, reads it back, puts the corner back as it was, reports.
 *   csd_test [report.json]      exit 0: a frontend can draw here; 1: it can't (the report says why)
 * Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (core_shell/LICENSE, NOTICE). */
#include "csdisplay.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	if (argc > 1 && !strcmp(argv[1], "vt")) {          /* csd_test vt: console switches for 10 s, as a frontend sees them */
		if (csd_vt_init()) { printf("csd_test vt: VT_SETMODE refused\n"); return 1; }
		printf("csd_test vt: watching (shown now: %s)\n", csd_vt_shown() ? "ours" : "another");
		int was = csd_vt_away();
		for (int t = 0; t < 200; t++) {
			if (csd_vt_tick()) printf("  %.1f s: our console is back: draw it all\n", t * 0.05);
			if (csd_vt_away() != was) { was = csd_vt_away(); if (was) printf("  %.1f s: another console asked: let go\n", t * 0.05); }
			fflush(stdout);
			usleep(50000);
		}
		return 0;
	}
	csd_t d;
	int r = csd_open(&d, 0, 0);                    /* (the size as it is: the frontend running keeps its mode) */
	const char *out = argc > 1 ? argv[1] : "/tmp/csdisplay.json";
	if (r) {
		csd_report(&d, out);
		printf("csd_test: the screen can't be used: %s\n", d.err);
		return 1;
	}
	/* 32 x 32 at the top-left: saved, a pattern written and read back, put back */
	enum { N = 32 };
	int bpp = d.bpp / 8, ok = 1;
	unsigned char save[N][N * 4];
	unsigned char *base = (unsigned char *)d.pixels;
	for (int y = 0; y < N; y++) memcpy(save[y], base + (size_t)y * d.stride, (size_t)N * bpp);
	for (int y = 0; y < N; y++)
		for (int x = 0; x < N * bpp; x++) base[(size_t)y * d.stride + x] = (unsigned char)(x * 7 + y * 13);
	for (int y = 0; y < N && ok; y++)
		for (int x = 0; x < N * bpp; x++)
			if (base[(size_t)y * d.stride + x] != (unsigned char)(x * 7 + y * 13)) { ok = 0; break; }
	for (int y = 0; y < N; y++) memcpy(base + (size_t)y * d.stride, save[y], (size_t)N * bpp);
	csd_vsync(&d);
	if (!ok) snprintf(d.err, sizeof(d.err), "the framebuffer's memory doesn't keep what's written");
	csd_report(&d, out);
	printf("csd_test: %dx%d, %d bits (%s), %d bytes a row, at 0x%08lx; mode setting %s, vsync %s, graphics mode %s; %s%s%s\n",
	       d.width, d.height, d.bpp, d.format, d.stride, d.smem_start, d.has_mode_param ? "yes" : "no",
	       d.has_vsync ? "yes" : "no", d.has_graphics_mode ? "yes" : "no",
	       d.matches_known_good == 1 ? "as Coreshell knew it" : d.matches_known_good == 0 ? "changed since Coreshell's known-good state: " : "no known-good state yet",
	       d.matches_known_good == 0 ? d.changed : "", ok ? "" : " - MEMORY CHECK FAILED");
	csd_close(&d);
	return ok ? 0 : 1;
}
