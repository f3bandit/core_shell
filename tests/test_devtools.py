"""devtools.py: the conflicts a Main fork leaves when the Super MemCard shim is applied, settled the way the shim needs
(the ones seen building RA, Zaparoo, Physical-CD), and the Main info."""
import os, tempfile, unittest
from coreshell import devtools as dt

ROW_RA = ('\t\t\tif (!page && achievements_has_active_game())\n\t\t\t{\n\t\t\t\tra_submenu = selentry;\n'
          '\t\t\t\tMenuWrite(entry, " Achievements", menusub == selentry, 0);\n')
ROW_SMC = ('\t\t\t// Super MemCard\n\t\t\tif (!page && smc_osd_available())\n\t\t\t{\n\t\t\t\tsmc_submenu = selentry;\n'
           '\t\t\t\tMenuWrite(entry, " Saves", menusub == selentry, 0);\n')
SEL_SMC = ('\t\t\tif (smc_submenu == menusub && select)\n\t\t\t{\n\t\t\t\tif (smc_osd_open())\n\t\t\t\t{\n'
           '\t\t\t\t\tmenustate = MENU_SMC_SAVES1;\n\t\t\t\t}\n\t\t\t}\n\t\t\telse if (manual_submenu == menusub)\n')


class Settle(unittest.TestCase):
    def test_includes(self):
        self.assertEqual(dt._settle('#include "a.h"\n', '#include "smc_shim.h"\n', 'x'), '#include "a.h"\n#include "smc_shim.h"\n')

    def test_row_each_its_own_block(self):
        out = dt._settle(ROW_RA, ROW_SMC, 'menu.cpp')
        self.assertEqual(out.count('menumask = (menumask << 1) | 1;'), 1)        # (the RA row's own end, put in)
        self.assertLess(out.index('" Achievements"'), out.index('" Saves"'))

    def test_selection_before_the_manual(self):          # (Zaparoo's: the fork's rows end in the manual's)
        ours = ('\t\t\tif (ra_menu_row_selected(menusub, select))\n\t\t\t{\n\t\t\t\tx();\n\t\t\t}\n\t\t\telse\n'
                '\t\t\tif (manual_submenu == menusub)\n')
        out = dt._settle(ours, SEL_SMC, 'menu.cpp')
        self.assertIn('else\n\t\t\tif (smc_submenu == menusub && select)', out)
        self.assertTrue(out.rstrip().endswith('else if (manual_submenu == menusub)'))
        self.assertEqual(out.count('manual_submenu'), 1)

    def test_selection_after_the_forks(self):            # (RA's: its own branch, then the shim's)
        ours = '\t\t\tif (ra_submenu == menusub && select)\n\t\t\t{\n\t\t\t\tint n = open();\n'
        out = dt._settle(ours, SEL_SMC.replace('\t\t\telse if (manual_submenu == menusub)\n', ''), 'menu.cpp')
        self.assertIn(dt._SEL_TAIL + '\t\t\tif (smc_submenu', out)

    def test_poll_and_busy(self):
        out = dt._settle('\t\t\t{ P p(1); user_io_poll(); }\n\t\t\tframe_timer();\n',
                         '\t\t\tuser_io_poll();\n\t\t\tsmc_shim_poll();\n', 'scheduler.cpp')
        self.assertIn('user_io_poll(); }\n\t\t\tsmc_shim_poll();', out)
        out = dt._settle('\t\tif (is_menu() && x()) timeout = 25;\n', '\t\tif (is_menu()) timeout = smc_shim_busy() ? 1 : 25;\n', 'input.cpp')
        self.assertIn('if (is_menu() && x()) timeout = smc_shim_busy() ? 1 : 25;', out)

    def test_leave_game_after_the_forks(self):       # (Zaparoo's save-on-exit before the load)
        ours = '\tif (zaparoo_save_defer_core_load(name, cfg, xml)) return 0;\n'
        out = dt._settle(ours, '\tsmc_shim_leave_game();\n', 'fpga_io.cpp')
        self.assertLess(out.index('zaparoo_save'), out.index('smc_shim_leave_game'))

    def test_unknown_left_for_a_person(self):
        self.assertIsNone(dt._settle('\tfoo();\n', '\tbar();\n', 'x.cpp'))


class OldMain(unittest.TestCase):
    def test_hunks_by_hand(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, 'menu.cpp'), 'w').write(
            '\tstatic uint32_t dip_submenu;\n...\n\t\t\tdip_submenu = -1;\n...\n\t\t\tif (dip_submenu == menusub && select)\n')
        open(os.path.join(d, 'input.cpp'), 'w').write(
            '#include "frame_timer.h"\n\t\tif (is_menu() && video_fb_state()) timeout = 25;\n')
        open(os.path.join(d, 'fpga_io.cpp'), 'w').write(
            'static int fpga_load_rbf_inner(const char *name, const char *cfg)\n{\n'
            '\tif (strcasecmp(name, "menu.rbf")) smc_shim_core_start(name, xml);   // Super MemCard: the game about to run\n'
            '\treturn 0;\n}\n'
            'int fpga_load_rbf_no_restart(const char *name)\n{\n\treturn fpga_load_rbf_inner(name, nullptr);\n}\n'
            'int fpga_load_rbf(const char *name, const char *cfg, const char *xml)\n{\n'
            '\tint ret = fpga_load_rbf_inner(name, cfg);\n\treturn ret;\n}\n')
        dt.settle_old_main(d)
        m = open(os.path.join(d, 'menu.cpp')).read()
        self.assertIn('static uint32_t smc_submenu;', m)
        self.assertIn('smc_submenu = -1;', m)
        self.assertIn('else if (dip_submenu == menusub && select)', m)
        i = open(os.path.join(d, 'input.cpp')).read()
        self.assertIn('#include "smc_shim.h"', i)
        self.assertIn('smc_shim_busy() ? 1 : 25', i)
        f = open(os.path.join(d, 'fpga_io.cpp')).read()
        inner = f[:f.index('fpga_load_rbf_no_restart')]
        self.assertNotIn('smc_shim_core_start', inner)
        self.assertIn('smc_shim_core_start(name, NULL);', f)
        self.assertIn('smc_shim_core_start(name, xml);   // Super MemCard: the game about to run\n\tint ret', f)
        self.assertEqual(f.count('smc_shim_leave_game();'), 2)
        dt.settle_old_main(d)                            # (twice: nothing more)
        self.assertEqual(open(os.path.join(d, 'menu.cpp')).read(), m)


class Info(unittest.TestCase):
    def test_main_info(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'MiSTer_RA')
        open(p, 'wb').write(b'\x7fELF ... $VER:261010 ... achievements_has_active_game ... /tmp/smcd.sock')
        i = dt.main_info(p)
        self.assertEqual((i['version'], i['fork'], i['shim']), ('261010', ['ra'], True))
        open(p, 'wb').write(b'\x7fELF /tmp/phosphor-main.sock quake-mister physical_disc')
        self.assertEqual(dt.main_info(p)['fork'], ['phosphor'])


if __name__ == '__main__':
    unittest.main()


def test_link_for_ra_only():
    """Phosphor's in-game link (super-memcard main/link) goes into MiSTer_RA's shim build; a --link folder without
    hooks.py is an error, never a build without it said nowhere."""
    import tempfile
    import pytest
    from coreshell import devtools as dt
    assert dt.FORKS['ra'].get('link') is True
    assert not any(f.get('link') for k, f in dt.FORKS.items() if k != 'ra')
    with pytest.raises(RuntimeError):
        dt._link(tempfile.mkdtemp(), tempfile.mkdtemp(), 'ra')
