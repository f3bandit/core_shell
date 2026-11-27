# Core patches: Super MemCard's only

A core patch changes a **stock** core's bitstream on the MiSTer, as the core loads, without its source and without
Quartus. Coreshell applies **only Super MemCard patches**
(`patches/<core>.memcard.cspatch`), and only when a launch needs one, or with `"memcard_patch": true` in
`coreshell/settings/coreshell.json` for every launch (off by default). Today that's one: the PSX core with the real
memory cards in the front ports under any controller type (the stock core only lets a card in when that Pad is the
front port's controller, SNAC-portN).

`PSX_20260807.memcard.cspatch` (Nov 27 2026) makes the core's own options mean what they say: **SNAC MemCard =
Real** is the memory cards in the front ports with any Pad type from USB (NeGcon, DualShock, GunCon...: a pad command
goes to the core's own pad of that type, a card command to the card in that front port; only the first byte a game
sends after selecting the port counts). A Pad set to Off keeps its virtual card; Pad = SNAC-portN, SNAC MemCard =
Virtual and multitap are stock. Coreshell uses the patched core when the core will run with SNAC MemCard = Real and
a Pad that is neither SNAC-portN nor Off (its .CFG with what Coreshell sets over it); nothing else is set, with any
Main. (It replaces the first patch, Nov 22, digital pads only, picked as a Pad choice "USB pad + front-port card":
old picks become SNAC MemCard = Real with the Pad they had.) Built by `psx_anypad/build.sh`, checked by
`psx_anypad/check.py` (every input of the changed logic, and the stock core's behaviour from the same checker).

No core is patched for the front port's controller. Controllers are USB (or Bluetooth) in Main, the frontend and
every core, as on a stock MiSTer. (A front-port pad patch, `frontpad.v`, was built and tested on NES and SNES on
Nov 14 2026 and dropped: it's in the git history only.)

No modified core is ever distributed: a patch holds only the change and is refused for any file but the exact
stock core it was made for.

## How a patch is made (on a PC)

A change to logic the core already has (the PSX card's): `tools/lutpatch` (libmistral) changes one ALM's LUT mask
(and input mode) and refuses unless the ALM is what it was worked out on; then `mkpatch.py` makes the difference.

A few LUTs with no flip-flops (the PSX patch): nextpnr places them into ALMs the stock core leaves unused inside its
own logic blocks (`NPNR_FREE_ALMS`; the block's clock and clear lines stay the stock core's), reads stock nodes
(`NPNR_TAPS` NAME=RNODE) and takes over the stock LAB inputs it drives (`NPNR_SINKS`); `lutpatch` then makes the
stock LUTs behind those inputs pass them through. A free ALM ahead of a stock adder chain has to be checked by hand: the
chain must not take its carry from it (the PSX patch: LAB 030.046's chain makes its own carry at ALM 2, so it reads
none from the new ALM 0; the other new ALMs have no stock chain after them).

New logic in free space: `build_patch.py STOCK.rbf --design DESIGN.v --name memcard.vN` (it refuses any other name):

1. `tools/coremap` (libmistral): the nodes the design connects to, a global clock fed straight from a board clock
   pin, and the free logic blocks that clock reaches.
2. `tools/baselinks`: the stock core's routing as the current Mistral decodes it; the clock-mux leaves the pinned
   Mistral can't see are blocked too.
3. yosys: the design.
4. nextpnr-mistral with `nextpnr-base.patch` (`NPNR_BASE_RBF`): placed into the densest group of free blocks,
   routed only through routing the stock core doesn't use (a shared node keeps its stock source), retried without
   any block the stock core's routing boxes in. It must reach 60 MHz or no patch is made.
5. `tools/coreverify`: every stock link kept but the ones into nodes taken over on purpose, every stock block
   setting unchanged, and nothing added touches a stock clock mux.
6. `mkpatch.py`: the difference as `<core>.memcard.vN.cspatch`.

## How it's applied (on the MiSTer)

`corepatch PATCH STOCK.rbf OUT.rbf` (C, static): checks STOCK's MD5, decompresses the logic frames (Cyclone V's
nibble compression), writes the patch's bytes (CRCs included), recompresses, keeps the stock file's own end, and
writes OUT only if its MD5 is the one the patch was checked for. Under a second on the SuperStation.

## Credits

Built on [Mistral](https://github.com/Ravenslofty/mistral) (Olivier Galibert and contributors, BSD 3-Clause), the
open Cyclone V bitstream library, and [nextpnr](https://github.com/YosysHQ/nextpnr) (ISC). `nextpnr-base.patch`
applies to nextpnr at the revision in `nextpnr-base.rev`, built against Mistral `ebfc0dd`. Mistery
(meathax, BSD 3-Clause) was used to study the stock cores.
