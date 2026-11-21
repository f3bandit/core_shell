// baselinks_pinned: baselinks for the Mistral revision nextpnr is pinned to (rn2s names).
#include "cyclonev.h"
#include <cstdio>
#include <fstream>
#include <vector>
using namespace mistral;
int main(int argc,char**argv){ auto cv=CycloneV::get_model("5CSEBA6U23I7");
 std::ifstream f(argv[1],std::ios::binary); std::vector<uint8_t> d((std::istreambuf_iterator<char>(f)),{}); cv->rbf_load(d.data(),d.size());
 FILE*o=fopen(argv[2],"w"); size_t n=0; for(auto&l: cv->route_all_active_links()){ fprintf(o,"%s %s\n",CycloneV::rn2s(l.first).c_str(),CycloneV::rn2s(l.second).c_str()); n++; } fclose(o); printf("%zu links\n",n);}
