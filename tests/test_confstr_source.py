"""CONF_STR read from a core's source: what can't be read is refused in strict mode, not left out."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from coreshell import confstr as cs  # noqa: E402

PLAIN = '''
`define SEP "-;"
localparam CONF_STR = {
    "NES;;",
    `SEP
    "O1,Option,Off,On;",
    "V,v",`BUILD_DATE
};
'''

# the PCXT core from Nov 2026: its menu is built from localparams and a macro that isn't a string
PARTS = '''
`define CONF_STR_SYSTEM (`ENABLE_MIDI ? {`CORE_NAME, ";UART;"} : {`CORE_NAME, ";"})
localparam CONF_STR_HALT = "O1,Halt,Off,On;";
localparam CONF_STR = {
    `CONF_STR_SYSTEM,
    CONF_STR_HALT,
    "S0,IMGIMAVFD,Floppy A:;",
    "-;"
};
'''


class TestSource(unittest.TestCase):
    def test_plain_same_in_strict(self):
        loose = cs.extract_from_verilog(PLAIN)
        self.assertEqual(loose, 'NES;;-;O1,Option,Off,On;V,vBUILD')
        self.assertEqual(cs.extract_from_verilog(PLAIN, strict=True), loose)

    def test_parts_refused_in_strict(self):
        # loose: only the literal pieces are left, so the core's name would be read as "S0,IMGIMAVFD,Floppy A:"
        self.assertTrue(cs.extract_from_verilog(PARTS).startswith('S0,IMGIMAVFD'))
        with self.assertRaises(ValueError):
            cs.extract_from_verilog(PARTS, strict=True)

    def test_bare_localparam_refused(self):
        src = 'localparam CONF_STR = {\n "NES;;",\n CONF_STR_HALT,\n "-;"\n};\n'
        with self.assertRaises(ValueError):
            cs.extract_from_verilog(src, strict=True)


if __name__ == '__main__':
    unittest.main()
