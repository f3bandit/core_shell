// PSX: the real memory cards in the front ports under any pad type (SNAC MemCard = Real), for Super MemCard.
// Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
// Six LUTs, no flip-flops: they drive three stock nodes the patch takes over, and the stock flip-flops read them.
//   SP1/SP2: port 1's / port 2's "SNAC" decode (joypad's snacPortN and PSX.sv's): SNAC-portN, or with SNAC MemCard
//            = Real any type but Off
//   CLR: snacMCEN's clear (into LAB 042.035 ALM 2, whose LUT then reads it alone): the stock clear (81 with the
//        virtual card), or, with a real card under another pad type, the first byte after selecting that port when
//        it isn't 81 (a pad command goes to the core's own pad of that type; a card command stays on the front port)
(* blackbox *) module MISTRAL_TAP (output Q);   endmodule  // a base node read (NPNR_TAPS)
(* blackbox *) module MISTRAL_SINK (input D);   endmodule  // a base node taken over (NPNR_SINKS)

module top;
	wire p4, p0, p1, a1q5, u2, v2, w2, z2, mt, mc, tA, tB, tC, tD, j13, bt, dE, X;
	(* keep, PIN = "P4" *) MISTRAL_TAP t0 (.Q(p4));
	(* keep, PIN = "P0" *) MISTRAL_TAP t1 (.Q(p0));
	(* keep, PIN = "P1" *) MISTRAL_TAP t2 (.Q(p1));
	(* keep, PIN = "P3" *) MISTRAL_TAP t3 (.Q(a1q5));
	(* keep, PIN = "U2" *) MISTRAL_TAP t4 (.Q(u2));
	(* keep, PIN = "V2" *) MISTRAL_TAP t5 (.Q(v2));
	(* keep, PIN = "W2" *) MISTRAL_TAP t6 (.Q(w2));
	(* keep, PIN = "Z2" *) MISTRAL_TAP t7 (.Q(z2));
	(* keep, PIN = "MT" *) MISTRAL_TAP t8 (.Q(mt));
	(* keep, PIN = "MC" *) MISTRAL_TAP t9 (.Q(mc));
	(* keep, PIN = "TA" *) MISTRAL_TAP t10 (.Q(tA));
	(* keep, PIN = "TB" *) MISTRAL_TAP t11 (.Q(tB));
	(* keep, PIN = "TC" *) MISTRAL_TAP t12 (.Q(tC));
	(* keep, PIN = "TD" *) MISTRAL_TAP t13 (.Q(tD));
	(* keep, PIN = "J13" *) MISTRAL_TAP t14 (.Q(j13));
	(* keep, PIN = "BT" *) MISTRAL_TAP t15 (.Q(bt));
	(* keep, PIN = "DE" *) MISTRAL_TAP t16 (.Q(dE));
	(* keep, PIN = "X" *) MISTRAL_TAP t17 (.Q(X));
	wire snac1, snac2, any1, any2, pad, clr;
	// Pad1 (status[48:45] = p1 p0 p3 p4): SNAC-port1 1010, Off 0001; MT multitap; MC SNAC MemCard = Real
	MISTRAL_ALUT6 #(.LUT(64'h0000fffd00001000)) l_snac1 (.A(p4), .B(p0), .C(p1), .D(a1q5), .E(mt), .F(mc), .Q(snac1));
	MISTRAL_ALUT5 #(.LUT(32'heffd0000)) l_any1 (.A(p4), .B(p0), .C(p1), .D(a1q5), .E(mc), .Q(any1));
	// Pad2 (status[52:49] = u2 v2 w2 z2): SNAC-port2 is 1010, Off 0001
	MISTRAL_ALUT6 #(.LUT(64'h0000feff00000020)) l_snac2 (.A(u2), .B(v2), .C(w2), .D(z2), .E(mt), .F(mc), .Q(snac2));
	MISTRAL_ALUT5 #(.LUT(32'hfedf0000)) l_any2 (.A(u2), .B(v2), .C(w2), .D(z2), .E(mc), .Q(any2));
	// the first byte after selecting a port: J13 port 2 selected, BT beginTransfer, DE delayEnableSnac, X select edge
	MISTRAL_ALUT6 #(.LUT(64'hca00ca00ca000000)) l_pad (.A(any1), .B(any2), .C(j13), .D(bt), .E(dE), .F(X), .Q(pad));
	// TA TB TC TD: transmitBuffer = 81 (TC: the stock decode of its other five bits, SNAC MemCard taken out of it)
	MISTRAL_ALUT6 #(.LUT(64'hefffffff00001000)) l_clr (.A(tA), .B(tB), .C(tC), .D(tD), .E(mc), .F(pad), .Q(clr));
	(* keep, NODE = "SP1" *) MISTRAL_SINK s0 (.D(snac1));
	(* keep, NODE = "SP2" *) MISTRAL_SINK s1 (.D(snac2));
	(* keep, NODE = "CLR" *) MISTRAL_SINK s2 (.D(clr));
endmodule
