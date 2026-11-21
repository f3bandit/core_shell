// lutpatch: change one ALM's LUT_MASK in a stock core (Cyclone V 5CSEBA6, libmistral) - a patch that only
// reconfigures logic the core already has (no new logic, no routing).
//
//   lutpatch STOCK.rbf OUT.rbf LAB X Y ALM OLD_MASK NEW_MASK [TMODE OLD NEW]   (masks as 16 hex digits)
//
// TMODE OLD NEW also changes the ALM's top input mode (E_0, F_0, D_E, D_F), e.g. D_F -> D_E to read pin E0
// in place of the flip-flop feedback. Refuses unless the ALM's mask (and mode) are the OLD ones (so it only
// applies to the core it was worked out on).
// Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE). Built on Mistral (BSD 3-Clause, Olivier Galibert et al.).
#include "cyclonev.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>
using namespace mistral;
int main(int argc, char **argv)
{
	if ((argc != 9 && !(argc == 12 && !strcmp(argv[9], "TMODE"))) || strcmp(argv[3], "LAB")) {
		fprintf(stderr, "usage: lutpatch STOCK.rbf OUT.rbf LAB X Y ALM OLD_MASK NEW_MASK [TMODE OLD NEW]\n");
		return 2;
	}
	auto cv = CycloneV::get_model("5CSEBA6U23I7");
	std::ifstream f(argv[1], std::ios::binary);
	std::vector<uint8_t> d((std::istreambuf_iterator<char>(f)), {});
	if (d.empty())
		return 1;
	cv->rbf_load(d.data(), d.size());
	auto pos = CycloneV::xy2pos(atoi(argv[4]), atoi(argv[5]));
	int alm = atoi(argv[6]);
	uint64_t want = strtoull(argv[7], nullptr, 16), nw = strtoull(argv[8], nullptr, 16);
	CycloneV::bmux_setting_t bs;
	if (!cv->bmux_get(CycloneV::LAB, pos, CycloneV::LUT_MASK, alm, bs))
		return 1;
	uint64_t cur = 0;                                   // r: the mask's bytes, low byte first
	for (size_t i = 0; i < bs.r.size() && i < 8; i++)
		cur |= uint64_t(bs.r[i]) << (8 * i);
	if (cur != want) {
		fprintf(stderr, "lutpatch: LAB %s.%s ALM %d mask is %016llx, not %016llx: not this core\n", argv[4], argv[5],
		        alm, (unsigned long long)cur, (unsigned long long)want);
		return 3;
	}
	if (argc == 12) {
		CycloneV::bmux_setting_t ms;
		auto om = cv->bmux_type_lookup(argv[10]), nm = cv->bmux_type_lookup(argv[11]);
		if (!cv->bmux_get(CycloneV::LAB, pos, CycloneV::TMODE, alm, ms))
			return 1;
		if (ms.s != uint32_t(om)) {
			fprintf(stderr, "lutpatch: LAB %s.%s ALM %d TMODE is %s, not %s: not this core\n", argv[4], argv[5], alm,
			        CycloneV::bmux_type_names[ms.s], argv[10]);
			return 3;
		}
		if (!cv->bmux_m_set(CycloneV::LAB, pos, CycloneV::TMODE, alm, nm))
			return 1;
		printf("lutpatch: LAB %s.%s ALM %d TMODE %s -> %s\n", argv[4], argv[5], alm, argv[10], argv[11]);
	}
	cv->bmux_r_set(CycloneV::LAB, pos, CycloneV::LUT_MASK, alm, nw);
	std::vector<uint8_t> out;
	cv->rbf_save(out);
	FILE *o = fopen(argv[2], "wb");
	if (!o || fwrite(out.data(), 1, out.size(), o) != out.size() || fclose(o))
		return 1;
	printf("lutpatch: LAB %s.%s ALM %d %016llx -> %016llx\n", argv[4], argv[5], alm, (unsigned long long)cur,
	       (unsigned long long)nw);
	return 0;
}
