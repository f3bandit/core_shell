"""Coreshell - CONF_STR parser.

Turns a MiSTer core's CONF_STR (the menu string built into every core) into
plain data: pages, options, file loaders, mounts and triggers.

The rules follow Main_MiSTer's own parser (menu.cpp, user_io.cpp,
support/arcade/mra_loader.cpp) so what Coreshell sees matches what the stock
OSD shows. No Main_MiSTer code is copied here; this is a clean reading of the
string format.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import re

# ------------------------------------------------------------- CONF_STR side

def _bit_index(ch):
    if '0' <= ch <= '9':
        return ord(ch) - 48
    if 'A' <= ch <= 'V':
        return ord(ch) - 55
    return None


def parse_bits(spec, ex=False):
    """Status-bit spec -> (start, end) like Main's user_io_status_bits.

    '9B' -> 9..11, 'J' -> 19..19, '[72:70]' -> 70..72, '[53]' -> 53..53.
    `ex` (a lowercase 'o' option) adds 32 to the letter form only.
    """
    if spec.startswith('['):
        m = re.fullmatch(r'\[(\d+):(\d+)\]', spec) or re.fullmatch(r'\[(\d+)\]', spec)
        if not m:
            return None
        if len(m.groups()) == 2:
            end, start = int(m.group(1)), int(m.group(2))
        else:
            start = end = int(m.group(1))
        return (start, end) if start <= end <= 127 else None
    s = _bit_index(spec[0]) if spec else None
    if s is None:
        return None
    e = _bit_index(spec[1]) if len(spec) > 1 else None
    if e is None:
        e = s
    if ex:
        s, e = s + 32, e + 32
    return (s, e) if s <= e else None


def _split_fields(item):
    return item.split(',')


def parse(confstr):
    """CONF_STR -> dict with core name, config version, pages and entries."""
    items = confstr.split(';')
    core = {
        'core_name': items[0] if items else '',
        'config_ver': 0,          # 'v,N' -> config file NAME_vN.CFG
        'version': '',
        'pages': {},              # page number -> title
        'options': [],
        'files': [],              # F entries: ROM / BIOS loaders
        'mounts': [],             # S entries: disk / CD / memcard images
        'triggers': [],           # R / r / T / t entries (reset, save RAM...)
        'cheats': False,
        'joystick': [],
    }

    # items[1] is the core-wide field (savestate RAM, UART modes), not a menu
    # line; Main's menu starts at items[2] as well
    for raw in items[2:]:
        if not raw:
            break               # Main's menu ends at the first empty item

        p = raw
        flags = []
        # H/h/D/d prefixes: hide/disable while a core-driven mask bit is set
        while p[:1] in ('H', 'h', 'D', 'd') and len(p) > 2:
            flags.append(p[:2])
            p = p[2:]

        page = 0
        if p[:1] == 'P' and p[1:2].isdigit():
            if p[2:3] == ',':
                core['pages'][int(p[1])] = _split_fields(p)[1]
                continue
            page = int(p[1])
            p = p[2:]

        kind = p[:1]
        if kind in ('O', 'o'):
            spec = p[1:].split(',', 1)[0]
            f = _split_fields(p)
            bits = parse_bits(spec, ex=(kind == 'o'))
            if bits is None or len(f) < 3:
                continue
            core['options'].append({
                'name': f[1],
                'page': page,
                'values': f[2:],
                'bits': list(bits),
                'spec': kind + spec,
                'flags': flags,
            })
        elif kind in ('F', 'S'):
            idx = 1
            remember = p[idx:idx + 1] == 'S' if kind == 'F' else False
            if p[idx:idx + 1] == 'S':
                idx += 1
            if p[idx:idx + 1] == 'C':
                idx += 1
            num = int(p[idx]) if p[idx:idx + 1].isdigit() else 0
            f = _split_fields(p)
            exts = f[1] if len(f) > 1 else ''
            exts = [exts[i:i + 3].strip() for i in range(0, len(exts), 3)]
            entry = {
                'index': num,
                'type': kind.lower(),      # MGL <file type="f|s">
                'exts': [e for e in exts if e],
                'label': f[2] if len(f) > 2 else '',
                'page': page,
                'flags': flags,
            }
            if kind == 'F':
                entry['remember_dir'] = remember
                core['files'].append(entry)
            else:
                core['mounts'].append(entry)
        elif kind in ('R', 'r', 'T', 't'):
            spec = p[1:].split(',', 1)[0]
            f = _split_fields(p)
            bits = parse_bits(spec, ex=kind.islower())
            if bits:
                core['triggers'].append({
                    'name': f[1] if len(f) > 1 else '',
                    'bits': list(bits),
                    'kind': 'reset' if kind in 'Rr' else 'toggle',
                    'page': page,
                    'flags': flags,
                })
        elif kind == 'C':
            core['cheats'] = True
        elif kind == 'J':
            core['joystick'] = _split_fields(p)[1:]
        elif kind == 'V':
            core['version'] = _split_fields(p)[1] if ',' in p else ''
        elif kind == 'v':
            f = _split_fields(p)
            if len(f) > 1 and f[1][:2].isdigit():
                core['config_ver'] = int(f[1][:2])
            elif len(f) > 1 and f[1][:1].isdigit():
                core['config_ver'] = int(f[1][:1])
    return core
