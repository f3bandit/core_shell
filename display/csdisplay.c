/* csdisplay: a frontend's screen, opened the way the kernel says it is now (csdisplay.h).
 * Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (core_shell/LICENSE, NOTICE). */
#include "csdisplay.h"
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/kd.h>
#include <linux/vt.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define MODE_PARAM "/sys/module/MiSTer_fb/parameters/mode"
#define KNOWN_GOOD "/media/fat/coreshell/state/kernel/display.json"

static int query(csd_t *d, struct fb_var_screeninfo *v, struct fb_fix_screeninfo *f)
{
	if (ioctl(d->fd, FBIOGET_VSCREENINFO, v) || ioctl(d->fd, FBIOGET_FSCREENINFO, f)) {
		snprintf(d->err, sizeof(d->err), "the framebuffer doesn't answer FBIOGET_*SCREENINFO (%s)", strerror(errno));
		return -1;
	}
	d->width = (int)v->xres;
	d->height = (int)v->yres;
	d->bpp = (int)v->bits_per_pixel;
	d->stride = (int)f->line_length;
	d->smem_start = f->smem_start;
	if (d->bpp == 32) snprintf(d->format, sizeof(d->format), "%u%u%u%u", v->transp.length, v->red.length, v->green.length, v->blue.length);
	else snprintf(d->format, sizeof(d->format), "%d", d->bpp);
	return 0;
}

/* a number after "key": in Coreshell's display.json (flat, written by kernel.py), -1 when not there */
static long json_num(const char *j, const char *key)
{
	char k[40];
	snprintf(k, sizeof(k), "\"%s\":", key);
	const char *p = strstr(j, k);
	if (!p) return -1;
	p += strlen(k);
	while (*p == ' ') p++;
	return (*p >= '0' && *p <= '9') ? strtol(p, NULL, 10) : -1;
}

static void against_known_good(csd_t *d)
{
	char j[4096];
	FILE *fp = fopen(KNOWN_GOOD, "r");
	d->changed[0] = 0;
	if (!fp) { d->matches_known_good = -1; return; }
	size_t n = fread(j, 1, sizeof(j) - 1, fp);
	fclose(fp);
	j[n] = 0;
	struct { const char *k; long now; } c[] = { { "xres", d->width }, { "yres", d->height }, { "bpp", d->bpp }, { "stride", d->stride } };
	d->matches_known_good = 1;
	for (size_t i = 0; i < sizeof(c) / sizeof(c[0]); i++) {
		long was = json_num(j, c[i].k);
		if (was >= 0 && was != c[i].now) {
			d->matches_known_good = 0;
			size_t l = strlen(d->changed);
			snprintf(d->changed + l, sizeof(d->changed) - l, "%s%s %ld -> %ld", l ? ", " : "", c[i].k, was, c[i].now);
		}
	}
}

int csd_open(csd_t *d, int want_w, int want_h)
{
	memset(d, 0, sizeof(*d));
	d->fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
	if (d->fd < 0) { snprintf(d->err, sizeof(d->err), "no framebuffer: /dev/fb0 (%s)", strerror(errno)); return -1; }
	d->has_mode_param = access(MODE_PARAM, W_OK) == 0;
	if (want_w > 0 && want_h > 0 && d->has_mode_param) {
		/* MiSTer's driver: format 8888, rb 1, the size and its row length; then the kernel is asked again (below) */
		FILE *fp = fopen(MODE_PARAM, "w");
		if (fp) { fprintf(fp, "8888 1 %d %d %d\n", want_w, want_h, want_w * 4); fclose(fp); }
	}
	struct fb_var_screeninfo v;
	struct fb_fix_screeninfo f;
	if (query(d, &v, &f)) { close(d->fd); d->fd = -1; return -2; }
	if (d->bpp != 32 && d->bpp != 16) {
		snprintf(d->err, sizeof(d->err), "the framebuffer is %d bits a pixel (a frontend needs 32 or 16)", d->bpp);
		close(d->fd); d->fd = -1; return -3;
	}
	if (d->stride < d->width * (d->bpp / 8)) {
		snprintf(d->err, sizeof(d->err), "the framebuffer's rows (%d bytes) are shorter than its width (%d)", d->stride, d->width);
		close(d->fd); d->fd = -1; return -3;
	}
	d->map_len = (size_t)d->stride * (size_t)d->height;
	if (f.smem_len && d->map_len > f.smem_len) d->map_len = f.smem_len;
	d->pixels = mmap(NULL, d->map_len, PROT_READ | PROT_WRITE, MAP_SHARED, d->fd, 0);
	if (d->pixels == MAP_FAILED) {
		d->pixels = NULL;
		snprintf(d->err, sizeof(d->err), "the framebuffer can't be mapped (%s)", strerror(errno));
		close(d->fd); d->fd = -1; return -4;
	}
	int zero = 0;
	d->has_vsync = ioctl(d->fd, FBIO_WAITFORVSYNC, &zero) == 0;
	int tty = open("/dev/tty0", O_RDWR | O_CLOEXEC);
	if (tty >= 0) { long m = 0; d->has_graphics_mode = ioctl(tty, KDGETMODE, &m) == 0; close(tty); }
	against_known_good(d);
	return 0;
}

void csd_vsync(csd_t *d)
{
	int zero = 0;
	if (d->has_vsync && ioctl(d->fd, FBIO_WAITFORVSYNC, &zero) == 0) return;
	struct timespec ts = { 0, 16666667 };       /* (no vsync on this kernel: a frame's time) */
	nanosleep(&ts, NULL);
}

int csd_graphics_mode(csd_t *d, int on)
{
	if (!d->has_graphics_mode) return -1;
	int tty = open("/dev/tty0", O_RDWR | O_CLOEXEC);
	if (tty < 0) return -1;
	int r = ioctl(tty, KDSETMODE, on ? KD_GRAPHICS : KD_TEXT);
	close(tty);
	return r ? -1 : 0;
}

int csd_report(const csd_t *d, const char *path)
{
	FILE *fp = fopen(path ? path : "/tmp/csdisplay.json", "w");
	if (!fp) return -1;
	fprintf(fp, "{\"ok\":%s,\"width\":%d,\"height\":%d,\"bpp\":%d,\"stride\":%d,\"format\":\"%s\",\"smem\":\"0x%08lx\","
	            "\"mode_param\":%s,\"vsync\":%s,\"graphics_mode\":%s,\"known_good\":%d,\"changed\":\"%s\",\"error\":\"%s\"}\n",
	        d->pixels ? "true" : "false", d->width, d->height, d->bpp, d->stride, d->format, d->smem_start,
	        d->has_mode_param ? "true" : "false", d->has_vsync ? "true" : "false", d->has_graphics_mode ? "true" : "false",
	        d->matches_known_good, d->changed, d->err);
	fclose(fp);
	return 0;
}

void csd_close(csd_t *d)
{
	if (d->pixels) munmap(d->pixels, d->map_len);
	if (d->fd >= 0) close(d->fd);
	d->pixels = NULL;
	d->fd = -1;
}

/* ---- console switches (ported from Phosphor's shell, frontend/src/main.c vt_watch_init / vt_tick) ---- */
static int vt_fd = -1, vt_away_ = 0;
static volatile sig_atomic_t vt_rel, vt_acq;
static void on_vt_rel(int sig) { (void)sig; vt_rel = 1; }
static void on_vt_acq(int sig) { (void)sig; vt_acq = 1; }

int csd_vt_shown(void)
{
	int fd = open("/dev/tty0", O_RDONLY | O_CLOEXEC);
	if (fd < 0) return 1;
	struct vt_stat vs;
	memset(&vs, 0, sizeof(vs));
	int r = ioctl(fd, VT_GETSTATE, &vs);
	close(fd);
	return r || vs.v_active <= 1;
}

int csd_vt_init(void)
{
	if (vt_fd >= 0) return 0;
	vt_fd = open("/dev/tty1", O_RDWR | O_CLOEXEC);
	if (vt_fd < 0) return -1;
	struct sigaction sa;
	memset(&sa, 0, sizeof(sa));
	sigemptyset(&sa.sa_mask);
	sa.sa_flags = SA_RESTART;
	sa.sa_handler = on_vt_rel;
	sigaction(SIGRTMIN + 1, &sa, NULL);
	sa.sa_handler = on_vt_acq;
	sigaction(SIGRTMIN + 2, &sa, NULL);
	struct vt_mode m;
	memset(&m, 0, sizeof(m));
	m.mode = VT_PROCESS;
	m.relsig = SIGRTMIN + 1;
	m.acqsig = SIGRTMIN + 2;
	if (ioctl(vt_fd, VT_SETMODE, &m) < 0) { close(vt_fd); vt_fd = -1; return -1; }
	vt_away_ = !csd_vt_shown();
	return 0;
}

int csd_vt_tick(void)
{
	if (vt_fd < 0) return 0;
	int back = 0;
	if (vt_rel) { vt_rel = 0; if (ioctl(vt_fd, VT_RELDISP, 1) == 0) vt_away_ = 1; }
	if (vt_acq) { vt_acq = 0; ioctl(vt_fd, VT_RELDISP, VT_ACKACQ); if (vt_away_) { vt_away_ = 0; back = 1; } }
	return back;
}

int csd_vt_away(void) { return vt_away_; }
