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

# ---------------------------------------------------------------- Verilog side

_STR = r'"((?:[^"\\]|\\.)*)"'
_TOKEN = re.compile(_STR + r'|`(\w+)|(\{)|(\})|(;)', re.S)
_DIRECTIVE = re.compile(r'^\s*`(ifdef|ifndef|elsif|else|endif)\b\s*(\w*)', re.M)


def _defines(src):
    """`define NAME "text" -> {NAME: text} (string macros only, e.g. SEP)."""
    out = {}
    for m in re.finditer(r'`define\s+(\w+)\s+' + _STR, src):
        out[m.group(1)] = m.group(2)
    return out


def extract_from_verilog(src, defines=None, strict=False):
    """Pull CONF_STR out of a core's top-level .sv source.

    Returns the joined string. `ifdef blocks are taken as if the macro is NOT
    defined (the normal release build) unless its name is in `defines`.
    strict: a CONF_STR built from parts this can't read (a localparam, a macro that
    isn't a plain string) raises ValueError instead of leaving those parts out
    (the PCXT core from Nov 2026 builds its menu that way: what's left isn't its menu).
    """
    defines = set(defines or ())
    m = re.search(r'(?:localparam|parameter)\s+CONF_STR\s*=\s*\{', src)
    if not m:
        raise ValueError("no CONF_STR in source")
    body_start = m.end()

    # cut the body at the matching '};'
    depth, i = 1, body_start
    while i < len(src) and depth:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < len(src) and src[j] != '"':
                j += 2 if src[j] == '\\' else 1
            i = j + 1
            continue
        if c == '/' and src[i:i + 2] == '//':
            i = src.find('\n', i)
            i = len(src) if i < 0 else i
            continue
        if c == '/' and src[i:i + 2] == '/*':
            i = src.find('*/', i) + 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        i += 1
    body = src[body_start:i - 1]

    macros = _defines(src)
    parts = []
    # walk line by line so `ifdef blocks can be honoured
    stack = []  # list of (active, taken)
    for line in body.splitlines():
        code = line.split('//', 1)[0]
        d = _DIRECTIVE.match(code)
        if d:
            kind, name = d.group(1), d.group(2)
            if kind in ('ifdef', 'ifndef'):
                on = (name in defines) == (kind == 'ifdef')
                stack.append([on, on])
            elif kind == 'elsif' and stack:
                on = not stack[-1][1] and name in defines
                stack[-1] = [on, stack[-1][1] or on]
            elif kind == 'else' and stack:
                stack[-1] = [not stack[-1][1], True]
            elif kind == 'endif' and stack:
                stack.pop()
            continue
        if not all(s[0] for s in stack):
            continue
        for t in _TOKEN.finditer(code):
            if t.group(1) is not None:
                parts.append(bytes(t.group(1), 'utf-8').decode('unicode_escape'))
            elif t.group(2):
                name = t.group(2)
                if name in macros:
                    parts.append(macros[name])
                elif name == 'BUILD_DATE':
                    parts.append('BUILD')
                elif strict:
                    raise ValueError("CONF_STR uses `%s, which isn't a string macro" % name)
        if strict:
            bare = re.findall(r'(?<![`\w])[A-Za-z_]\w*', re.sub(_STR, '', code))
            if bare:
                raise ValueError('CONF_STR uses %s, which this reads only from the core itself' % bare[0])
    return ''.join(parts)


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
