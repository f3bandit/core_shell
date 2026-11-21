"""Coreshell command line.

    python3 -m coreshell serve [--root /media/fat] [--socket /tmp/coreshell.sock]
    python3 -m coreshell systems
    python3 -m coreshell menu <profile> [game]
    python3 -m coreshell set <profile> <key> <value> [--game <game>]
    python3 -m coreshell plan <profile> [rom]
    python3 -m coreshell launch <profile> <rom> [save]
    python3 -m coreshell ini-check [--fix | --undo]
    python3 -m coreshell stale                 cores whose profile is out of date or missing
    python3 -m coreshell refresh               dump those here and rebuild their profiles
    python3 -m coreshell mains [check|restore] Main files: the shim builds put in place (mains.py), or the stock back
                                               (and left alone until `mains check`)
    python3 -m coreshell update [--check|--force|--mains|--rollback]
                                               everything brought up to the latest release (update.py)
    python3 -m coreshell status | events | mains scan|info|probe|forks|build | cores scan | core info|profile
                                               the developer command line (devtools.py: python3 -m coreshell status -h)
    serve --refresh-at-start                   refresh once the service is up (from the menu)

    run-script PATH                           a script, as the Scripts menu runs it (addons/: an add-on's menu names
                                               the frontend that started it)
Without `serve`, commands run directly (no server needed).

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import json
import sys
import time

from . import launcher as ln
from . import server as sv


def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        val = argv[i + 1]
        del argv[i:i + 2]
        return val
    return default


def _title(name):
    """The process's name in ps and top: its command line (written over the one it started with, in its own memory,
    /proc/self/mem) and its short name (prctl PR_SET_NAME). Anything refused: python3 it stays."""
    import ctypes
    try:
        ctypes.CDLL(None).prctl(15, ctypes.c_char_p(name.encode()[:15]), 0, 0, 0)
    except (OSError, AttributeError):
        pass
    try:
        with open('/proc/self/stat') as f:
            st = f.read().rsplit(')', 1)[1].split()
        start, end = int(st[45]), int(st[46])          # (arg_start, arg_end: the command line's memory)
        new = name.encode()[:end - start - 1]
        with open('/proc/self/mem', 'r+b', buffering=0) as m:
            m.seek(start)
            m.write(new + b'\0' * (end - start - len(new)))
    except (OSError, ValueError, IndexError):
        pass


def main(argv):
    root = _opt(argv, '--root', '/media/fat')
    sock = _opt(argv, '--socket', sv.SOCKET)
    game = _opt(argv, '--game')
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == 'run-script' and len(argv) > 1:
        # a script from the Scripts menu (/tmp/script), as its entry in addons/ says (addons.py): first, and fast
        from . import addons
        addons.run_script(argv[1])
    shell = sv.Coreshell(ln.Paths(root))
    cmd, args = argv[0], argv[1:]
    if cmd == 'ini-check':
        from . import inicheck as ic
        backups = shell.paths.backups
        if '--undo' in args:
            print(json.dumps({'restored': ic.undo(root, backups)}, indent=1))
            return 0
        if '--fix' in args:
            print(json.dumps({'commented_out': ic.fix(root, backups)}, indent=1))
        report, mains = ic.check(root)
        print('Mains checked: %s' % ', '.join(mains))
        if not report:
            print('No INI line any installed Main complains about.')
        for ini, hits in report.items():
            print(ini)
            for h in hits:
                print('  line %-4d %-22s = %-10s %s  unknown to %s' % (
                    h['line'], h['key'], h['value'][:10],
                    'default, safe to comment out' if h['default'] else 'REAL SETTING, left alone',
                    ', '.join(h['unknown_to'])))
        return 0
    if cmd == 'update':
        from . import update as up      # Coreshell brought up to its latest release (update.py)
        return up.main(args)
    if cmd in ('status', 'events', 'cores', 'core') or (cmd == 'mains' and args[:1] in (['scan'], ['info'], ['probe'], ['forks'],
                                                                                       ['build'])):
        from . import devtools as dt     # the developer command line (devtools.py)
        return dt.main(argv, lambda: shell)
    if cmd == 'mains':
        if args[:1] not in ([], ['check'], ['restore']):     # (anything else must not touch the Main files)
            print('mains: scan, info <file>, probe <file>, check, restore, forks, build <fork>  (-h: the developer '
                  'command line)')
            return 2
        from . import mains as mn
        m = mn.Mains(root, shell.paths.home, say=print)
        print(json.dumps({'restored': m.restore()} if args[:1] == ['restore'] else {'replaced': m.check(force=True)},
                         indent=1))
        return 0
    if cmd == 'refresh':
        r = shell.refresher
        r.start()
        last = None
        while r.status['state'] == 'running':
            line = '%d/%d %s' % (r.status['done'], r.status['total'], r.status['core'] or '')
            if line != last:
                print(line, flush=True)
                last = line
            time.sleep(0.5)
        print(json.dumps(r.status, indent=1))
        return 0 if not r.status.get('failed') else 1
    if cmd == 'serve':
        _title('core-shell')             # in ps and top: core-shell, not python3
        srv = sv.Server(shell, sock)
        # what add-ons send Main (/dev/MiSTer_cmd) comes to Coreshell first; Main's FIFO back when it stops
        import signal
        def _stop(signum, frame):
            shell.stop_pipe()
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        if '--no-cmdpipe' not in args:
            shell.start_pipe()
        shell.events.start()             # CORENAME and RetroAchievements' events watched (events.py)
        if '--no-takeover' not in args:  # add-ons that take the system over come back to the frontend (takeover.py)
            from . import takeover as _to
            shell.takeover = _to.Takeover(shell.paths.home, shell.paths.root, emit=shell.events.emit, log=sv._log)
            shell.takeover.start()
        if '--no-kernel' not in args:    # the kernel's display against the known-good state (kernel.py)
            import threading as _th
            _th.Thread(target=shell.kernel.check, daemon=True).start()
        if '--no-mains' not in args:     # every Main that runs carries the shim (mains.py)
            from . import mains as mn
            shell.mains = mn.Mains(root, shell.paths.home, say=sv._log, emit=shell.events.emit)
            shell.mains.start()
        if '--no-caps' not in args:      # what's installed and plugged in, for the frontends' menus (caps.py)
            from . import caps as _cp
            shell.caps = _cp.Caps(root, shell.paths.home, emit=shell.events.emit)
            shell.caps.start()
        if '--refresh-at-start' in args:
            shell.refresher.start(wait=15)      # Main and the menu settle first
        print('coreshell %s (api %d) on %s' % (sv.VERSION, sv.API, sock), flush=True)
        try:
            srv.serve_forever()
        finally:
            shell.stop_pipe()
        return 0
    req = {'v': sv.API, 'cmd': cmd}
    if cmd in ('menu', 'plan'):
        req['profile'] = args[0]
        if len(args) > 1:
            req['game' if cmd == 'menu' else 'rom'] = args[1]
    elif cmd == 'set':
        req.update(profile=args[0], key=args[1], value=args[2])
        if game:
            req['game'] = game
    elif cmd == 'kernel':
        if args:
            req['action'] = args[0]
    elif cmd == 'launch':
        req['profile'] = args[0]
        if len(args) > 1:
            req['rom'] = args[1]       # (none: the core with no game, as the API takes it)
        if len(args) > 2:
            req['save'] = args[2]
    resp = shell.handle(req)
    print(json.dumps(resp, indent=1, ensure_ascii=False))
    return 0 if resp.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
