"""main_processes(): a known Main is looked at alone; all of /proc only when it's gone (or every FULL_S)."""
import coreshell.takeover as t


def test_known_main_checked_alone(monkeypatch):
    looked = []
    mains = {'100': ('MiSTer_phosphor', '/media/fat/phosphor/bin/MiSTer_phosphor')}

    def main_of(p):
        looked.append(str(p))
        return mains.get(str(p))

    clock = [1000.0]
    monkeypatch.setattr(t, '_main_of', main_of)
    monkeypatch.setattr(t.os, 'listdir', lambda d: ['1', '50', '100', 'self'])
    monkeypatch.setattr(t.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(t, '_known', {})
    monkeypatch.setattr(t, '_full_at', 0.0)

    assert t.main_processes() == {100: mains['100']}
    assert looked == ['1', '50', '100']             # first: all of /proc
    looked.clear()
    clock[0] += 0.25
    assert t.main_processes() == {100: mains['100']}
    assert looked == ['100']                          # then the known one alone
    looked.clear()
    del mains['100']                                  # it went (a core load, an add-on)
    clock[0] += 0.25
    assert t.main_processes() == {}
    assert looked == ['100', '1', '50', '100']        # all of /proc again
    mains['100'] = ('MiSTer_phosphor', '/x')
    t.main_processes()
    looked.clear()
    clock[0] += t.FULL_S                              # and every FULL_S anyway
    t.main_processes()
    assert looked == ['1', '50', '100']


def test_known_main_by_its_one_link(monkeypatch):
    """a known Main is checked by the link its pipe was on, not by listing all its files"""
    links = {'/proc/100/fd/7': '/dev/MiSTer_cmd'}
    listed = []
    monkeypatch.setattr(t.os, 'readlink', lambda p: links[p] if p in links else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(t.os, 'listdir', lambda d: listed.append(d) or [])
    monkeypatch.setattr(t, '_pipe_fd', {'100': '7'})
    was = ('MiSTer_phosphor', '/x')
    assert t._still(100, was) == was
    assert listed == []                                  # (no listing of /proc/100/fd)
    del links['/proc/100/fd/7']                          # the pipe closed (a core load)
    assert t._still(100, was) is None
    assert listed == ['/proc/100/fd']                    # then it's looked at in full


def test_pipe_opened_looks_at_all(monkeypatch):
    """something opened the pipe (inotify): all of /proc at once, though a known Main is still there"""
    looked = []
    mains = {'100': ('MiSTer_phosphor', '/a'), '200': ('MiSTer', '/media/fat/MiSTer')}
    monkeypatch.setattr(t, '_main_of', lambda p: looked.append(str(p)) or mains.get(str(p)))
    monkeypatch.setattr(t, '_still', lambda p, was: looked.append(str(p)) or mains.get(str(p)))
    monkeypatch.setattr(t.os, 'listdir', lambda d: ['100', '200'])
    clock = [1000.0]
    monkeypatch.setattr(t.time, 'monotonic', lambda: clock[0])
    opened = [False]
    monkeypatch.setattr(t, '_pipe_opened', lambda: opened[0])
    monkeypatch.setattr(t, '_known', {100: mains['100']})
    monkeypatch.setattr(t, '_full_at', clock[0])
    clock[0] += 1
    assert t.main_processes() == {100: mains['100']}
    assert looked == ['100']
    looked.clear()
    opened[0] = True
    clock[0] += 1
    assert t.main_processes() == {100: mains['100'], 200: mains['200']}
    assert looked == ['100', '200']


def test_no_watch_every_5s(monkeypatch):
    """no inotify: all of /proc every 5 s, as before"""
    looked = []
    monkeypatch.setattr(t, '_main_of', lambda p: looked.append(str(p)) or ('MiSTer', '/m'))
    monkeypatch.setattr(t, '_still', lambda p, was: was)
    monkeypatch.setattr(t.os, 'listdir', lambda d: ['100'])
    clock = [1000.0]
    monkeypatch.setattr(t.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(t, '_pipe_opened', lambda: None)
    monkeypatch.setattr(t, '_known', {100: ('MiSTer', '/m')})
    monkeypatch.setattr(t, '_full_at', clock[0])
    clock[0] += 4
    t.main_processes()
    assert looked == []
    clock[0] += 2
    t.main_processes()
    assert looked == ['100']
