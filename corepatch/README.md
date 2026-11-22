# Core patches: Super MemCard's only

A core patch changes a **stock** core's bitstream on the MiSTer, as the core loads, without its source and without
Quartus. Coreshell applies **only Super MemCard patches** (`patches/<core>.memcard.cspatch`), and only when the user has
turned them on: **SuperCard**, the Pad1 or Pad2 choice "USB pad + front-port card" in the core's menu (per system or per
game; the choice is only there when a patch for the installed core is, and SNAC MemCard then shows "Real": the
core's own labels say the opposite of what the patched core does with them), or `"memcard_patch": true` in `coreshell/settings/coreshell.json` for
every launch (off by default). Today that's one: the PSX core reaching the real card in the front port while a USB pad
plays (the stock core only lets the card in when Pad1 is the front port's controller).

`PSX_20260807.memcard.cspatch` turns the core's virtual-card switch round: with SNAC MemCard on "Virtual", a pad
command (01) goes to the core's own pad (USB) and a card command (81) stays on the front port. Only the first byte
a game sends after selecting the port counts (Nov 22 2026: the first patch read a byte left over from the previous
exchange, so card reads after a pad read went to the virtual card). A SuperCard launch
runs the patched core with Pad1 = SNAC-port1 and SNAC MemCard = Virtual for that launch only (the handoff's status
bits), so the core's own Pad1 and SNAC MemCard options stay as the user left them. It needs a Main that takes the
handoff (Phosphor's, corehost); with another Main the launch says so and starts without it.

No core is patched for the front port's controller. Controllers are USB (or Bluetooth) in Main, the frontend and
every core, as on a stock MiSTer. (A front-port pad patch, `frontpad.v`, was built and tested on NES and SNES on
Nov 14 2026 and dropped: it's in the git history only.)

No modified core is ever distributed: a patch holds only the change and is refused for any file but the exact
stock core it was made for.

## How a patch is made (on a PC)

A change to logic the core already has (the PSX card's): `tools/lutpatch` (libmistral) changes one ALM's LUT mask
(and input mode) and refuses unless the ALM is what it was worked out on; then `mkpatch.py` makes the difference.

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
