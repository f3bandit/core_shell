// baselinks: every routing link of a core, as the current Mistral decodes it (clock-mux leaves included,
// which the Mistral nextpnr is pinned to does not see).  baselinks CORE.rbf OUT.txt
// Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE). Built on Mistral (BSD 3-Clause).
#include "cyclonev.h"
#include <cstdio>
#include <fstream>
#include <vector>
using namespace mistral;
int main(int argc,char**argv){ auto cv=CycloneV::get_model("5CSEBA6U23I7");
 std::ifstream f(argv[1],std::ios::binary); std::vector<uint8_t> d((std::istreambuf_iterator<char>(f)),{}); cv->rbf_load(d.data(),d.size());
 FILE*o=fopen(argv[2],"w"); size_t n=0; for(auto&l: cv->route_all_active_links()){ fprintf(o,"%s %s\n",cv->ri2rc(l.first).to_string().c_str(),cv->ri2rc(l.second).to_string().c_str()); n++; } fclose(o); printf("%zu links\n",n);}
