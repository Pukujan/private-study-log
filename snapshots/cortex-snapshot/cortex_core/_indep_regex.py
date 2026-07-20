r"""Independent second implementation of the regex oracle (D1 cross-validation).

The primary regex checker (`evals.objective_regex_correctness.checker_regex`) runs the candidate
pattern through Python's `re` engine. This module shares NO CODE with it and does NOT import
`re`: it is a from-scratch recursive-descent parser + Thompson-NFA simulator covering the feature
subset the regex fixtures use (anchors, literals, `.`, the `\d \w \s` escape classes and their
negations, character classes with ranges/negation, the `+ * ? {n} {n,} {n,m}` quantifiers,
groups, and alternation). Two independent matchers that agree on a pattern's must_match /
must_not_match verdict give the BFCL-style measured-agreement signal.

Because it is an NFA (linear-time, no backtracking), it is IMMUNE to catastrophic backtracking.
That is a feature for cross-validation: on a pattern like `^([a-z]+)+$` it reports the *string-set
correctness* verdict, revealing that the primary lane's `catastrophic_backtracking` FAIL is a
performance/policy decision, not a string-set-correctness fact. Such disagreements are surfaced,
not hidden. Patterns using features outside the supported subset raise `UnsupportedRegex`, and the
harness treats that op as ``abstain`` (never a fake verdict).
"""

from __future__ import annotations


class UnsupportedRegex(Exception):
    """Pattern uses a construct outside this engine's supported subset."""


# --------------------------------------------------------------------------- AST
# Node kinds: ('char', predicate), ('anchor', which), ('concat', [nodes]),
#             ('alt', [nodes]), ('star', node), ('plus', node), ('opt', node),
#             ('rep', node, lo, hi|None), ('group', node)

class _Parser:
    def __init__(self, pattern: str):
        self.p = pattern
        self.i = 0

    def _peek(self):
        return self.p[self.i] if self.i < len(self.p) else None

    def _next(self):
        c = self.p[self.i]
        self.i += 1
        return c

    def parse(self):
        node = self._alt()
        if self.i != len(self.p):
            raise UnsupportedRegex(f"trailing input at {self.i}: {self.p[self.i:]!r}")
        return node

    def _alt(self):
        branches = [self._concat()]
        while self._peek() == "|":
            self._next()
            branches.append(self._concat())
        return branches[0] if len(branches) == 1 else ("alt", branches)

    def _concat(self):
        items = []
        while self._peek() is not None and self._peek() not in ")|":
            items.append(self._repeat())
        return items[0] if len(items) == 1 else ("concat", items)

    def _repeat(self):
        atom = self._atom()
        c = self._peek()
        if c == "*":
            self._next()
            return ("star", atom)
        if c == "+":
            self._next()
            return ("plus", atom)
        if c == "?":
            self._next()
            return ("opt", atom)
        if c == "{":
            return self._counted(atom)
        return atom

    def _counted(self, atom):
        self._next()  # consume '{'
        num = ""
        while self._peek() is not None and self._peek().isdigit():
            num += self._next()
        lo = int(num) if num else 0
        hi = lo
        if self._peek() == ",":
            self._next()
            num2 = ""
            while self._peek() is not None and self._peek().isdigit():
                num2 += self._next()
            hi = int(num2) if num2 else None
        if self._peek() != "}":
            raise UnsupportedRegex("malformed {} quantifier")
        self._next()  # consume '}'
        return ("rep", atom, lo, hi)

    def _atom(self):
        c = self._peek()
        if c is None:
            raise UnsupportedRegex("unexpected end of pattern")
        if c == "(":
            self._next()
            # non-capturing prefix (?: ...) supported; other (?...) not
            if self.p[self.i:self.i + 2] == "?:":
                self.i += 2
            elif self._peek() == "?":
                raise UnsupportedRegex("(?...) extension not supported")
            inner = self._alt()
            if self._peek() != ")":
                raise UnsupportedRegex("unbalanced (")
            self._next()
            return ("group", inner)
        if c == "[":
            return self._charclass()
        if c == "^":
            self._next()
            return ("anchor", "start")
        if c == "$":
            self._next()
            return ("anchor", "end")
        if c == ".":
            self._next()
            return ("char", lambda ch: ch != "\n")
        if c == "\\":
            self._next()
            return ("char", self._escape_pred(self._next()))
        if c in "*+?{":
            raise UnsupportedRegex(f"dangling quantifier {c!r}")
        self._next()
        return ("char", (lambda lit: (lambda ch: ch == lit))(c))

    def _escape_pred(self, e):
        table = {
            "d": lambda ch: ch.isdigit(),
            "D": lambda ch: not ch.isdigit(),
            "w": lambda ch: ch.isalnum() or ch == "_",
            "W": lambda ch: not (ch.isalnum() or ch == "_"),
            "s": lambda ch: ch.isspace(),
            "S": lambda ch: not ch.isspace(),
        }
        if e in table:
            return table[e]
        # escaped metachar / literal
        return (lambda lit: (lambda ch: ch == lit))(e)

    def _charclass(self):
        self._next()  # consume '['
        negate = False
        if self._peek() == "^":
            self._next()
            negate = True
        preds = []
        literals = set()
        ranges = []
        first = True
        while True:
            c = self._peek()
            if c is None:
                raise UnsupportedRegex("unterminated character class")
            if c == "]" and not first:
                self._next()
                break
            first = False
            if c == "\\":
                self._next()
                e = self._next()
                base = {"d": None, "D": None, "w": None, "W": None, "s": None, "S": None}
                if e in base:
                    preds.append(self._escape_pred(e))
                    continue
                lit = e
            else:
                lit = self._next()
            # range? lit '-' hi   (but not a trailing '-')
            if self._peek() == "-" and self.p[self.i + 1:self.i + 2] not in ("", "]"):
                self._next()  # consume '-'
                hi = self._next()
                if hi == "\\":
                    hi = self._next()
                ranges.append((lit, hi))
            else:
                literals.add(lit)

        def pred(ch, _lits=literals, _ranges=ranges, _preds=preds, _neg=negate):
            hit = ch in _lits or any(lo <= ch <= hi for lo, hi in _ranges) or any(p(ch) for p in _preds)
            return (not hit) if _neg else hit

        return ("char", pred)


# --------------------------------------------------------------------------- NFA compile
class _NFA:
    __slots__ = ("start", "accept", "eps", "canon", "nstates")

    def __init__(self):
        self.eps = {}     # state -> list of (target, anchor|None)
        self.canon = {}   # state -> list of (predicate, target)
        self.nstates = 0

    def _new(self):
        s = self.nstates
        self.nstates += 1
        self.eps[s] = []
        self.canon[s] = []
        return s

    def _eps(self, a, b, anchor=None):
        self.eps[a].append((b, anchor))

    def _char(self, a, pred, b):
        self.canon[a].append((pred, b))


def _compile(node, nfa: "_NFA"):
    """Return (start, accept) fragment for `node`."""
    kind = node[0]
    if kind == "char":
        s, a = nfa._new(), nfa._new()
        nfa._char(s, node[1], a)
        return s, a
    if kind == "anchor":
        s, a = nfa._new(), nfa._new()
        nfa._eps(s, a, anchor=node[1])
        return s, a
    if kind == "group":
        return _compile(node[1], nfa)
    if kind == "concat":
        parts = [_compile(n, nfa) for n in node[1]]
        for (_s1, a1), (s2, _a2) in zip(parts, parts[1:], strict=False):
            nfa._eps(a1, s2)
        return parts[0][0], parts[-1][1]
    if kind == "alt":
        s, a = nfa._new(), nfa._new()
        for n in node[1]:
            si, ai = _compile(n, nfa)
            nfa._eps(s, si)
            nfa._eps(ai, a)
        return s, a
    if kind == "star":
        s, a = nfa._new(), nfa._new()
        si, ai = _compile(node[1], nfa)
        nfa._eps(s, si)
        nfa._eps(s, a)
        nfa._eps(ai, si)
        nfa._eps(ai, a)
        return s, a
    if kind == "plus":
        si, ai = _compile(node[1], nfa)
        a = nfa._new()
        nfa._eps(ai, si)
        nfa._eps(ai, a)
        return si, a
    if kind == "opt":
        s, a = nfa._new(), nfa._new()
        si, ai = _compile(node[1], nfa)
        nfa._eps(s, si)
        nfa._eps(s, a)
        nfa._eps(ai, a)
        return s, a
    if kind == "rep":
        _, sub, lo, hi = node
        s = nfa._new()
        cur = s
        # mandatory copies
        for _ in range(lo):
            si, ai = _compile(sub, nfa)
            nfa._eps(cur, si)
            cur = ai
        if hi is None:
            # then a star of sub
            si, ai = _compile(sub, nfa)
            loop_in = nfa._new()
            a = nfa._new()
            nfa._eps(cur, loop_in)
            nfa._eps(loop_in, si)
            nfa._eps(loop_in, a)
            nfa._eps(ai, loop_in)
            return s, a
        a = nfa._new()
        nfa._eps(cur, a)
        for _ in range(hi - lo):
            si, ai = _compile(sub, nfa)
            nfa._eps(cur, si)
            nfa._eps(ai, a)
            # allow skipping the rest after this optional copy too
            cur2 = nfa._new()
            nfa._eps(ai, cur2)
            nfa._eps(cur, cur2)  # keep chain reachable
            cur = ai
        nfa._eps(cur, a)
        return s, a
    raise UnsupportedRegex(f"unknown node {kind}")


def _build(pattern: str) -> "_NFA":
    ast = _Parser(pattern).parse()
    nfa = _NFA()
    s, a = _compile(ast, nfa)
    nfa.start = s
    nfa.accept = a
    return nfa


# --------------------------------------------------------------------------- simulate
def _closure(nfa: "_NFA", states, pos, slen):
    stack = list(states)
    out = set(states)
    while stack:
        st = stack.pop()
        for tgt, anchor in nfa.eps[st]:
            if anchor == "start" and pos != 0:
                continue
            if anchor == "end" and pos != slen:
                continue
            if tgt not in out:
                out.add(tgt)
                stack.append(tgt)
    return out


def _fullmatch(nfa: "_NFA", s: str) -> bool:
    slen = len(s)
    cur = _closure(nfa, {nfa.start}, 0, slen)
    for pos, ch in enumerate(s):
        nxt = set()
        for st in cur:
            for pred, tgt in nfa.canon[st]:
                if pred(ch):
                    nxt.add(tgt)
        cur = _closure(nfa, nxt, pos + 1, slen)
    return nfa.accept in cur


def _search(nfa: "_NFA", s: str) -> bool:
    slen = len(s)
    for start in range(slen + 1):
        cur = _closure(nfa, {nfa.start}, start, slen)
        if nfa.accept in cur:
            return True
        for pos in range(start, slen):
            nxt = set()
            for st in cur:
                for pred, tgt in nfa.canon[st]:
                    if pred(s[pos]):
                        nxt.add(tgt)
            cur = _closure(nfa, nxt, pos + 1, slen)
            if nfa.accept in cur:
                return True
    return False


def matches(pattern: str, s: str, mode: str) -> bool:
    """True/False: does `pattern` match `s` under `mode` ('fullmatch'|'search')?
    Raises UnsupportedRegex for out-of-subset patterns."""
    nfa = _build(pattern)
    if mode == "fullmatch":
        return _fullmatch(nfa, s)
    if mode == "search":
        return _search(nfa, s)
    raise ValueError(f"bad mode {mode!r}")


def independent_verdict(candidate_regex: str, match_mode: str,
                        must_match: list, must_not_match: list) -> str:
    """'pass' | 'fail' | 'abstain'. 'pass' iff every must_match matches and every must_not_match
    is rejected, by the NFA. 'abstain' if the pattern is outside the supported subset (never a
    fake verdict)."""
    try:
        for s in must_match:
            if not matches(candidate_regex, s, match_mode):
                return "fail"
        for s in must_not_match:
            if matches(candidate_regex, s, match_mode):
                return "fail"
    except UnsupportedRegex:
        return "abstain"
    return "pass"
