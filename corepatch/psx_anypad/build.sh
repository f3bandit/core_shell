#!/bin/sh
# build.sh: the PSX "any pad" Super MemCard patch for PSX_20260807 (on a PC).
#
#   build.sh STOCK.rbf OUTDIR
#
# The real memory cards in the front ports under any Pad type (SNAC MemCard = Real; a Pad set to Off keeps its
# virtual card; Pad = SNAC-portN, SNAC MemCard = Virtual or multitap: stock). Six LUTs placed into ALMs the stock
# core leaves unused (free_alms.txt), no flip-flops; three stock nodes taken over (the two ports' SNAC decodes,
# snacMCEN's clear) and four stock LUTs changed (lutpatch). Tools: yosys, NEXTPNR (nextpnr-mistral with
# ../nextpnr-base.patch), LUTPATCH (../tools/lutpatch), BASELINKS + BASELINKS_PINNED (../tools), ../mkpatch.py.
# Then check.py on the result. Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
set -e
STOCK=$1; OUT=${2:-.}; HERE=$(cd "$(dirname "$0")" && pwd)
NEXTPNR=${NEXTPNR:-nextpnr-mistral}; LUTPATCH=${LUTPATCH:-lutpatch}
BASELINKS=${BASELINKS:-baselinks}; BASELINKS_PINNED=${BASELINKS_PINNED:-baselinks_pinned}
[ "$(md5sum "$STOCK" | cut -d' ' -f1)" = 5c67a2d7dbd01f723a4cf9125460c095 ] || { echo "not PSX_20260807.rbf"; exit 1; }
mkdir -p "$OUT"; W=$(mktemp -d)
yosys -q -p "read_verilog $HERE/anypad.v; synth_intel_alm -family cyclonev -noclkbuf -top top; write_json $W/d.json"
# routing only the current Mistral decodes (clock-mux leaves): off-limits, as build_patch.py does
"$BASELINKS" "$STOCK" $W/all.links >/dev/null; "$BASELINKS_PINNED" "$STOCK" $W/pinned.links >/dev/null
sort $W/pinned.links > $W/p.s; sort $W/all.links | comm -23 - $W/p.s | tr ' ' '\n' | sort -u > $W/block.txt
# taps: the base nodes read (P0 and Z2 from their nets' first free wires: their own outputs reach no free wiring)
NPNR_BASE_RBF="$STOCK" NPNR_BLOCK_FILE=$W/block.txt NPNR_FREE_ALMS="$(cat $HERE/free_alms.txt)" \
NPNR_TAPS=P4=GIN.029.044.0019,P0=H3.027.044.0009,P1=GIN.023.044.0004,P3=GIN.029.044.0007,U2=GIN.024.048.0033,V2=GIN.024.048.0011,W2=GIN.024.048.0023,Z2=H6.024.048.0006,MT=GIN.027.042.0026,MC=GIN.048.019.0032,TA=GIN.048.019.0030,TB=GIN.043.032.0019,TC=GIN.048.019.0033,TD=GIN.043.032.0020,J13=GIN.034.035.0002,BT=GIN.042.035.0021,DE=GIN.053.035.0024,X=GIN.042.035.0032 \
NPNR_SINKS=SP1=GOUT.029.044.0023,SP2=GOUT.029.044.0076,CLR=GOUT.042.035.0020 \
"$NEXTPNR" --device 5CSEBA6U23I7 --router router1 --json $W/d.json --rbf $W/routed.rbf > $W/nextpnr.log 2>&1 || { tail -5 $W/nextpnr.log; exit 1; }
# the stock LUTs that read the taken-over nodes: each one passes its new input through
"$LUTPATCH" $W/routed.rbf $W/s1.rbf LAB 29 44 2 fff7ffff20ff00ff ffff000020ff00ff   # port 1 SNAC decode (F1)
"$LUTPATCH" $W/s1.rbf $W/s2.rbf LAB 29 44 9 afffffffffcfffff affffffff0f0f0f0       # port 2 SNAC decode (C)
"$LUTPATCH" $W/s2.rbf $W/s3.rbf LAB 42 35 2 ffff000800080008 ffff00ff0f0f0f0f       # snacMCEN: (set | q) & ~C
"$LUTPATCH" $W/s3.rbf $W/final.rbf LAB 48 19 8 f7ffffffffffffff f7ffffffff7fffff    # 0x81's five bits, no SNAC MemCard
python3 "$HERE/../mkpatch.py" "$STOCK" $W/final.rbf "$OUT/PSX_20260807.memcard.cspatch" "$OUT/PSX_20260807.patched.rbf" \
	--name "SuperMC (real cards, any pad type)"
rm -rf $W
