"""The grammar both condition translators share, and the rules Intune enforces
on every platform.

A manifest condition is evaluated on the device against facts the client
collects locally, so it can reference almost anything. An assignment filter can
only reference the handful of properties Intune holds. Everything hard about the
translators comes from that gap; everything in this file is the part that does
not depend on which client wrote the condition.

Grammar: NOT binds tighter than AND, which binds tighter than OR. Keywords and
word operators are case-insensitive, values are quoted or bare. A character the
tokenizer does not recognise is an error rather than something to skip, because
a condition that cannot be read exactly must not become a filter that targets
something else.

Two Intune rules, both verified against Graph's validateFilter endpoint, shape
the translation:

  * The unary (not ...) operator is rejected on every property. NOT is pushed
    down to the predicates instead (De Morgan over and/or), and each predicate
    emits its own negated operator. A predicate with no accepted negated form
    raises UnsupportedCondition.
  * -endsWith on device.deviceName, and -notStartsWith / -notEndsWith on
    device.deviceName, device.model and device.manufacturer, are rejected.
    -notContains is accepted.

forbidden_operator() checks a finished rule against those, as a guard against a
translator regression rather than as the primary defence.
"""

from __future__ import annotations

import hashlib
import re
from typing import Callable

# Intune caps a filter displayName; long conditions keep a stable hash suffix.
MAX_DISPLAY = 250


class CondParseError(Exception):
    """The condition is malformed: a bug in the manifest."""


class UnsupportedCondition(Exception):
    """The condition is well-formed but has no filter equivalent: a limit of the
    platform."""


_TOKEN = re.compile(
    r"\s*(?:"
    r'(?P<str>"[^"]*"|\'[^\']*\')'
    r"|(?P<op>==|!=|>=|<=|>|<)"
    r"|(?P<paren>[()])"
    r"|(?P<word>[A-Za-z0-9_.]+)"
    r"|(?P<bad>\S)"
    r")"
)

_KEYWORDS = {"AND", "OR", "NOT", "ANY", "ALL", "SOME", "NONE"}
_WORD_OPS = {
    "CONTAINS",
    "DOES_NOT_CONTAIN",
    "BEGINSWITH",
    "ENDSWITH",
    "LIKE",
    "IN",
    "EQUALS",
    "NOT_EQUALS",
    "GREATER_THAN",
    "LESS_THAN",
    "GREATER_THAN_OR_EQUAL",
    "LESS_THAN_OR_EQUAL",
}
_OP_ALIASES = {
    "EQUALS": "==",
    "NOT_EQUALS": "!=",
    "GREATER_THAN": ">",
    "LESS_THAN": "<",
    "GREATER_THAN_OR_EQUAL": ">=",
    "LESS_THAN_OR_EQUAL": "<=",
}

ORDERED_OPS = {">", "<", ">=", "<="}


def tokenize(text: str) -> list[tuple[str, str]]:
    """Return [(kind, value)]; kind is str / op / paren / kw / word."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group("bad"):
            raise CondParseError(f"unexpected character {m.group('bad')!r}")
        if m.group("str"):
            tokens.append(("str", m.group("str")[1:-1]))
        elif m.group("op"):
            tokens.append(("op", m.group("op")))
        elif m.group("paren"):
            tokens.append(("paren", m.group("paren")))
        else:
            word = m.group("word")
            up = word.upper()
            if up in _KEYWORDS:
                tokens.append(("kw", up))
            elif up in _WORD_OPS:
                tokens.append(("op", _OP_ALIASES.get(up, up)))
            else:
                tokens.append(("word", word))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def _take(self, kinds=None, what="token"):
        tok = self._peek()
        if tok[0] is None:
            raise CondParseError(f"unexpected end of expression (expected {what})")
        if kinds and tok[0] not in kinds:
            raise CondParseError(f"expected {what}, got {tok[1]!r}")
        self.pos += 1
        return tok

    def parse(self):
        if not self.tokens:
            raise CondParseError("empty condition")
        node = self._or()
        if self.pos < len(self.tokens):
            rest = " ".join(v for _k, v in self.tokens[self.pos :])
            raise CondParseError(f"trailing tokens: {rest}")
        return node

    def _or(self):
        n = self._and()
        while self._peek() == ("kw", "OR"):
            self._take()
            n = ("or", n, self._and())
        return n

    def _and(self):
        n = self._not()
        while self._peek() == ("kw", "AND"):
            self._take()
            n = ("and", n, self._not())
        return n

    def _not(self):
        if self._peek() == ("kw", "NOT"):
            self._take()
            return ("not", self._not())
        return self._primary()

    def _primary(self):
        if self._peek() == ("paren", "("):
            self._take()
            n = self._or()
            if self._peek() != ("paren", ")"):
                raise CondParseError("missing ')'")
            self._take()
            return n
        if self._peek()[0] == "kw" and self._peek()[1] in (
            "ANY",
            "ALL",
            "SOME",
            "NONE",
        ):
            _k, quant = self._take()
            _k, field = self._take(("word",), f"collection key after {quant}")
            raise UnsupportedCondition(
                f"{quant} {field.lower()}: collection predicates "
                "have no filter equivalent"
            )
        _k, field = self._take(("word",), "fact name")
        _k, op = self._take(("op",), "operator")
        kind, value = self._take(("str", "word"), "value")
        return ("pred", field.lower(), op, value, "string" if kind == "str" else "bare")


def parse(text: str):
    return _Parser(tokenize(text)).parse()


PredicateFn = Callable[[str, str, str, str, bool], str]


def translate_tree(node, predicate: PredicateFn, negate: bool = False) -> str:
    """Render a parsed condition, pushing NOT down to the predicates."""
    if node[0] in ("and", "or"):
        joiner = node[0]
        if negate:
            joiner = "or" if joiner == "and" else "and"
        left = translate_tree(node[1], predicate, negate)
        right = translate_tree(node[2], predicate, negate)
        return f"({left} {joiner} {right})"
    if node[0] == "not":
        return translate_tree(node[1], predicate, not negate)
    if node[0] == "pred":
        _tag, field, op, value, kind = node
        return predicate(field, op, value, kind, negate)
    raise UnsupportedCondition(f"unknown AST node: {node[0]}")


def quote(value: str) -> str:
    if '"' in value:
        raise UnsupportedCondition(f"value {value!r} contains a double quote")
    return f'"{value}"'


def normalize_condition(text) -> str:
    return " ".join(str(text or "").split())


def display_name(prefix: str, normalized: str) -> str:
    display = f"{prefix}{normalized}"
    if len(display) > MAX_DISPLAY:
        digest = hashlib.sha1(normalized.encode()).hexdigest()[:8]
        display = display[:240] + f"... [{digest}]"
    return display


def forbidden_operator(rule: str) -> str | None:
    """Return why Intune would reject this rule, or None if it would not."""
    if "(not " in rule:
        return "emits a unary -not, which Intune rejects on every property"
    if "device.deviceName -endsWith" in rule:
        return "emits -endsWith on device.deviceName, which Intune rejects"
    for prop in ("device.model", "device.deviceName", "device.manufacturer"):
        for bad in ("-notStartsWith", "-notEndsWith"):
            if f"{prop} {bad}" in rule:
                return f"emits {bad} on {prop}, which Intune rejects"
    return None


def combine_conditions(conds) -> str | None:
    """One condition for several blocks that target the same (item, group).

    Intune accepts one filter per group per assignment, so two blocks naming the
    same item for the same group become a single OR. None (unconditional) wins
    outright, since it already covers every device in the group.
    """
    conds = {normalize_condition(c) if c is not None else None for c in conds}
    if None in conds or not conds:
        return None
    ordered = sorted(conds)
    if len(ordered) == 1:
        return ordered[0]
    return " OR ".join(f"({c})" for c in ordered)


class Translator:
    """One platform's translator: a filter platform, a display-name prefix and a
    predicate function. Everything else is shared."""

    def __init__(
        self, *, name: str, filter_platform: str, prefix: str, predicate: PredicateFn
    ):
        self.name = name
        self.filter_platform = filter_platform
        self.prefix = prefix
        self._predicate = predicate

    def translate(self, condition: str) -> tuple[str, str]:
        """Return (filter display name, filter rule).

        Raises CondParseError if malformed, UnsupportedCondition if it cannot be
        expressed. Callers must not treat those the same: one is a bug in the
        manifest, the other is a limit of the platform.
        """
        normalized = normalize_condition(condition)
        if not normalized:
            raise CondParseError("empty condition")
        rule = translate_tree(parse(normalized), self._predicate)
        return display_name(self.prefix, normalized), rule

    def is_supported(self, condition: str) -> bool:
        try:
            _display, rule = self.translate(condition)
        except (CondParseError, UnsupportedCondition):
            return False
        return forbidden_operator(rule) is None
