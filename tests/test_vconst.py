"""coreshell/vconst.py: CONF_STR worked out as Quartus would (macros, `ifdef, localparams, ? :)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from coreshell import vconst as vc  # noqa: E402

# laid out like the PCXT core of Nov 2026: the .qsf sources config.tcl, which picks a build profile
REPO = {
    'CORE.qsf': '''set_global_assignment -name TOP_LEVEL_ENTITY sys_top
#set_global_assignment -name VERILOG_MACRO "MISTER_FB=1"
source config.tcl
source sys/sys.tcl
source files.qip
''',
    'config.tcl': '''# build profiles
foreach v [info vars core_*] { unset $v }
set core_profile full
if {[info exists ::env(CORE_PROFILE)] && $::env(CORE_PROFILE) ne ""} {
    set core_profile $::env(CORE_PROFILE)
}
set core_profile_file [file join [file dirname [info script]] profiles ${core_profile}.tcl]
source $core_profile_file
''',
    'profiles/full.tcl': '''set_global_assignment -name VERILOG_MACRO "LAYOUT_EGA=1"
set_global_assignment -name VERILOG_MACRO "ENABLE_EGA=1"
set_global_assignment -name VERILOG_MACRO "ENABLE_MODEL=1"
''',
}

SV = r'''
`ifndef ENABLE_EGA
`define ENABLE_EGA 0
`endif
`ifndef ENABLE_MODEL
`define ENABLE_MODEL 0
`endif
`define ENABLE_MIDI 1
`define CORE_NAME (`ENABLE_MODEL ? "PC" : "XT")    // the name
`define CONF_SYSTEM (`ENABLE_MIDI ? {`CORE_NAME, ";UART,MIDI;"} : {`CORE_NAME, ";"})
module emu;
`ifdef LAYOUT_EGA
    localparam bit L_EGA = 1'b1;
`else
    localparam bit L_EGA = 1'b0;
`endif
    localparam bit HAS_EGA = (`ENABLE_EGA != 0);
    localparam bit HAS_MODEL = (`ENABLE_MODEL != 0) && L_EGA;
    localparam H2 = HAS_MODEL ? "h2" : "";
    localparam CONF_CARD = HAS_EGA ? {H2, "O[78],Video,CGA,EGA;"} : "";
    /* a block comment; with "quotes" */
    localparam CONF_STR = {
        `CONF_SYSTEM,
        "S0,IMG,Floppy A:;",
`ifdef LAYOUT_EGA
        CONF_CARD,
`endif
        {2{"-;"}},
        "I,a\nb;",
        "V,v",`BUILD_DATE
    };
endmodule
'''


def read(path):
    return REPO.get(path)


class TestVconst(unittest.TestCase):
    def test_build_macros_through_tcl_profile(self):
        m = vc.build_macros(read, 'CORE.qsf')
        self.assertEqual(m, {'LAYOUT_EGA': '1', 'ENABLE_EGA': '1', 'ENABLE_MODEL': '1'})

    def test_conf_str_with_build_macros(self):
        m = vc.build_macros(read, 'CORE.qsf')
        self.assertEqual(vc.conf_str(SV, m),
                         'PC;UART,MIDI;S0,IMG,Floppy A:;h2O[78],Video,CGA,EGA;-;-;I,a\nb;V,vBUILD')

    def test_conf_str_without_macros(self):
        # nothing from the build: the source's own defaults, and the `ifdef LAYOUT_EGA parts left out
        self.assertEqual(vc.conf_str(SV), 'XT;UART,MIDI;S0,IMG,Floppy A:;-;-;I,a\nb;V,vBUILD')

    def test_unknown_name_is_unreadable(self):
        with self.assertRaises(vc.Unreadable):
            vc.conf_str('localparam CONF_STR = {"A;;", SOMETHING_ELSE};')

    def test_unknown_macro_is_unreadable(self):
        with self.assertRaises(vc.Unreadable):
            vc.conf_str('localparam CONF_STR = {"A;;", `NOT_DEFINED};')

    def test_macro_with_arguments_is_unreadable(self):
        with self.assertRaises(vc.Unreadable):
            vc.conf_str('`define F(x) x\nlocalparam CONF_STR = {"A;;", `F("b")};')

    def test_declared_twice_is_unreadable(self):
        # (two declarations the preprocessor can't choose between, e.g. in generate blocks)
        with self.assertRaises(vc.Unreadable):
            vc.conf_str('localparam X = "a;";\nlocalparam X = "b;";\nlocalparam CONF_STR = {"A;;", X};')

    def test_unresolved_tcl_source_is_unreadable(self):
        repo = {'C.qsf': 'source $nowhere\n'}
        with self.assertRaises(vc.Unreadable):
            vc.build_macros(repo.get, 'C.qsf')

    def test_numbers(self):
        self.assertEqual(vc.conf_str("localparam N = 4'b0010 + 'h1;\nlocalparam CONF_STR = N == 3 ? \"y\" : \"n\";"),
                         'y')


if __name__ == '__main__':
    unittest.main()
