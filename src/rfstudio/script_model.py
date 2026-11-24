"""Discover the configurable variables of a Python script and patch new values in.

The GUI is generated from what this module finds, so a script needs no special
code to be controllable. It looks at *top-level* assignments whose value is a
literal (``True``/``False``, numbers, strings, lists, ...) or simple arithmetic
on numbers (``433.92 * 1e6``) and groups them into sections taken from comment
headers::

    # ===== Radio =====              <- section header (decorated)
    CENTER_FREQ = 433.92e6  # Hz     <- inline comment becomes the tooltip
    ENABLE_AMP = False

    # [Logging]                      <- also a header
    VERBOSE = True

Optional hints may be placed in the comment on the assignment line (or the
comment lines directly above it)::

    MODE = "fm"        # @options fm|am|usb  Demodulator
    LNA_GAIN = 16      # @range 0..40 @step 8
    API_KEY = ""       # @hide
    SAMPLE_RATE = 8e6  # @label "Sample rate" @unit Hz

``# @section Name`` forces a section, and ``# @gui-begin`` / ``# @gui-end``
limit discovery to the lines between them.
"""

from __future__ import annotations

import ast
import io
import math
import operator
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SECTION = "General"

KIND_BOOL = "bool"
KIND_INT = "int"
KIND_FLOAT = "float"
KIND_STR = "str"
KIND_CHOICE = "choice"
KIND_LITERAL = "literal"  # lists, tuples, dicts, None ... edited as Python text

_DECOR_CHARS = "=-*#~_+<>|/\\"
_ARG_DIRECTIVES = {"options", "choices", "range", "step", "label", "unit", "section"}
_FLAG_DIRECTIVES = {"hide", "hidden", "ignore"}
_DIRECTIVE_KEY_RE = re.compile(r"(?<![\w@])@([A-Za-z][\w-]*)")
_DIRECTIVE_ARG_RE = re.compile(r"\s+(\"[^\"]*\"|'[^']*'|\S+)")


@dataclass
class ScriptVariable:
    name: str
    kind: str
    default: Any
    section: str = DEFAULT_SECTION
    label: str = ""
    description: str = ""
    unit: str = ""
    lineno: int = 0
    source_text: str = ""
    choices: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    hidden: bool = False
    # (start_line, start_col_bytes, end_line, end_col_bytes) of the value expr
    span: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def display_name(self) -> str:
        return self.label or humanize(self.name)

    def coerce(self, value: Any) -> Any:
        """Validate/convert a value for this variable. Raises ValueError."""
        return coerce_value(self, value)


@dataclass
class ScriptSpec:
    path: Path
    variables: list[ScriptVariable] = field(default_factory=list)
    docstring: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def sections(self) -> list[str]:
        seen: list[str] = []
        for v in self.variables:
            if v.section not in seen:
                seen.append(v.section)
        return seen

    def by_name(self) -> dict[str, ScriptVariable]:
        return {v.name: v for v in self.variables}

    def visible(self) -> list[ScriptVariable]:
        return [v for v in self.variables if not v.hidden]


# --------------------------------------------------------------------- helpers

def humanize(name: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").split()
    if not words:
        return name
    acronyms = {"rx", "tx", "lna", "vga", "if", "rf", "fm", "am", "iq", "fft", "usb",
                "id", "ip", "api", "url", "gps", "dc", "ppm", "lo", "bw", "sdr", "cw"}
    out = []
    for w in words:
        lw = w.lower()
        out.append(w.upper() if lw in acronyms else lw)
    text = " ".join(out)
    return text[:1].upper() + text[1:]


def read_source(path: Path) -> str:
    with tokenize.open(str(path)) as fh:  # honours PEP 263 coding cookies and BOM
        return fh.read()


_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}


def _eval_numeric(node: ast.AST) -> int | float:
    """Safely evaluate simple arithmetic on numeric constants."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval_numeric(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval_numeric(node.left), _eval_numeric(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 64:
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    raise ValueError("not numeric")


def _literal(node: ast.AST) -> tuple[bool, Any]:
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    try:
        return True, _eval_numeric(node)
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return False, None


def _kind_for(value: Any) -> str:
    if isinstance(value, bool):
        return KIND_BOOL
    if isinstance(value, int):
        return KIND_INT
    if isinstance(value, float):
        return KIND_FLOAT
    if isinstance(value, str):
        return KIND_STR
    return KIND_LITERAL


def _comment_map(source: str) -> tuple[dict[int, str], dict[int, str]]:
    """Return (full-line comments, inline comments) keyed by 1-based line."""
    full: dict[int, str] = {}
    inline: dict[int, str] = {}
    lines = source.splitlines()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        tokens = []
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        line_no, col = tok.start
        text = tok.string[1:].strip()
        prefix = lines[line_no - 1][:col] if line_no - 1 < len(lines) else ""
        (full if not prefix.strip() else inline)[line_no] = text
    return full, inline


def _strip_decor(text: str) -> str:
    return text.strip(_DECOR_CHARS + " \t")


def _header_title(comment: str) -> str | None:
    """Return the section title if this full-line comment looks like a header."""
    raw = comment.strip()
    if not raw or raw.startswith("!") or raw.lower().startswith(("type:", "noqa", "pylint", "fmt:")):
        return None
    m = re.match(r"^@section\s+(.+)$", raw, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip("\"'")
    if raw.startswith("@"):
        return None
    core = _strip_decor(raw)
    if not core:
        return None  # pure decoration line, e.g. "# ========"
    if core.startswith("[") and core.endswith("]") and len(core) > 2:
        return core[1:-1].strip()
    decor = sum(1 for ch in raw if ch in _DECOR_CHARS)
    if decor >= 3 and (raw[0] in _DECOR_CHARS or raw[-1] in _DECOR_CHARS):
        return core
    letters = [c for c in core if c.isalpha()]
    if letters and core.upper() == core and len(core) <= 60 and len(letters) >= 3:
        return core.title() if " " in core or "_" in core else core.capitalize()
    if core.endswith(":") and len(core) <= 50 and "  " not in core:
        return core[:-1].strip()
    return None


def _parse_directives(text: str) -> tuple[str, dict[str, str]]:
    """Split hint directives out of a comment; returns (plain text, directives).

    Arguments are a single token or a quoted string: ``@label "Centre freq"``.
    """
    directives: dict[str, str] = {}
    plain: list[str] = []
    pos = 0
    for m in _DIRECTIVE_KEY_RE.finditer(text):
        key = m.group(1).lower()
        if key not in _ARG_DIRECTIVES and key not in _FLAG_DIRECTIVES:
            continue
        if m.start() < pos:
            continue
        plain.append(text[pos:m.start()])
        pos = m.end()
        arg = ""
        if key in _ARG_DIRECTIVES:
            am = _DIRECTIVE_ARG_RE.match(text, pos)
            if am:
                arg = am.group(1)
                pos = am.end()
                if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in "\"'":
                    arg = arg[1:-1]
        directives[key] = arg
    plain.append(text[pos:])
    return re.sub(r"\s{2,}", " ", "".join(plain)).strip(), directives


def _parse_number(text: str) -> float:
    return float(parse_number_text(text))


def _apply_directives(var: ScriptVariable, directives: dict[str, str]) -> None:
    if "hide" in directives or "hidden" in directives:
        var.hidden = True
    if "label" in directives and directives["label"]:
        var.label = directives["label"]
    if "unit" in directives:
        var.unit = directives["unit"]
    opts = directives.get("options") or directives.get("choices")
    if opts:
        parts = [p.strip() for p in re.split(r"[|,]", opts) if p.strip()]
        choices: list[Any] = []
        for p in parts:
            ok, val = (True, p)
            if var.kind != KIND_STR:
                try:
                    val = ast.literal_eval(p)
                except (ValueError, SyntaxError):
                    try:
                        val = parse_number_text(p)
                    except ValueError:
                        ok = False
            if ok:
                choices.append(val)
        if var.default not in choices:
            choices.insert(0, var.default)
        var.choices = choices
        var.kind = KIND_CHOICE
    rng = directives.get("range")
    if rng:
        m = re.match(r"^\s*([^.\s][^\s]*?)\s*(?:\.\.|to|,|:)\s*([^\s,]+)(?:\s*(?:,|step)\s*([^\s]+))?\s*$", rng)
        if m:
            try:
                var.minimum = _parse_number(m.group(1))
                var.maximum = _parse_number(m.group(2))
                if m.group(3):
                    var.step = _parse_number(m.group(3))
            except ValueError:
                pass
    if directives.get("step"):
        try:
            var.step = _parse_number(directives["step"])
        except ValueError:
            pass


# ----------------------------------------------------------------- parse API

def parse_script(path: str | Path, source: str | None = None) -> ScriptSpec:
    path = Path(path)
    spec = ScriptSpec(path=path)
    if source is None:
        try:
            source = read_source(path)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            spec.errors.append(f"Cannot read script: {exc}")
            return spec
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        spec.errors.append(f"Syntax error on line {exc.lineno}: {exc.msg}")
        return spec

    spec.docstring = ast.get_docstring(tree) or ""
    full_comments, inline_comments = _comment_map(source)

    # Region markers
    begin = end = None
    for ln, text in full_comments.items():
        low = text.lower().replace(" ", "")
        if low.startswith("@gui-begin") and begin is None:
            begin = ln
        elif low.startswith("@gui-end"):
            end = ln

    # Section headers keyed by line
    headers = {ln: t for ln, t in ((ln, _header_title(c)) for ln, c in full_comments.items()) if t}

    seen: set[str] = set()
    header_lines = sorted(headers)

    def section_for(line: int) -> str:
        current = DEFAULT_SECTION
        for hl in header_lines:
            if hl < line:
                current = headers[hl]
            else:
                break
        return current

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                continue
            target, value_node = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            if not isinstance(stmt.target, ast.Name) or stmt.value is None:
                continue
            target, value_node = stmt.target.id, stmt.value
        else:
            continue
        if target.startswith("_") or target in seen:
            continue
        if begin is not None and stmt.lineno < begin:
            continue
        if end is not None and stmt.lineno > end:
            continue
        ok, value = _literal(value_node)
        if not ok:
            continue

        # Description: inline comment, else adjacent non-header comment block above
        desc_parts: list[str] = []
        directives: dict[str, str] = {}
        above: list[str] = []
        ln = stmt.lineno - 1
        while ln >= 1 and ln in full_comments and ln not in headers:
            text = full_comments[ln]
            if _strip_decor(text) == "" or text.lower().replace(" ", "").startswith("@gui-"):
                break
            above.insert(0, text)
            ln -= 1
        for text in above:
            plain, d = _parse_directives(text)
            directives.update(d)
            if plain:
                desc_parts.append(plain)
        inline = None
        for line in range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1):
            if line in inline_comments:
                inline = inline_comments[line]
        if inline:
            plain, d = _parse_directives(inline)
            directives.update(d)
            if plain:
                desc_parts = [plain]  # inline comment wins
        if "ignore" in directives:
            continue

        seen.add(target)
        var = ScriptVariable(
            name=target,
            kind=_kind_for(value),
            default=value,
            section=section_for(stmt.lineno),
            description=" ".join(desc_parts).strip(),
            lineno=stmt.lineno,
            source_text=ast.get_source_segment(source, value_node) or repr(value),
            span=(value_node.lineno, value_node.col_offset,
                  value_node.end_lineno or value_node.lineno,
                  value_node.end_col_offset or 0),
        )
        if "section" in directives and directives["section"]:
            var.section = directives["section"]
        _apply_directives(var, directives)
        spec.variables.append(var)

    return spec


# ------------------------------------------------------------- value helpers

_SUFFIXES = {"k": 1e3, "m": 1e6, "g": 1e9, "K": 1e3, "M": 1e6, "G": 1e9}


def parse_number_text(text: str) -> int | float:
    """Parse '433.92M', '8e6', '2_000_000', '-3', '0x1F' into a number."""
    t = str(text).strip().replace("_", "").replace(" ", "")
    if not t:
        raise ValueError("empty")
    for unit in ("hz", "Hz", "HZ", "sps", "S/s"):
        if t.endswith(unit) and len(t) > len(unit):
            t = t[: -len(unit)]
            break
    mult = 1.0
    if t[-1] in _SUFFIXES and not t.lower().startswith("0x"):
        mult = _SUFFIXES[t[-1]]
        t = t[:-1]
    try:
        if t.lower().startswith(("0x", "-0x")):
            base = int(t, 16)
            return base * mult if mult != 1.0 else base
        if mult == 1.0 and re.fullmatch(r"[+-]?\d+", t):
            return int(t)
        val = float(t) * mult
    except ValueError as exc:
        raise ValueError(f"'{text}' is not a number") from exc
    if math.isnan(val) or math.isinf(val):
        raise ValueError("number must be finite")
    return val


def coerce_value(var: ScriptVariable, value: Any) -> Any:
    kind = var.kind
    if kind == KIND_BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "on"):
            return True
        if isinstance(value, str) and value.strip().lower() in ("false", "0", "no", "off"):
            return False
        if isinstance(value, int):
            return bool(value)
        raise ValueError("expected True or False")
    if kind in (KIND_INT, KIND_FLOAT):
        num = parse_number_text(value) if isinstance(value, str) else value
        if isinstance(num, bool) or not isinstance(num, (int, float)):
            raise ValueError("expected a number")
        if kind == KIND_INT:
            if isinstance(num, float):
                if not num.is_integer():
                    raise ValueError("expected a whole number")
                num = int(num)
        else:
            num = float(num)
        if var.minimum is not None and num < var.minimum:
            raise ValueError(f"must be ≥ {format_number(var.minimum)}")
        if var.maximum is not None and num > var.maximum:
            raise ValueError(f"must be ≤ {format_number(var.maximum)}")
        return num
    if kind == KIND_STR:
        return "" if value is None else str(value)
    if kind == KIND_CHOICE:
        for c in var.choices or []:
            if c == value or str(c) == str(value):
                return c
        raise ValueError(f"must be one of: {', '.join(map(str, var.choices or []))}")
    # literal: accept python text or an already-built value
    if isinstance(value, str) and not isinstance(var.default, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError) as exc:
            raise ValueError("not a valid Python literal") from exc
    return value


def format_number(value: float | int) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if value.is_integer() and abs(value) >= 1e5:
        for div, suffix in ((1e9, "e9"), (1e6, "e6"), (1e3, "e3")):
            if abs(value) >= div and (value / div) == round(value / div, 6):
                return f"{value / div:g}{suffix}"
    return repr(value)


def value_to_text(var: ScriptVariable, value: Any) -> str:
    if var.kind in (KIND_INT, KIND_FLOAT):
        return format_number(value)
    if var.kind == KIND_STR:
        return str(value)
    return repr(value)


def serialize(value: Any) -> str:
    """Overrides are stored as Python literal text (type-preserving)."""
    return repr(value)


def deserialize(text: str) -> Any:
    return ast.literal_eval(text)


# ------------------------------------------------------------------- patching

def apply_overrides(source: str, spec: ScriptSpec, overrides: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (patched source, warnings). Line numbers are preserved."""
    warnings: list[str] = []
    variables = spec.by_name()
    data = source.encode("utf-8")
    line_starts = [0]
    for i, b in enumerate(data):
        if b == 0x0A:
            line_starts.append(i + 1)

    edits: list[tuple[int, int, bytes]] = []
    for name, value in overrides.items():
        var = variables.get(name)
        if var is None:
            warnings.append(f"'{name}' no longer exists in the script - ignored")
            continue
        try:
            value = var.coerce(value)
        except ValueError as exc:
            warnings.append(f"'{name}': {exc} - using script default")
            continue
        if value == var.default and type(value) is type(var.default):
            continue
        sl, sc, el, ec = var.span
        start = line_starts[sl - 1] + sc
        end = line_starts[el - 1] + ec
        text = repr(value)
        extra_lines = el - sl
        if extra_lines:
            text = "(" + text + "\n" * extra_lines + ")"
        edits.append((start, end, text.encode("utf-8")))

    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        data = data[:start] + repl + data[end:]
    return data.decode("utf-8"), warnings
