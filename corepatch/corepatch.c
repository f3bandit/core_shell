/*
 * corepatch: apply a Coreshell core patch (mkpatch.py) to the user's own stock core, on the MiSTer.
 *
 *   corepatch PATCH.cspatch STOCK.rbf OUT.rbf     the patched core (only if STOCK is the file the patch is for,
 *                                                  and only if the result is exactly the one mkpatch checked)
 *   corepatch --info PATCH.cspatch                 what it's for
 *
 * The core's bitstream: an uncompressed header, the logic frames (Cyclone V nibble compression: a mask nibble
 * saying which of the next four nibbles are non-zero, then those), then a fixed end. The patch changes bytes
 * of the header and of the decompressed frames (their CRCs included); the end is kept as it is.
 * Exit codes: 0 done, 1 error, 2 usage, 3 not the stock file this patch is for, 4 result check failed.
 *
 * Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ MD5 (RFC 1321) */

typedef struct { uint32_t a, b, c, d; uint64_t len; uint8_t buf[64]; size_t n; } md5_t;

static const uint32_t K[64] = {
	0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
	0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
	0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
	0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
	0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
	0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
	0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
	0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391 };
static const uint8_t R[64] = { 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
	5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
	6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21 };

static void md5_block(md5_t *m, const uint8_t *p)
{
	uint32_t w[16], a = m->a, b = m->b, c = m->c, d = m->d;
	for (int i = 0; i < 16; i++)
		w[i] = p[i * 4] | p[i * 4 + 1] << 8 | p[i * 4 + 2] << 16 | (uint32_t)p[i * 4 + 3] << 24;
	for (int i = 0; i < 64; i++) {
		uint32_t f, g;
		if (i < 16)      { f = (b & c) | (~b & d); g = i; }
		else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) & 15; }
		else if (i < 48) { f = b ^ c ^ d;          g = (3 * i + 5) & 15; }
		else             { f = c ^ (b | ~d);       g = (7 * i) & 15; }
		uint32_t t = d;
		d = c;
		c = b;
		uint32_t x = a + f + K[i] + w[g];
		b = b + (x << R[i] | x >> (32 - R[i]));
		a = t;
	}
	m->a += a; m->b += b; m->c += c; m->d += d;
}

static void md5_init(md5_t *m) { m->a = 0x67452301; m->b = 0xefcdab89; m->c = 0x98badcfe; m->d = 0x10325476; m->len = 0; m->n = 0; }

static void md5_add(md5_t *m, const uint8_t *p, size_t n)
{
	m->len += n;
	while (n) {
		size_t k = 64 - m->n < n ? 64 - m->n : n;
		memcpy(m->buf + m->n, p, k);
		m->n += k; p += k; n -= k;
		if (m->n == 64) { md5_block(m, m->buf); m->n = 0; }
	}
}

static void md5_end(md5_t *m, uint8_t out[16])
{
	uint64_t bits = m->len * 8;
	uint8_t pad = 0x80, z = 0, l[8];
	md5_add(m, &pad, 1);
	while (m->n != 56) md5_add(m, &z, 1);
	for (int i = 0; i < 8; i++) l[i] = bits >> (8 * i);
	md5_add(m, l, 8);
	uint32_t s[4] = { m->a, m->b, m->c, m->d };
	for (int i = 0; i < 16; i++) out[i] = s[i / 4] >> (8 * (i % 4));
}

static void md5(const uint8_t *p, size_t n, uint8_t out[16]) { md5_t m; md5_init(&m); md5_add(&m, p, n); md5_end(&m, out); }

/* ------------------------------------------------------------------ files */

static uint8_t *slurp(const char *path, size_t *n)
{
	FILE *f = fopen(path, "rb");
	if (!f) { perror(path); return NULL; }
	fseek(f, 0, SEEK_END);
	long len = ftell(f);
	fseek(f, 0, SEEK_SET);
	uint8_t *b = malloc(len ? len : 1);
	if (!b || fread(b, 1, len, f) != (size_t)len) { fprintf(stderr, "corepatch: can't read %s\n", path); fclose(f); free(b); return NULL; }
	fclose(f);
	*n = len;
	return b;
}

static void hex(const uint8_t *m, char *out) { for (int i = 0; i < 16; i++) sprintf(out + i * 2, "%02x", m[i]); }

struct patch { const uint8_t *stock_md5, *out_md5; uint32_t prefix, framed; char name[256]; const uint8_t *runs, *end; };

static int parse(const uint8_t *p, size_t n, struct patch *pt)
{
	if (n < 8 + 32 + 10 || memcmp(p, "CSPATCH1", 8)) { fprintf(stderr, "corepatch: not a Coreshell core patch\n"); return -1; }
	pt->stock_md5 = p + 8;
	pt->out_md5 = p + 24;
	const uint8_t *q = p + 40;
	pt->prefix = q[0] | q[1] << 8 | q[2] << 16 | (uint32_t)q[3] << 24;
	pt->framed = q[4] | q[5] << 8 | q[6] << 16 | (uint32_t)q[7] << 24;
	unsigned nl = q[8] | q[9] << 8;
	q += 10;
	if (q + nl > p + n) return -1;
	snprintf(pt->name, sizeof(pt->name), "%.*s", (int)nl, (const char *)q);
	pt->runs = q + nl;
	pt->end = p + n;
	return 0;
}

/* ------------------------------------------------------------------ compression */

static int decompress(const uint8_t *src, size_t n, size_t start, uint8_t *out, size_t framed, size_t *end)
{
	size_t pos = start * 2, o = 0;   /* nibbles */
#define NIB() (pos >> 1 >= n ? -1 : (pos & 1 ? src[pos++ >> 1] >> 4 : src[pos++ >> 1] & 0xF))
	while (o < framed) {
		int key = NIB(), v = 0, x;
		if (key < 0) return -1;
		if (key & 1) { if ((x = NIB()) < 0) return -1; v = x; }
		if (key & 2) { if ((x = NIB()) < 0) return -1; v |= x << 4; }
		out[o++] = v;
		v = 0;
		if (key & 4) { if ((x = NIB()) < 0) return -1; v = x; }
		if (key & 8) { if ((x = NIB()) < 0) return -1; v |= x << 4; }
		out[o++] = v;
	}
#undef NIB
	*end = (pos + 1) >> 1;
	return 0;
}

static size_t compress(const uint8_t *in, size_t framed, uint8_t *out)
{
	size_t o = 0;
	int high = 0;
#define W(nib) do { if (high) { out[o - 1] |= (nib) << 4; high = 0; } else { out[o++] = (nib); high = 1; } } while (0)
	for (size_t i = 0; i < framed; i += 2) {
		uint8_t a = in[i], b = in[i + 1];
		uint8_t mask = (a & 0x0F ? 1 : 0) | (a & 0xF0 ? 2 : 0) | (b & 0x0F ? 4 : 0) | (b & 0xF0 ? 8 : 0);
		W(mask);
		if (mask & 1) W(a & 0xF);
		if (mask & 2) W(a >> 4);
		if (mask & 4) W(b & 0xF);
		if (mask & 8) W(b >> 4);
	}
	while (high) W(0xF);
#undef W
	return o;
}

/* ------------------------------------------------------------------ main */

int main(int argc, char **argv)
{
	if (argc == 3 && !strcmp(argv[1], "--info")) {
		size_t n;
		uint8_t *p = slurp(argv[2], &n);
		struct patch pt;
		if (!p || parse(p, n, &pt)) return 1;
		char a[33], b[33];
		hex(pt.stock_md5, a);
		hex(pt.out_md5, b);
		printf("%s\nfor stock md5 %s\nresult md5 %s\n", pt.name, a, b);
		return 0;
	}
	if (argc != 4) {
		fprintf(stderr, "usage: corepatch PATCH.cspatch STOCK.rbf OUT.rbf | corepatch --info PATCH.cspatch\n");
		return 2;
	}
	size_t pn, sn;
	uint8_t *p = slurp(argv[1], &pn), *s = p ? slurp(argv[2], &sn) : NULL;
	struct patch pt;
	if (!p || !s || parse(p, pn, &pt)) return 1;
	uint8_t m[16];
	md5(s, sn, m);
	if (memcmp(m, pt.stock_md5, 16)) {
		char a[33], b[33];
		hex(m, a);
		hex(pt.stock_md5, b);
		fprintf(stderr, "corepatch: %s isn't the core this patch is for (md5 %s, wants %s)\n", argv[2], a, b);
		return 3;
	}
	if (sn < pt.prefix) return 1;
	uint8_t *framed = malloc(pt.framed);
	size_t end;
	if (!framed || decompress(s, sn, pt.prefix, framed, pt.framed, &end)) {
		fprintf(stderr, "corepatch: can't decompress %s\n", argv[2]);
		return 1;
	}
	uint8_t *head = malloc(pt.prefix ? pt.prefix : 1);
	if (!head) return 1;
	memcpy(head, s, pt.prefix);

	const uint8_t *q = pt.runs;
	while (q < pt.end && *q != 0xFF) {
		if (q + 7 > pt.end) return 1;
		uint8_t area = q[0];
		uint32_t off = q[1] | q[2] << 8 | q[3] << 16 | (uint32_t)q[4] << 24;
		uint32_t len = q[5] | q[6] << 8;
		q += 7;
		uint8_t *dst = area == 0 ? head : framed;
		uint32_t lim = area == 0 ? pt.prefix : pt.framed;
		if (area > 1 || (uint64_t)off + len > lim || q + len > pt.end)   /* (64-bit: off + len can't wrap) */ { fprintf(stderr, "corepatch: damaged patch\n"); return 1; }
		memcpy(dst + off, q, len);
		q += len;
	}

	size_t cap = pt.framed + pt.framed / 2 + 16;           /* worst case: 5 nibbles per 2 bytes */
	uint8_t *comp = malloc(cap);
	if (!comp) return 1;
	size_t cn = compress(framed, pt.framed, comp);
	md5_t mm;
	md5_init(&mm);
	md5_add(&mm, head, pt.prefix);
	md5_add(&mm, comp, cn);
	md5_add(&mm, s + end, sn - end);
	md5_end(&mm, m);
	if (memcmp(m, pt.out_md5, 16)) {
		fprintf(stderr, "corepatch: the result isn't the one this patch was checked for; nothing written\n");
		return 4;
	}
	FILE *o = fopen(argv[3], "wb");
	if (!o || fwrite(head, 1, pt.prefix, o) != pt.prefix || fwrite(comp, 1, cn, o) != cn ||
	    fwrite(s + end, 1, sn - end, o) != sn - end || fclose(o)) {
		perror(argv[3]);
		remove(argv[3]);
		return 1;
	}
	printf("corepatch: %s -> %s (%s)\n", argv[2], argv[3], pt.name);
	return 0;
}
