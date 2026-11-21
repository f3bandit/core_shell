/*
 * discfs: the disc in the drive as an ordinary cue sheet and bin, for Coreshell (by f3bandit).
 *
 *   discfs [-d /dev/sr0] [-n name] <mountpoint>      serve <mountpoint>/<name>.cue and <name>.bin
 *   discfs [-d /dev/sr0] --toc                       print the disc's tracks (no mount)
 *
 * Any core then plays the disc like an image on the SD card, under the frontend's own Main: Coreshell's settings,
 * Super MemCard and the in-game menu come with it, no other Main involved.
 *
 * Written from the MMC command set (READ TOC 0x43, READ CD 0xBE over SG_IO) and the Linux FUSE kernel protocol
 * (/dev/fuse, no libfuse: the SS1 has none). The .bin is every sector from LBA 0 to the lead-out, 2352 bytes
 * each (sync, header, data, EDC/ECC for data tracks; the samples for audio), read from the drive on demand
 * with read-ahead. The disc's own idea of Physical-CD playback is Anime0t4ku's MiSTer_Physical-CD; none of its
 * code is here.
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/fuse.h>
#include <scsi/sg.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define SECTOR 2352
#define CHUNK 32                 /* sectors per drive read (75 KB) */
#define SLOTS 24                 /* chunks kept (1.8 MB) */
#define MAXT 100

static const char *dev = "/dev/sr0", *mnt;
static char name[200] = "disc";
static int cd = -1;

static struct track { int no, lba, audio, mode; } trk[MAXT];
static int ntrk, leadout;
static char cue[8192];
static size_t cuelen;
static uint64_t binsize;
static time_t made;

/* ------------------------------------------------------------------ the drive */

static int scsi(const unsigned char *cdb, int cdblen, void *buf, int len, int timeout_ms)
{
	unsigned char sense[32];
	sg_io_hdr_t io;
	memset(&io, 0, sizeof(io));
	io.interface_id = 'S';
	io.cmd_len = cdblen;
	io.cmdp = (unsigned char *)cdb;
	io.dxfer_direction = SG_DXFER_FROM_DEV;
	io.dxferp = buf;
	io.dxfer_len = len;
	io.sbp = sense;
	io.mx_sb_len = sizeof(sense);
	io.timeout = timeout_ms;
	if (ioctl(cd, SG_IO, &io) < 0)
		return -1;
	if ((io.info & SG_INFO_OK_MASK) != SG_INFO_OK)
		return -2;
	return len - io.resid;
}

static int read_cd(int lba, int n, unsigned char *buf)
{
	unsigned char cdb[12] = { 0xBE, 0x00, lba >> 24, lba >> 16, lba >> 8, lba, n >> 16, n >> 8, n,
	                          0xF8, 0x00, 0x00 };      /* any sector type; sync, header(s), data, EDC/ECC */
	for (int tries = 0; tries < 3; tries++)
		if (scsi(cdb, 12, buf, n * SECTOR, 20000) == n * SECTOR)
			return 0;
	return -1;
}

static int read_toc(void)
{
	unsigned char cdb[10] = { 0x43, 0x00, 0x00, 0, 0, 0, 1, 0x0F, 0xFC, 0x00 };   /* format 0, LBA, from track 1 */
	unsigned char b[4096];
	int got = scsi(cdb, 10, b, sizeof(b), 10000);
	if (got < 4)
		return -1;
	int len = (b[0] << 8 | b[1]) + 2;
	if (len > got)
		len = got;
	ntrk = 0;
	for (int i = 4; i + 8 <= len && ntrk < MAXT; i += 8) {
		int no = b[i + 2], ctl = b[i + 1] & 0x0F;
		int lba = b[i + 4] << 24 | b[i + 5] << 16 | b[i + 6] << 8 | b[i + 7];
		if (no == 0xAA) {
			leadout = lba;
			continue;
		}
		trk[ntrk].no = no;
		trk[ntrk].lba = lba;
		trk[ntrk].audio = !(ctl & 4);
		trk[ntrk].mode = 0;
		ntrk++;
	}
	if (!ntrk || leadout <= 0)
		return -1;
	unsigned char s[SECTOR];
	for (int t = 0; t < ntrk; t++)        /* a data track's mode: its first sector's header */
		if (!trk[t].audio)
			trk[t].mode = read_cd(trk[t].lba, 1, s) ? 1 : (s[15] == 2 ? 2 : 1);
	return 0;
}

static void msf(int lba, char *out)
{
	sprintf(out, "%02d:%02d:%02d", lba / 4500, lba / 75 % 60, lba % 75);
}

static void make_cue(void)
{
	char m[16];
	cuelen = snprintf(cue, sizeof(cue), "FILE \"%s.bin\" BINARY\r\n", name);
	for (int t = 0; t < ntrk && cuelen < sizeof(cue) - 128; t++) {
		msf(trk[t].lba, m);
		cuelen += snprintf(cue + cuelen, sizeof(cue) - cuelen, "  TRACK %02d %s\r\n    INDEX 01 %s\r\n",
		                   trk[t].no, trk[t].audio ? "AUDIO" : trk[t].mode == 2 ? "MODE2/2352" : "MODE1/2352", m);
	}
	binsize = (uint64_t)leadout * SECTOR;
}

/* ------------------------------------------------------------------ read-ahead */

static struct slot { int lba; unsigned age; unsigned char d[CHUNK * SECTOR]; } *slot;
static unsigned clock_;

static unsigned char *sector(int lba)
{
	int base = lba / CHUNK * CHUNK, old = 0;
	for (int i = 0; i < SLOTS; i++) {
		if (slot[i].lba == base) {
			slot[i].age = ++clock_;
			return slot[i].d + (lba - base) * SECTOR;
		}
		if (slot[i].age < slot[old].age)
			old = i;
	}
	int n = leadout - base < CHUNK ? leadout - base : CHUNK;
	if (read_cd(base, n, slot[old].d)) {
		/* a bad patch: one at a time, so only the bad sectors fail */
		slot[old].lba = -1;
		static unsigned char one[SECTOR];
		return read_cd(lba, 1, one) ? NULL : one;
	}
	slot[old].lba = base;
	slot[old].age = ++clock_;
	return slot[old].d + (lba - base) * SECTOR;
}

/* ------------------------------------------------------------------ FUSE */

enum { INO_ROOT = 1, INO_CUE = 2, INO_BIN = 3 };
static int fd;

static void reply(uint64_t unique, int err, const void *data, size_t len)
{
	struct fuse_out_header h = { .len = sizeof(h) + (err ? 0 : len), .error = -err, .unique = unique };
	static char buf[sizeof(h) + 256 * 1024];
	memcpy(buf, &h, sizeof(h));
	if (!err && len)
		memcpy(buf + sizeof(h), data, len);
	if (write(fd, buf, h.len) < 0 && errno != ENOENT)
		perror("discfs: reply");
}

static void attr(int ino, struct fuse_attr *a)
{
	memset(a, 0, sizeof(*a));
	a->ino = ino;
	a->mtime = a->ctime = a->atime = made;
	a->nlink = 1;
	a->blksize = SECTOR * CHUNK;
	if (ino == INO_ROOT) {
		a->mode = S_IFDIR | 0555;
		a->nlink = 2;
	} else {
		a->mode = S_IFREG | 0444;
		a->size = ino == INO_CUE ? cuelen : binsize;
		a->blocks = (a->size + 511) / 512;
	}
}

static int file_of(const char *n)
{
	char want[256];
	snprintf(want, sizeof(want), "%s.cue", name);
	if (!strcmp(n, want))
		return INO_CUE;
	snprintf(want, sizeof(want), "%s.bin", name);
	return !strcmp(n, want) ? INO_BIN : 0;
}

static size_t add_dirent(char *out, size_t room, uint64_t ino, uint64_t off, const char *n, int type)
{
	size_t nl = strlen(n), sz = FUSE_DIRENT_ALIGN(FUSE_NAME_OFFSET + nl);
	if (sz > room)
		return 0;
	struct fuse_dirent *d = (struct fuse_dirent *)out;
	memset(out, 0, sz);
	d->ino = ino;
	d->off = off;
	d->namelen = nl;
	d->type = type;
	memcpy(d->name, n, nl);
	return sz;
}

static void serve(void)
{
	static char in[FUSE_MIN_READ_BUFFER + 256 * 1024];
	static char out[256 * 1024];
	for (;;) {
		ssize_t r = read(fd, in, sizeof(in));
		if (r < 0) {
			if (errno == EINTR || errno == ENOENT || errno == EAGAIN)
				continue;
			if (errno == ENODEV)
				return;                      /* unmounted */
			perror("discfs: read");
			return;
		}
		struct fuse_in_header *h = (struct fuse_in_header *)in;
		void *arg = in + sizeof(*h);
		switch (h->opcode) {
		case FUSE_INIT: {
			struct fuse_init_in *i = arg;
			struct fuse_init_out o = { .major = FUSE_KERNEL_VERSION,
			                           .minor = i->minor < FUSE_KERNEL_MINOR_VERSION ? i->minor : FUSE_KERNEL_MINOR_VERSION,
			                           .max_readahead = i->max_readahead, .max_write = 4096,
			                           .flags = FUSE_ASYNC_READ & i->flags, .max_background = 4,
			                           .congestion_threshold = 3 };
			reply(h->unique, 0, &o, i->minor < 23 ? 24 : sizeof(o));
			break;
		}
		case FUSE_LOOKUP: {
			int ino = h->nodeid == INO_ROOT ? file_of((char *)arg) : 0;
			if (!ino) {
				reply(h->unique, ENOENT, NULL, 0);
				break;
			}
			struct fuse_entry_out e = { .nodeid = ino, .generation = 1, .entry_valid = 3600, .attr_valid = 3600 };
			attr(ino, &e.attr);
			reply(h->unique, 0, &e, sizeof(e));
			break;
		}
		case FUSE_GETATTR: {
			struct fuse_attr_out a = { .attr_valid = 3600 };
			attr(h->nodeid, &a.attr);
			reply(h->unique, 0, &a, sizeof(a));
			break;
		}
		case FUSE_OPEN: case FUSE_OPENDIR: {
			struct fuse_open_out o = { .fh = h->nodeid, .open_flags = h->opcode == FUSE_OPEN ? FOPEN_KEEP_CACHE : 0 };
			reply(h->unique, 0, &o, sizeof(o));
			break;
		}
		case FUSE_READ: {
			struct fuse_read_in *ri = arg;
			uint64_t size = h->nodeid == INO_CUE ? cuelen : binsize, off = ri->offset;
			size_t want = ri->size > sizeof(out) ? sizeof(out) : ri->size, done = 0;
			if (off >= size) {
				reply(h->unique, 0, NULL, 0);
				break;
			}
			if (want > size - off)
				want = size - off;
			if (h->nodeid == INO_CUE) {
				reply(h->unique, 0, cue + off, want);
				break;
			}
			int err = 0;
			while (done < want) {
				uint64_t p = off + done;
				unsigned char *s = sector(p / SECTOR);
				if (!s) {
					err = EIO;
					break;
				}
				size_t in_s = SECTOR - p % SECTOR, n = want - done < in_s ? want - done : in_s;
				memcpy(out + done, s + p % SECTOR, n);
				done += n;
			}
			if (err && !done)
				reply(h->unique, err, NULL, 0);
			else
				reply(h->unique, 0, out, done);
			break;
		}
		case FUSE_READDIR: {
			struct fuse_read_in *ri = arg;
			char n1[256], n2[256];
			snprintf(n1, sizeof(n1), "%s.cue", name);
			snprintf(n2, sizeof(n2), "%s.bin", name);
			struct { const char *n; int ino, type; } ents[] = {
				{ ".", INO_ROOT, DT_DIR }, { "..", INO_ROOT, DT_DIR }, { n1, INO_CUE, DT_REG }, { n2, INO_BIN, DT_REG } };
			size_t len = 0, room = ri->size > sizeof(out) ? sizeof(out) : ri->size;   /* (never past out) */
			for (unsigned i = ri->offset; i < 4; i++) {
				size_t sz = add_dirent(out + len, room - len, ents[i].ino, i + 1, ents[i].n, ents[i].type);
				if (!sz)
					break;
				len += sz;
			}
			reply(h->unique, 0, out, len);
			break;
		}
		case FUSE_STATFS: {
			struct fuse_statfs_out s = { .st = { .blocks = binsize / 512, .bsize = 512, .namelen = 255, .frsize = 512 } };
			reply(h->unique, 0, &s, sizeof(s));
			break;
		}
		case FUSE_RELEASE: case FUSE_RELEASEDIR: case FUSE_FLUSH: case FUSE_ACCESS:
			reply(h->unique, 0, NULL, 0);
			break;
		case FUSE_FORGET: case FUSE_BATCH_FORGET: case FUSE_INTERRUPT:
			break;                               /* no reply */
		case FUSE_DESTROY:
			reply(h->unique, 0, NULL, 0);
			return;
		default:
			reply(h->unique, ENOSYS, NULL, 0);
		}
	}
}

static void stop(int sig)
{
	(void)sig;
	if (mnt)
		umount2(mnt, MNT_DETACH);
	_exit(0);
}

int main(int argc, char **argv)
{
	int toc_only = 0;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-d") && i + 1 < argc)
			dev = argv[++i];
		else if (!strcmp(argv[i], "-n") && i + 1 < argc)
			snprintf(name, sizeof(name), "%s", argv[++i]);
		else if (!strcmp(argv[i], "--toc"))
			toc_only = 1;
		else
			mnt = argv[i];
	}
	for (char *p = name; *p; p++)                  /* a file name: no slashes */
		if (*p == '/')
			*p = '-';
	if (!toc_only && !mnt) {
		fprintf(stderr, "usage: discfs [-d /dev/sr0] [-n name] <mountpoint> | --toc\n");
		return 2;
	}
	cd = open(dev, O_RDONLY | O_NONBLOCK);
	if (cd < 0 || read_toc()) {
		fprintf(stderr, "discfs: no readable disc in %s\n", dev);
		return 1;
	}
	made = time(NULL);
	make_cue();
	if (toc_only) {
		fwrite(cue, 1, cuelen, stdout);
		printf("lead-out %d (%llu bytes)\n", leadout, (unsigned long long)binsize);
		return 0;
	}
	slot = calloc(SLOTS, sizeof(*slot));
	for (int i = 0; i < SLOTS; i++)
		slot[i].lba = -1;
	mkdir(mnt, 0755);
	fd = open("/dev/fuse", O_RDWR);
	char opts[256];
	snprintf(opts, sizeof(opts), "fd=%d,rootmode=40000,user_id=0,group_id=0,allow_other,max_read=262144", fd);
	if (fd < 0 || mount("discfs", mnt, "fuse.discfs", MS_NOSUID | MS_NODEV | MS_RDONLY, opts)) {
		perror("discfs: mount");
		return 1;
	}
	signal(SIGTERM, stop);
	signal(SIGINT, stop);
	signal(SIGHUP, stop);
	printf("discfs: %s/%s.cue (%d tracks, %llu bytes)\n", mnt, name, ntrk, (unsigned long long)binsize);
	fflush(stdout);
	serve();
	umount2(mnt, MNT_DETACH);
	return 0;
}
