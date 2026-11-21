"""Coreshell - work out a core's CONF_STR from its Verilog source, as Quartus would.

Some cores build CONF_STR from pieces: string localparams, `define macros with
? : choices, flags such as HAS_EGA = (`ENABLE_EGA != 0), and macros set by the
build (VERILOG_MACRO in the .qsf, or in a Tcl profile the .qsf sources: the
PCXT core of Nov 2026 does all of this). This evaluates those: a preprocessor for
`define / `ifdef / `ifndef / `elsif / `else / `endif, and the constant expressions
CONF_STR is made of (strings, numbers, names, `macros, ( ), { } and {n{ }},
? :, || && ! == != < > <= >= + - ~).

Anything it can't evaluate raises Unreadable, so the caller reads the core on a
MiSTer instead (tools/dump_confstr.py) rather than guessing.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import posixpath
import re


class Unreadable(ValueError):
    pass


# ---- the build's macros: .qsf VERILOG_MACRO lines, and the Tcl files it sources

_MACRO = re.compile(r'^\s*set_global_assignment\s+-name\s+VERILOG_MACRO\s+"?([A-Za-z_]\w*)(?:=([^"\s]*))?"?')
_SET = re.compile(r'^\s*set\s+([A-Za-z_]\w*)\s+(.+?)\s*$')
_SOURCE = re.compile(r'^\s*source\s+(.+?)\s*$')


def build_macros(read, qsf):
    """{name: value} the build defines. read(path) -> text or None (paths from the repo root)."""
    macros = {}
    _tcl(read, qsf, macros, {}, 0)
    return macros


def _tcl(read, path, macros, tvars, depth):
    text = read(path)
    if text is None:
        raise Unreadable('%s is not in the repo' % path)
    if depth > 8:
        raise Unreadable('Tcl files source each other too deep')
    here = posixpath.dirname(path)
    braces = 0                                  # inside an if/foreach/proc body: not followed
    for line in text.split('\n'):
        code = line.split('#', 1)[0] if line.lstrip().startswith('#') else line
        if braces == 0 and code.strip():
            m = _MACRO.match(code)
            if m:
                macros[m.group(1)] = m.group(2) if m.group(2) is not None else '1'
            else:
                m = _SET.match(code)
                if m:
                    v = m.group(2)
                    if v.startswith('[file join'):
                        try:
                            v = '/' + _tcl_path(v, here, tvars)    # (from the repo root)
                        except Unreadable:
                            v = None                    # (only a problem if a source uses it)
                    elif v.startswith('[') or v.startswith('$'):
                        v = None
                    else:
                        v = v.strip('"')
                    if v is None:
                        tvars.pop(m.group(1), None)
                    else:
                        tvars[m.group(1)] = v
                else:
                    m = _SOURCE.match(code)
                    if m:
                        target = _tcl_path(m.group(1), here, tvars)
                        if target.endswith('.tcl'):
                            if not target.startswith('sys/'):     # the MiSTer framework's own: no core macros
                                _tcl(read, target, macros, tvars, depth + 1)
                        # .qip/.sdc and the like hold file lists, not macros
        braces += code.count('{') - code.count('}')
        braces = max(braces, 0)


def _tcl_path(arg, here, tvars):
    def sub(s):
        s2 = re.sub(r'\$\{(\w+)\}|\$(\w+)', lambda m: _var(tvars, m.group(1) or m.group(2)), s)
        return s2
    arg = arg.strip()
    m = re.match(r'^\[file join (.*)\]$', arg)
    if m:
        inner = m.group(1).strip()
        inner = re.sub(r'^\[file dirname \[info script\]\]\s*', '', inner)
        if '[' in inner:
            raise Unreadable('Tcl source path %r' % arg)
        parts = [sub(p.strip('"')) for p in inner.split()]
        return posixpath.normpath(posixpath.join(here, *parts))
    if '[' in arg:
        raise Unreadable('Tcl source path %r' % arg)
    p = sub(arg.strip('"'))
    if p.startswith('/'):                       # a path worked out earlier, from the repo root
        return posixpath.normpath(p.lstrip('/'))
    return posixpath.normpath(posixpath.join(here, p))


def _var(tvars, name):
    if name not in tvars:
        raise Unreadable('Tcl variable $%s' % name)
    return tvars[name]


# ---- preprocessor

_STR = r'"(?:[^"\\\n]|\\.)*"'


def strip_comments(src):
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            m = re.compile(_STR).match(src, i)
            if not m:
                raise Unreadable('an unclosed string')
            out.append(m.group(0))
            i = m.end()
        elif src.startswith('//', i):
            j = src.find('\n', i)
            i = n if j < 0 else j
        elif src.startswith('/*', i):
            j = src.find('*/', i + 2)
            if j < 0:
                raise Unreadable('an unclosed comment')
            out.append('\n' * src.count('\n', i, j))
            i = j + 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


_DIRECTIVE = re.compile(r'^\s*`(define|undef|ifdef|ifndef|elsif|else|endif|include|timescale|default_nettype|resetall)\b(.*)$')
_KNOWN = {'BUILD_DATE': '"BUILD"'}             # (as confstr.extract_from_verilog writes it)


def preprocess(src, macros):
    """Text with `ifdef blocks resolved and `macros expanded; macros is updated by `define."""
    defs = dict(_KNOWN)
    defs.update(macros)
    out = []
    stack = []                                  # (this branch active, a branch was taken, parent active)
    active = True
    lines = strip_comments(src).split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        while line.endswith('\\') and i + 1 < len(lines):     # a `define over several lines
            i += 1
            line = line[:-1] + ' ' + lines[i]
        i += 1
        m = _DIRECTIVE.match(line)
        if not m:
            out.append(_expand(line, defs) if active else '')
            continue
        d, rest = m.group(1), m.group(2).strip()
        if d in ('ifdef', 'ifndef'):
            name = rest.split()[0]
            cond = (name in defs) == (d == 'ifdef')
            stack.append((active and cond, cond, active))
            active = active and cond
        elif d == 'elsif':
            if not stack:
                raise Unreadable('`elsif without `ifdef')
            _, taken, parent = stack.pop()
            cond = not taken and rest.split()[0] in defs
            stack.append((parent and cond, taken or cond, parent))
            active = parent and cond
        elif d == 'else':
            if not stack:
                raise Unreadable('`else without `ifdef')
            _, taken, parent = stack.pop()
            stack.append((parent and not taken, True, parent))
            active = parent and not taken
        elif d == 'endif':
            if not stack:
                raise Unreadable('`endif without `ifdef')
            active = stack.pop()[2]
        elif not active:
            pass
        elif d == 'define':
            m2 = re.match(r'([A-Za-z_]\w*)(\()?\s*(.*)$', rest)
            if not m2:
                raise Unreadable('`define %s' % rest)
            if m2.group(2):
                defs[m2.group(1)] = _Fn()            # a macro with arguments: unreadable only if used
            else:
                defs[m2.group(1)] = m2.group(3).strip()
        elif d == 'undef':
            defs.pop(rest.split()[0], None)
        elif d == 'include':
            out.append('`include ' + rest)           # (an include used in CONF_STR fails in the evaluator)
        out.append('')
    if stack:
        raise Unreadable('`ifdef without `endif')
    return '\n'.join(out)


class _Fn(object):
    pass


def _expand(text, defs, depth=0):
    if '`' not in text:
        return text
    if depth > 30:
        raise Unreadable('macros expand into each other too deep')
    parts = re.split('(%s)' % _STR, text)
    for k in range(0, len(parts), 2):          # outside strings only
        def rep(m):
            name = m.group(1)
            if name not in defs:
                raise Unreadable('`%s is not defined' % name)
            v = defs[name]
            if isinstance(v, _Fn):
                raise Unreadable('`%s takes arguments' % name)
            return ' ' + _expand(v, defs, depth + 1) + ' '
        parts[k] = re.sub(r'`([A-Za-z_]\w*)', rep, parts[k])
    return ''.join(parts)


# ---- constant expressions

_TOK = re.compile(r'\s*(?:(%s)|(\d*\'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ]+|\d[\d_]*)|([A-Za-z_]\w*)'
                  r'|(\|\||&&|==|!=|<=|>=|[?:(){},!~<>+\-]))' % _STR)


def tokenize(text):
    toks, i = [], 0
    text = text.rstrip()
    while i < len(text):
        m = _TOK.match(text, i)
        if not m or m.end() == i:
            raise Unreadable('cannot read %r' % text[i:i + 30])
        i = m.end()
        if m.group(1):
            toks.append(('s', bytes(m.group(1)[1:-1], 'utf-8').decode('unicode_escape')))
        elif m.group(2):
            toks.append(('n', _number(m.group(2))))
        elif m.group(3):
            toks.append(('id', m.group(3)))
        else:
            toks.append(('op', m.group(4)))
    return toks


def _number(s):
    s = s.replace('_', '')
    if "'" not in s:
        return int(s)
    digits = re.sub(r"^\d*'[sS]?", '', s)
    base = {'b': 2, 'o': 8, 'd': 10, 'h': 16}[digits[0].lower()]
    if re.search('[xXzZ]', digits):
        raise Unreadable('x/z in %s' % s)
    return int(digits[1:], base)


class _Expr(object):
    def __init__(self, toks, names):
        self.t, self.i, self.names = toks, 0, names

    def peek(self, v=None):
        if self.i >= len(self.t):
            return None if v is None else False
        tok = self.t[self.i]
        return tok if v is None else tok == ('op', v)

    def take(self, v):
        if not self.peek(v):
            raise Unreadable('expected %r' % v)
        self.i += 1

    def parse(self):
        e = self.ternary()
        if self.i != len(self.t):
            raise Unreadable('left over: %r' % (self.t[self.i:self.i + 3],))
        return e

    def ternary(self):
        c = self.binary(0)
        if self.peek('?'):
            self.i += 1
            a = self.ternary()
            self.take(':')
            b = self.ternary()
            return ('?', c, a, b)
        return c

    _LEVELS = [('||',), ('&&',), ('==', '!='), ('<', '>', '<=', '>='), ('+', '-')]

    def binary(self, lvl):
        if lvl == len(self._LEVELS):
            return self.unary()
        e = self.binary(lvl + 1)
        while self.peek() and self.peek()[0] == 'op' and self.peek()[1] in self._LEVELS[lvl]:
            op = self.peek()[1]
            self.i += 1
            e = (op, e, self.binary(lvl + 1))
        return e

    def unary(self):
        if self.peek('!') or self.peek('~') or self.peek('-'):
            op = self.peek()[1]
            self.i += 1
            return ('u' + op, self.unary())
        return self.primary()

    def primary(self):
        tok = self.peek()
        if tok is None:
            raise Unreadable('an expression ends early')
        self.i += 1
        if tok[0] in ('s', 'n'):
            return ('lit', tok[1])
        if tok[0] == 'id':
            return ('name', tok[1])
        if tok == ('op', '('):
            e = self.ternary()
            self.take(')')
            return e
        if tok == ('op', '{'):
            first = self.ternary()
            if self.peek('{'):                     # {n{x}}
                self.i += 1
                inner = self.ternary()
                self.take('}')
                self.take('}')
                return ('rep', first, inner)
            items = [first]
            while self.peek(','):
                self.i += 1
                items.append(self.ternary())
            self.take('}')
            return ('cat', items)
        raise Unreadable('unexpected %r' % (tok,))


def _truth(v):
    if isinstance(v, str):
        raise Unreadable('a string used as a condition')
    return v != 0


def evaluate(node, lookup):
    k = node[0]
    if k == 'lit':
        return node[1]
    if k == 'name':
        return lookup(node[1])
    if k == '?':
        return evaluate(node[2] if _truth(evaluate(node[1], lookup)) else node[3], lookup)
    if k == 'cat':
        vals = [evaluate(x, lookup) for x in node[1]]
        if all(isinstance(v, str) for v in vals):
            return ''.join(vals)
        raise Unreadable('{ } of numbers')
    if k == 'rep':
        n, v = evaluate(node[1], lookup), evaluate(node[2], lookup)
        if isinstance(n, int) and isinstance(v, str):
            return v * n
        raise Unreadable('{n{ }} of numbers')
    if k in ('u!', 'u~', 'u-'):
        v = evaluate(node[1], lookup)
        if isinstance(v, str):
            raise Unreadable('%s on a string' % k[1])
        return int(not v) if k == 'u!' else (~v if k == 'u~' else -v)
    a = evaluate(node[1], lookup)
    if k == '||':
        return 1 if _truth(a) or _truth(evaluate(node[2], lookup)) else 0
    if k == '&&':
        return 1 if _truth(a) and _truth(evaluate(node[2], lookup)) else 0
    b = evaluate(node[2], lookup)
    if k in ('==', '!='):
        if isinstance(a, str) != isinstance(b, str):
            raise Unreadable('a string compared with a number')
        return int((a == b) == (k == '=='))
    if isinstance(a, str) or isinstance(b, str):
        raise Unreadable('%s on a string' % k)
    return int({'<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b}[k]) if k in '<><=>=' \
        else (a + b if k == '+' else a - b)


# ---- the declarations

_DECL = re.compile(r'\b(localparam|parameter)\b')
_TYPE = re.compile(r'^\s*(?:(?:bit|logic|reg|integer|int|string|signed|unsigned)\s+)*(?:\[[^\]]*\]\s*)?')


def declarations(text):
    """{name: expression text} for every localparam/parameter. A name declared twice: Unreadable when used."""
    found = {}
    for m in _DECL.finditer(text):
        body, end = _statement(text, m.end())
        if body is None:
            continue
        body = _TYPE.sub('', body, count=1)
        for part in _split_top(body, ','):
            m2 = re.match(r'\s*([A-Za-z_]\w*)\s*=\s*(.*)$', part, re.S)
            if m2:
                found.setdefault(m2.group(1), []).append(m2.group(2).strip())
    return found


def _statement(text, i):
    """Text up to the ';' (or ')' of a module's #( ) list) that ends a declaration."""
    depth, j, n = 0, i, len(text)
    while j < n:
        c = text[j]
        if c == '"':
            m = re.compile(_STR).match(text, j)
            if not m:
                return None, j
            j = m.end()
            continue
        if c in '({[':
            depth += 1
        elif c in ')}]':
            if depth == 0:
                return text[i:j], j
            depth -= 1
        elif c == ';' and depth == 0:
            return text[i:j], j
        j += 1
    return None, n


def _split_top(text, sep):
    parts, depth, cur, j = [], 0, [], 0
    while j < len(text):
        c = text[j]
        if c == '"':
            m = re.compile(_STR).match(text, j)
            cur.append(m.group(0))
            j = m.end()
            continue
        if c in '({[':
            depth += 1
        elif c in ')}]':
            depth -= 1
        if c == sep and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
        j += 1
    parts.append(''.join(cur))
    return parts


def conf_str(src, macros=None, name='CONF_STR'):
    """The value of CONF_STR in this source, with these build macros."""
    text = preprocess(src, dict(macros or {}))
    decl = declarations(text)
    done, busy = {}, set()

    def lookup(n):
        if n in done:
            return done[n]
        if n not in decl:
            raise Unreadable('%s is not a parameter of this file' % n)
        if len(decl[n]) > 1:
            raise Unreadable('%s is declared %d times' % (n, len(decl[n])))
        if n in busy:
            raise Unreadable('%s depends on itself' % n)
        busy.add(n)
        v = evaluate(_Expr(tokenize(decl[n][0]), decl).parse(), lookup)
        busy.discard(n)
        done[n] = v
        return v

    v = lookup(name)
    if not isinstance(v, str):
        raise Unreadable('%s is not a string' % name)
    return v
