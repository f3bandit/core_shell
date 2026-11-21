import json
import os
import shutil
import tempfile
import unittest

from coreshell import addons as ad
from coreshell import takeover as to


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Takeover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, 'coreshell')
        os.makedirs(os.path.join(self.home, 'state'))
        os.makedirs(os.path.join(self.home, 'addons'))
        self.main = os.path.join(self.tmp, 'phosphor', 'MiSTer_phosphor')
        os.makedirs(os.path.dirname(self.main))
        open(self.main, 'w').close()
        os.chmod(self.main, 0o755)
        with open(os.path.join(self.home, 'state', 'frontend.json'), 'w') as f:
            json.dump({'name': 'Phosphor', 'main': self.main}, f)
        self.running = {1: ('MiSTer_phosphor', self.main)}
        self.events, self.started, self.killed = [], [], []
        self.clock = Clock()
        self.menu = True
        self.procs = {}                                    # pid -> exe of every process (beyond the Mains)
        self.t = to.Takeover(self.home, self.tmp, emit=lambda n, **k: self.events.append((n, k)),
                             mains=lambda: dict(self.running), start=self.started.append,
                             kill=self.killed.append, clock=self.clock, in_menu=lambda: self.menu,
                             exes=lambda: dict(self.procs))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def tick(self, s=0.25, held=False):
        self.clock.t += s
        self.t.step(held)

    def test_addon_takes_over_and_exits(self):
        self.tick()
        self.running = {}
        self.tick(1.0)
        self.assertEqual(self.events, [])                 # (not yet: Main restarts take a moment)
        self.tick(1.5)
        self.assertEqual(self.events[-1][0], 'addon_started')
        self.running = {2: ('MiSTer_phosphor', self.main)}
        self.tick()
        self.assertEqual(self.events[-1], ('addon_ended', {'name': None, 'how': 'exit'}))
        self.assertEqual(self.started, [])

    def test_a_main_restart_is_not_a_takeover(self):
        self.running = {}
        self.tick(1.0)
        self.running = {3: ('MiSTer_phosphor', self.main)}
        self.tick()
        self.assertEqual(self.events, [])

    def test_menu_button_held_brings_the_frontend_back(self):
        self.tick()
        self.running = {}
        self.tick(2.5)
        self.tick(held=True)
        self.assertEqual(self.started, [self.main])
        self.assertEqual(len(self.killed), 1)
        self.tick(held=True)
        self.assertEqual(self.started, [self.main])       # (once)
        self.running = {4: ('MiSTer_phosphor', self.main)}
        self.tick()
        self.assertEqual(self.events[-1][1]['how'], 'escape')

    def test_main_restarting_on_a_core_load_is_not_an_addon(self):
        self.tick()
        self.running = {}                                  # (the pipe closed: Main exec'ing itself)
        self.procs = {1: self.main}
        self.tick(5)
        self.assertEqual(self.events, [])
        self.assertEqual(self.t.state, 'main')
        self.running = {1: ('MiSTer_phosphor', self.main)}
        self.tick()
        self.assertEqual(self.events, [])

    def test_main_binary_gone_is_an_addon(self):
        self.tick()
        self.running = {}
        self.procs = {7: '/media/fat/linux/dreamster'}
        self.tick(2.5)
        self.assertEqual(self.events[0][0], 'addon_started')

    def test_at_boot_no_main_yet_is_not_a_takeover(self):
        self.running = {}                                  # (Coreshell up before any Main)
        self.tick(5, held=True)
        self.assertEqual(self.events, [])
        self.assertEqual(self.started, [])

    def test_held_before_takeover_does_nothing(self):
        self.tick(held=True)
        self.running = {}
        self.tick(0.5, held=True)
        self.assertEqual(self.started, [])

    def test_stock_main_left_in_the_menu_is_replaced(self):
        stock = os.path.join(self.tmp, 'MiSTer')
        self.running = {5: ('MiSTer', stock)}
        self.tick()
        self.tick(3)
        self.assertEqual(self.started, [])                # (MiSTer.ini's main= may still be on its way)
        self.tick(4)
        self.assertEqual(self.started, [self.main])
        self.assertEqual(self.killed, [[5]])

    def test_stock_main_in_a_game_is_left_alone(self):
        self.menu = False
        self.running = {5: ('MiSTer', os.path.join(self.tmp, 'MiSTer'))}
        self.tick()
        self.tick(10)
        self.assertEqual(self.started, [])

    def test_no_frontend_registered_leaves_stock_alone(self):
        os.remove(os.path.join(self.home, 'state', 'frontend.json'))
        self.running = {5: ('MiSTer', os.path.join(self.tmp, 'MiSTer'))}
        self.tick()
        self.tick(10)
        self.assertEqual(self.started, [])


class Addons(unittest.TestCase):
    def test_rewrite_all_or_nothing(self):
        e = {'menu': [{'from': 'Exit To MiSTer', 'to': 'Exit To {frontend}'}, {'from': 'returns to MiSTer', 'to': 'returns to {frontend}'}]}
        self.assertEqual(ad.rewrite('a Exit To MiSTer b returns to MiSTer', e, 'Phosphor'),
                         'a Exit To Phosphor b returns to Phosphor')
        self.assertIsNone(ad.rewrite('a Exit To MiSTer b', e, 'Phosphor'))   # (a new release: run as it is)

    def test_dreamster_entry_matches_its_source_lines(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        e = ad.find('/media/fat/Scripts/DreamSTer.sh', here)
        self.assertIsNotNone(e)
        src = ('    system_rows = [("configure", "Configure"),\n                   ("bios", "Boot To Bios"),\n'
               '                   ("exit", "Exit To MiSTer")]\n    "Exit returns to MiSTer, leaving it untouched.",\n')
        out = ad.rewrite(src, e, 'Phosphor')
        self.assertIn('("exit", "Exit To Phosphor")', out)
        self.assertIn('"Exit returns to Phosphor, leaving it untouched."', out)

    def test_prepare_runs_a_named_copy_and_leaves_the_script(self):
        tmp = tempfile.mkdtemp()
        try:
            home = os.path.join(tmp, 'coreshell')
            os.makedirs(os.path.join(home, 'addons')); os.makedirs(os.path.join(home, 'state'))
            with open(os.path.join(home, 'addons', 'x.json'), 'w') as f:
                json.dump({'name': 'X', 'script': 'X.sh', 'menu': [{'from': 'To MiSTer', 'to': 'To {frontend}'}]}, f)
            script = os.path.join(tmp, 'X.sh')
            with open(script, 'w') as f:
                f.write('#!/bin/sh\necho To MiSTer\n')
            os.chmod(script, 0o755)
            self.assertEqual(ad.prepare(script, home), script)                 # (no frontend registered)
            with open(os.path.join(home, 'state', 'frontend.json'), 'w') as f:
                json.dump({'name': 'Phosphor'}, f)
            ad.RUN_DIR = os.path.join(tmp, 'run')
            out = ad.prepare(script, home)
            self.assertNotEqual(out, script)
            self.assertIn('To Phosphor', open(out).read())
            self.assertIn('To MiSTer', open(script).read())
            self.assertTrue(os.access(out, os.X_OK))
        finally:
            shutil.rmtree(tmp)


if __name__ == '__main__':
    unittest.main()
