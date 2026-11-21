// coremap: what a Coreshell core patch needs to know about a stock core (Cyclone V 5CSEBA6, libmistral).
//
//   coremap CORE.rbf
//
// Prints (key value lines, for build_patch.py):
//   rxd    the routing node feeding the HPS UART's RXD (the patch takes it over: Linux /dev/ttyS1)
//   clk    a global clock fed straight from a board clock pin (the DE10-Nano's are all 50 MHz), with fanout
//   tiles  free LABs whose first clock input that global can reach, x,y;x,y;... (nextpnr NPNR_ONLY_TILES)
//
// Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE). Built on Mistral (BSD 3-Clause, Olivier Galibert et al.).
#define private public
#define protected public
#include "cyclonev.h"
#undef private
#undef protected
#include <cstdio>
#include <cstring>
#include <fstream>
#include <queue>
#include <set>
#include <unordered_map>
#include <unordered_set>
#include <vector>
using namespace mistral;
typedef CycloneV::rnode_t rn_t;

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: coremap CORE.rbf\n");
		return 2;
	}
	auto cv = CycloneV::get_model("5CSEBA6U23I7");
	std::ifstream f(argv[1], std::ios::binary);
	std::vector<uint8_t> d((std::istreambuf_iterator<char>(f)), {});
	if (d.empty()) {
		fprintf(stderr, "coremap: can't read %s\n", argv[1]);
		return 1;
	}
	cv->rbf_load(d.data(), d.size());

	std::unordered_set<rn_t> blocked;
	std::unordered_map<rn_t, rn_t> bsrc;
	std::unordered_map<rn_t, std::vector<rn_t>> down;
	std::unordered_map<rn_t, int> fanout;
	std::set<int> used_tiles;
	for (auto &l : cv->route_all_active_links()) {
		blocked.insert(l.first);
		blocked.insert(l.second);
		bsrc[l.second] = l.first;
		down[l.first].push_back(l.second);
		fanout[l.first]++;
		for (rn_t rn : {l.first, l.second}) {
			auto pn = cv->rnode_to_pnode(rn);
			if (!pn)
				continue;
			auto bt = CycloneV::pn2bt(pn);
			if (bt == CycloneV::LAB || bt == CycloneV::MLAB || bt == CycloneV::M10K || bt == CycloneV::DSP)
				used_tiles.insert(CycloneV::pn2p(pn));
		}
	}

	// rxd: the node driving the HPS UART's RXD port
	// the second HPS UART (Linux /dev/ttyS1) is the one sys/ wires to the core
	auto hp = cv->hps_get_pos()[CycloneV::I_HPS_PERIPHERAL_UART + 1];
	rn_t rxd = cv->pnode_to_rnode(CycloneV::pnode(CycloneV::HPS_PERIPHERAL_UART, hp, CycloneV::RXD, -1, -1));
	printf("rxd %s\n", rxd ? CycloneV::rn2s(rxd).c_str() : "-");
	// rxdfree: the stock core's private wire into RXD (nodes that only carry its UART signal), free to reuse
	printf("rxdfree ");
	for (rn_t w = rxd, n = 0; w && bsrc.count(w); w = bsrc[w]) {
		rn_t s = bsrc[w];
		if (fanout[s] != 1 || cv->rnode_to_pnode(s))
			break;                                      // shared, or a block's own output: the core's
		printf("%s%s", n++ ? "," : "", CycloneV::rn2s(s).c_str());
	}
	printf("\n");

	// clk: CMUXHG/CMUXVG outputs whose INPUT_SEL picks a clock pin, the one with the most fanout
	rn_t clk = 0;
	int best = 0;
	struct { CycloneV::block_type_t bt; const std::pair<uint8_t, uint8_t> (*table)[64]; const std::vector<CycloneV::pos_t> *pos; } muxes[] = {
		{ CycloneV::CMUXHG, CycloneV::cmuxhg_link_table, &cv->cmuxh_get_pos() },
		{ CycloneV::CMUXVG, CycloneV::cmuxvg_link_table, &cv->cmuxv_get_pos() },
	};
	for (auto &m : muxes)
		for (auto pos : *m.pos)
			for (int bi = 0; bi < 4; bi++) {
				rn_t out = cv->pnode_to_rnode(CycloneV::pnode(m.bt, pos, CycloneV::CLKOUT, bi, -1));
				if (!out || !fanout.count(out))
					continue;
				CycloneV::bmux_setting_t bs;
				if (!cv->bmux_get(m.bt, pos, CycloneV::INPUT_SEL, bi, bs))
					continue;
				uint32_t sel = bs.r.empty() ? bs.s : bs.r[0];
				auto link = m.table[bi][sel & 63];
				const char *what = CycloneV::cmux_link_names[link.first];
				bool pin = !strncmp(what, "CLKPIN", 6);
				int fo = 0;     // whole tree size
				std::vector<rn_t> st{out};
				std::unordered_set<rn_t> seen;
				while (!st.empty()) {
					rn_t w = st.back();
					st.pop_back();
					if (!seen.insert(w).second)
						continue;
					fo++;
					for (auto x : down[w])
						st.push_back(x);
				}
				fprintf(stderr, "  %s.%d %s: %s.%d, tree %d\n", CycloneV::rn2s(out).c_str(), bi,
				        CycloneV::block_type_names[m.bt], what, link.second, fo);
				if (pin && fo > best) {
					best = fo;
					clk = out;
				}
			}
	printf("clk %s\n", clk ? CycloneV::rn2s(clk).c_str() : "-");
	if (!clk)
		return 0;

	// tiles: free LABs reachable from the clock's tree through free clock routing (CLKIN 0, nextpnr's LAB clock)
	std::unordered_set<rn_t> shared;
	std::vector<rn_t> st{clk};
	while (!st.empty()) {
		auto w = st.back();
		st.pop_back();
		if (shared.count(w))
			continue;
		shared.insert(w);
		blocked.erase(w);
		for (auto x : down[w])
			st.push_back(x);
	}
	std::unordered_map<rn_t, std::vector<rn_t>> up;
	for (const auto &r : cv->rnodes())
		for (auto s : r.sources())
			up[r.id()].push_back(s);
	// a free LAB is only usable if its inputs can still be reached: each input node needs a free feed
	std::unordered_map<int, std::pair<int, int>> inputs;   // pos -> (inputs, inputs with 2+ free feeds)
	for (const auto &r : cv->rnodes()) {
		if (CycloneV::rn2t(r.id()) != CycloneV::GOUT)
			continue;
		auto pn = cv->rnode_to_pnode(r.id());
		if (!pn || CycloneV::pn2bt(pn) != CycloneV::LAB || used_tiles.count(CycloneV::pn2p(pn)))
			continue;
		int fr = 0;
		for (auto s : r.sources())
			fr += !blocked.count(s);
		auto &c = inputs[CycloneV::pn2p(pn)];
		c.first++;
		c.second += fr >= 2;
	}
	printf("tiles ");
	int ok = 0, tot = 0, cut = 0;
	for (auto p : cv->lab_get_pos()) {
		if (used_tiles.count(p))
			continue;
		tot++;
		auto in = inputs[p];
		if (!in.first || in.second * 10 < in.first * 9) {
			cut++;
			continue;                             // boxed in by the stock core's routing
		}
		rn_t rn = cv->pnode_to_rnode(CycloneV::pnode(CycloneV::LAB, p, CycloneV::CLKIN, -1, 0));
		if (!rn)
			continue;
		bool reach = false;
		std::queue<std::pair<rn_t, int>> q;
		std::unordered_set<rn_t> seen{rn};
		q.push({rn, 0});
		while (!q.empty() && !reach) {
			auto [w, dd] = q.front();
			q.pop();
			if (dd > 12 || blocked.count(w))
				continue;
			for (auto s : up[w]) {
				if (blocked.count(s) && !shared.count(s))
					continue;
				if (shared.count(w) && bsrc.count(w) && bsrc[w] != s)
					continue;
				if (shared.count(s)) {
					reach = true;
					break;
				}
				auto t = CycloneV::rn2t(s);
				if (t == CycloneV::H14 || t == CycloneV::H6 || t == CycloneV::H3 || t == CycloneV::V12 ||
				    t == CycloneV::V2 || t == CycloneV::V4 || t == CycloneV::WM)
					continue;
				if (seen.insert(s).second)
					q.push({s, dd + 1});
			}
		}
		if (reach) {
			printf("%s%d,%d", ok ? ";" : "", CycloneV::pos2x(p), CycloneV::pos2y(p));
			ok++;
		}
	}
	printf("\n");
	fprintf(stderr, "free LABs %d, boxed in %d, reachable %d\n", tot, cut, ok);
	return 0;
}
