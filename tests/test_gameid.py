"""gameid.py and the launch's card step (Coreshell server._card_in)."""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from coreshell import gameid  # noqa: E402
from coreshell import server  # noqa: E402


def _image(boot, raw=True, mode=2):
    """A data track: PVD at 16, root directory at 20, SYSTEM.CNF at 21."""
    user = {}
    pvd = bytearray(2048)
    pvd[0] = 1
    pvd[1:6] = b'CD001'
    rec = bytearray(34)
    rec[0] = 34
    rec[2:6] = struct.pack('<I', 20)
    rec[10:14] = struct.pack('<I', 2048)
    pvd[156:190] = rec
    user[16] = bytes(pvd)
    name = b'SYSTEM.CNF;1'
    d = bytearray(2048)
    r = bytearray(33 + len(name) + 1)
    r[0] = len(r)
    r[2:6] = struct.pack('<I', 21)
    r[10:14] = struct.pack('<I', 64)
    r[32] = len(name)
    r[33:33 + len(name)] = name
    d[:len(r)] = r
    user[20] = bytes(d)
    user[21] = boot.encode().ljust(2048, b'\0')
    out = bytearray()
    for lba in range(24):
        data = user.get(lba, bytes(2048))
        if raw:
            head = b'\x00' + b'\xff' * 10 + b'\x00' + bytes(3) + bytes([mode])
            head += bytes(8) if mode == 2 else b''
            out += (head + data).ljust(2352, b'\0')
        else:
            out += data
    return bytes(out)


def _write(tmp, name, data):
    p = os.path.join(tmp, name)
    with open(p, 'wb') as f:
        f.write(data)
    return p


def test_cue_mode2():
    with tempfile.TemporaryDirectory() as t:
        _write(t, 'g.bin', _image('BOOT = cdrom:\\SLUS_010.41;1\r\nTCB = 4\r\n'))
        cue = _write(t, 'g.cue', b'FILE "g.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
        assert gameid.psx(cue) == 'SLUS-01041'


def test_digit_prefix_and_bin_and_iso():
    with tempfile.TemporaryDirectory() as t:
        b = _write(t, 'h.bin', _image('BOOT=cdrom:\\TLW2_942.21;1'))
        assert gameid.psx(b) == 'TLW-294221'
        i = _write(t, 'h.iso', _image('BOOT = cdrom:SCUS_942.21;1', raw=False))
        assert gameid.psx(i) == 'SCUS-94221'


def test_not_a_psx_disc():
    with tempfile.TemporaryDirectory() as t:
        b = _write(t, 'x.bin', bytes(2352 * 24))
        assert gameid.psx(b) is None
        assert gameid.psx(os.path.join(t, 'missing.cue')) is None
        assert gameid.psx(None) is None


class _Smc(object):
    def __init__(self, up=True, answer='select ok'):
        self._up, self.answer, self.asked = up, answer, []

    def up(self):
        return self._up

    def select(self, sys_name, game):
        self.asked.append((sys_name, game))
        return self.answer


def _cs(smc):
    cs = server.Coreshell.__new__(server.Coreshell)
    cs.smc = smc
    return cs


def test_card_step(monkeypatch, tmp_path):
    monkeypatch.setattr(server, 'LAUNCH_LOG', str(tmp_path / 'launch.log'))
    psx = {'system': 'PSX', 'saves_dir': '/media/fat/saves/PSX'}
    s = _Smc()
    cue = tmp_path / 'g.cue'
    (tmp_path / 'g.bin').write_bytes(_image('BOOT = cdrom:\\SLUS_010.41;1'))
    cue.write_text('FILE "g.bin" BINARY\n  TRACK 01 MODE2/2352\n')
    assert _cs(s)._card_in(psx, str(cue)) == {'game_id': 'SLUS-01041', 'select': 'select ok'}
    assert s.asked == [('PSX', 'SLUS-01041')]
    # a CHD: the frontend's game_id
    s2 = _Smc()
    assert _cs(s2)._card_in(psx, '/x/game.chd', 'SCUS-94351')['game_id'] == 'SCUS-94351'
    # no smcd, no game ID, another system: nothing sent
    s3 = _Smc(up=False)
    assert _cs(s3)._card_in(psx, str(cue)) is None and s3.asked == []
    s4 = _Smc()
    assert _cs(s4)._card_in(psx, '/x/unknown.chd') is None and s4.asked == []
    s5 = _Smc()
    assert _cs(s5)._card_in({'system': 'NES', 'saves_dir': '/media/fat/saves/NES'}, '/x/g.nes') is None
    assert s5.asked == []
    log = (tmp_path / 'launch.log').read_text()
    assert 'PSX SLUS-01041: select ok' in log and 'no smcd' in log and 'no game ID' in log
