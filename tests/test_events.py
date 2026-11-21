"""events.py: what happens on the console, for every frontend and add-on (game started / ended, OSD file, saves,
achievements), whoever started the game."""
import json, os, socket, tempfile, threading, time, unittest
from coreshell import events as evm, launcher as ln, server as sv


class EventsFiles(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        t = self.d.name
        self.ev = evm.Events(log=os.path.join(t, 'events.jsonl'), tmp=t)
        self.put('CORENAME', 'MENU')
        self.ev.start(); self.ev.stop()

    def tearDown(self):
        self.d.cleanup()

    def put(self, name, text, mode='w'):
        with open(os.path.join(self.d.name, name), mode) as f:
            f.write(text)

    def types(self):
        return [e['type'] for e in self.ev.since(0)[0]]

    def test_a_core_main_started_is_a_game(self):     # (a stock Main: no shim, no Coreshell launch)
        self.put('FULLPATH', '/media/fat/games/NES/Game.nes')
        self.put('CORENAME', 'NES'); self.ev.check()
        evs = self.ev.since(0)[0]
        self.assertEqual([e['type'] for e in evs], ['core_changed', 'game_started'])
        self.assertEqual(evs[1]['game'], '/media/fat/games/NES/Game.nes')
        self.assertEqual(evs[1]['by'], 'main')
        self.put('CORENAME', 'MENU'); self.ev.check()
        self.assertEqual(self.types()[-2:], ['core_changed', 'game_ended'])

    def test_coreshell_launch_not_told_twice(self):
        self.ev.game_started('/g/A.sfc', system='snes', by='zaparoo')
        self.put('CORENAME', 'SNES'); self.ev.check()
        self.assertEqual(self.types(), ['game_started', 'core_changed'])

    def test_ended_once(self):                       # (Main's card event, then CORENAME: one game_ended)
        self.ev.game_started('/g/A.nes', system='nes', by='coreshell')
        self.put('CORENAME', 'NES'); self.ev.check()
        self.ev.game_over(by='main')
        self.put('CORENAME', 'MENU'); self.ev.check()
        self.assertEqual(self.types().count('game_ended'), 1)

    def test_restarted_mid_game(self):               # (Coreshell killed during a game, started again)
        self.put('CORENAME', 'NES'); self.put('ACTIVEGAME', '/g/Zelda.nes')
        ev = evm.Events(log=os.path.join(self.d.name, 'e2.jsonl'), tmp=self.d.name)
        ev.start(); ev.stop()
        self.put('CORENAME', 'MENU'); ev.check()
        evs = ev.since(0)[0]
        self.assertEqual([e['type'] for e in evs], ['core_changed', 'game_ended'])
        self.assertEqual((evs[1]['game'], evs[1]['core']), ('/g/Zelda.nes', 'NES'))

    def test_wait(self):
        threading.Timer(0.2, lambda: self.ev.emit('saves', to='card')).start()
        t0 = time.time(); out, seq = self.ev.since(0, wait=3)
        self.assertEqual([e['type'] for e in out], ['saves'])
        self.assertLess(time.time() - t0, 2)
        time.sleep(0.2)
        self.assertTrue(os.path.getsize(self.ev.log) > 0)


class EventsFromCoreshell(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        t = self.d.name
        sv.LAUNCH_LOG = os.path.join(t, 'launch.log')
        sv.STATE_DIR = os.path.join(t, 'state'); os.makedirs(sv.STATE_DIR)
        self.cs = sv.Coreshell(ln.Paths(t), fifo=os.path.join(t, 'fifo'))
        self.cs.events.log = os.path.join(t, 'events.jsonl')

    def tearDown(self):
        self.d.cleanup()

    def test_card_events(self):
        h = lambda line: self.cs.handle(dict(v=sv.API, cmd='card_event', line=line))
        h('ROM /media/fat/_Computer/Atari800.rbf\t/media/usb0/games/ATARI800/boot0.rom\t0')
        self.assertEqual(open(os.path.join(sv.STATE_DIR, 'FULLPATH')).read(), '/media/usb0/games/ATARI800/boot0.rom')
        h('MENU')
        evs = self.cs.events.since(0)[0]
        self.assertEqual([e['type'] for e in evs], ['rom_loaded', 'game_ended'])
        self.assertEqual(evs[1]['game'], '/media/usb0/games/ATARI800/boot0.rom')

    def test_launch_told_and_subscribe(self):
        sock = os.path.join(self.d.name, 'cs.sock')
        srv = sv.Server(self.cs, sock)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        c = socket.socket(socket.AF_UNIX); c.connect(sock); c.settimeout(5)
        c.sendall(b'{"v": 1, "cmd": "subscribe"}\n')
        f = c.makefile('rb')
        self.assertTrue(json.loads(f.readline())['ok'])
        rom = os.path.join(self.d.name, 'A.bin'); open(rom, 'w').write('x')
        r = sv.request(dict(v=sv.API, cmd='launch', rbf='_Console/Odd_20260101', rom=rom, slot={'type': 'f', 'delay': 1, 'index': 0}, **{'from': 'zaparoo'}), sock)
        self.assertTrue(r['ok'], r)
        e = json.loads(f.readline())
        self.assertEqual((e['type'], e['game'], e['by']), ('game_started', rom, 'zaparoo'))
        got = sv.request(dict(v=sv.API, cmd='events', since=0), sock)
        self.assertEqual([x['type'] for x in got['events']], ['game_started'])
        c.close(); srv.shutdown(); srv.server_close()


if __name__ == '__main__':
    unittest.main()
