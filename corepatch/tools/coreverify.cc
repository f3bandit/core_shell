// coreverify: check that a patched core only adds to its stock core (Cyclone V 5CSEBA6, libmistral).
//
//   coreverify STOCK.rbf PATCHED.rbf [TAKEN_NODE ...]
//
// Passes (exit 0) only if
//   - every routing link of the stock core is still there, except links into the TAKEN nodes (the ones the
//     patch takes over on purpose, e.g. the HPS UART's RXD and the front-port pins' outputs), and
//   - every block setting of the stock core (logic, memory, DSP, IO, clocks...) is unchanged, except in logic
//     blocks the stock core left empty.
//
// Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE). Built on Mistral (BSD 3-Clause, Olivier Galibert et al.).
#include "cyclonev.h"
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <vector>
using namespace mistral;

struct state {
	std::set<std::pair<uint32_t, uint32_t>> links;
	std::map<std::tuple<int, int, int, int>, std::pair<uint32_t, std::vector<uint8_t>>> settings;   // bt pos mux midx
	std::set<int> logic_tiles;
};

static bool load(CycloneV *cv, const char *fn, state &s)
{
	std::ifstream f(fn, std::ios::binary);
	std::vector<uint8_t> d((std::istreambuf_iterator<char>(f)), {});
	if (d.empty())
		return false;
	cv->clear();
	cv->rbf_load(d.data(), d.size());
	for (auto &l : cv->route_all_active_links()) {
		s.links.insert(l);
		for (auto rn : {l.first, l.second}) {
			auto pn = cv->rnode_to_pnode(rn);
			if (!pn)
				continue;
			auto bt = CycloneV::pn2bt(pn);
			if (bt == CycloneV::LAB || bt == CycloneV::MLAB || bt == CycloneV::M10K || bt == CycloneV::DSP)
				s.logic_tiles.insert(CycloneV::pn2p(pn));
		}
	}
	for (auto &b : cv->bmux_get())
		if (!b.def)
			s.settings[{int(b.btype), int(b.pos), int(b.mux), b.midx}] = {b.s, b.r};
	return true;
}

int main(int argc, char **argv)
{
	if (argc < 3) {
		fprintf(stderr, "usage: coreverify STOCK.rbf PATCHED.rbf [TAKEN_NODE ...]\n");
		return 2;
	}
	auto cv = CycloneV::get_model("5CSEBA6U23I7");
	std::set<std::string> taken;
	for (int i = 3; i < argc; i++)
		taken.insert(argv[i]);
	state a, b;
	if (!load(cv, argv[1], a) || !load(cv, argv[2], b)) {
		fprintf(stderr, "coreverify: can't read the bitstreams\n");
		return 1;
	}
	int bad = 0, taken_n = 0, added = 0;
	for (auto &l : a.links)
		if (!b.links.count(l)) {
			if (taken.count(CycloneV::rn2s(l.second))) {
				taken_n++;
				continue;
			}
			if (bad++ < 20)
				printf("LOST link %s -> %s\n", CycloneV::rn2s(l.first).c_str(), CycloneV::rn2s(l.second).c_str());
		}
	for (auto &l : b.links)
		added += !a.links.count(l);
	int changed = 0, new_blocks = 0;
	std::set<int> new_tiles;
	auto is_logic = [](int bt) { return bt == CycloneV::LAB || bt == CycloneV::MLAB; };
	for (auto &kv : b.settings) {
		auto k = kv.first;
		auto it = a.settings.find(k);
		if (it != a.settings.end() && it->second == kv.second)
			continue;
		if (is_logic(std::get<0>(k)) && !a.logic_tiles.count(std::get<1>(k))) {
			new_tiles.insert(std::get<1>(k));
			continue;                         // ours, in a block the stock core left empty
		}
		if (changed++ < 20)
			printf("CHANGED %s %d.%d %s.%d\n", CycloneV::block_type_names[std::get<0>(k)],
			       CycloneV::pos2x(std::get<1>(k)), CycloneV::pos2y(std::get<1>(k)),
			       CycloneV::bmux_type_names[std::get<2>(k)], std::get<3>(k));
	}
	for (auto &kv : a.settings)
		if (!b.settings.count(kv.first) && changed++ < 40)
			printf("CLEARED %s %d.%d %s.%d\n", CycloneV::block_type_names[std::get<0>(kv.first)],
			       CycloneV::pos2x(std::get<1>(kv.first)), CycloneV::pos2y(std::get<1>(kv.first)),
			       CycloneV::bmux_type_names[std::get<2>(kv.first)], std::get<3>(kv.first));
	new_blocks = new_tiles.size();
	printf("links: %zu stock, %d lost, %d taken over, %d added; settings: %zu stock, %d changed; %d new logic blocks\n",
	       a.links.size(), bad, taken_n, added, a.settings.size(), changed, new_blocks);
	if (bad || changed) {
		printf("FAIL\n");
		return 1;
	}
	printf("PASS\n");
	return 0;
}
