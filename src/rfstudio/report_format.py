"""Turn plain-text script output into structured blocks for nice display.

Recognises:
* tables with ``|`` separators, comma/tab separated rows, or columns aligned
  with two or more spaces (a header row is detected automatically);
* ``key: value`` / ``key = value`` summaries;
* titles (``=== Top frequencies ===``, ``TOP FREQUENCIES``, ``Results:``);
* everything else stays as monospace text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SEPARATOR = re.compile(r"^[\s=\-+*_|#~:.]{3,}$")
KV = re.compile(r"^\s*([A-Za-z][^:=|]{0,48}?)\s*(?::|=)\s+(.+?)\s*$")
NUMBER = re.compile(r"^[-+]?\d[\d,_]*(\.\d+)?([eE][-+]?\d+)?\s*[a-zA-Z%/]*$")


@dataclass
class Block:
    kind: str                        # title | table | kv | text
    text: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def _is_numberish(cell: str) -> bool:
    return bool(NUMBER.match(cell.strip()))


def _split_ws(line: str) -> list[str]:
    return [c for c in re.split(r"\s{2,}|\t", line.strip()) if c != ""]


def _split_pipes(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _cells(line: str) -> list[str]:
    return _split_pipes(line) if "|" in line else _split_ws(line)


def _title_of(line: str) -> str | None:
    raw = line.strip()
    core = raw.strip("=-*#~_[] :").strip()
    if not core or len(core) > 80:
        return None
    if raw != core and re.match(r"^[=\-*#~_\[]{2,}", raw):
        return core
    if raw.endswith(":") and len(raw) < 60 and not KV.match(raw):
        return raw[:-1].strip()
    letters = [c for c in core if c.isalpha()]
    if len(letters) >= 4 and all(c.isupper() for c in letters) and len(core.split()) <= 8:
        return core
    return None


def _table(lines: list[str], had_rule_after_first: bool) -> Block | None:
    if len(lines) < 2:
        return None
    if sum("|" in ln for ln in lines) >= max(2, int(len(lines) * 0.8)):
        rows = [_split_pipes(ln) for ln in lines if "|" in ln]
    elif sum("\t" in ln for ln in lines) >= len(lines) - 1:
        rows = [[c.strip() for c in ln.split("\t")] for ln in lines]
    elif all(ln.count(",") == lines[0].count(",") >= 1 for ln in lines) and len(lines) >= 2:
        rows = [[c.strip() for c in ln.split(",")] for ln in lines]
    else:
        rows = [_split_ws(ln) for ln in lines]
    width = max(len(r) for r in rows)
    if width < 2:
        return None
    same = sum(len(r) == width for r in rows)
    if same < max(2, int(len(rows) * 0.7)):
        return None
    rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in rows]
    first = rows[0]
    rest = rows[1:]
    first_numeric = sum(_is_numberish(c) for c in first)
    rest_numeric = sum(_is_numberish(c) for r in rest for c in r) / max(1, len(rest))
    header = had_rule_after_first or (first_numeric == 0 and rest_numeric >= 1) or \
        (first_numeric == 0 and all(c[:1].isupper() for c in first if c))
    if header and rest:
        return Block("table", headers=first, rows=rest)
    return Block("table", headers=[f"Column {i + 1}" for i in range(width)], rows=rows)


def parse(text: str) -> list[Block]:
    text = ANSI.sub("", text.replace("\r\n", "\n"))
    blocks: list[Block] = []
    chunk: list[str] = []
    rule_after_first = False

    def flush() -> None:
        nonlocal chunk, rule_after_first
        lines = [ln.rstrip() for ln in chunk if ln.strip()]
        chunk = []
        rfa, rule_after_first = rule_after_first, False
        if not lines:
            return
        # leading title line
        if len(lines) > 1:
            t = _title_of(lines[0])
            if t and not _is_numberish(lines[0]):
                blocks.append(Block("title", text=t))
                lines = lines[1:]
        if len(lines) == 1:
            t = _title_of(lines[0])
            if t:
                blocks.append(Block("title", text=t))
                return
        kv = [KV.match(ln) for ln in lines]
        if len(lines) >= 2 and sum(bool(m) for m in kv) >= max(2, int(len(lines) * 0.8)) \
                and not any("|" in ln for ln in lines):
            blocks.append(Block("kv", headers=["Item", "Value"],
                                rows=[[m.group(1).strip(), m.group(2).strip()] for m in kv if m]))
            return
        tail: list[str] = []
        while len(lines) > 2 and len(_cells(lines[-1])) == 1 and len(_cells(lines[0])) >= 2:
            tail.insert(0, lines.pop())  # "Done." after a table
        tbl = _table(lines, rfa)
        if tbl is not None:
            blocks.append(tbl)
            if tail:
                blocks.append(Block("text", text="\n".join(tail)))
            return
        lines = lines + tail
        blocks.append(Block("text", text="\n".join(lines)))

    for line in text.split("\n"):
        if not line.strip():
            flush()
            continue
        if SEPARATOR.match(line):
            # A rule under a single line is a title underline or a table-header rule;
            # anywhere else it separates blocks.
            lines = [c for c in chunk if c.strip()]
            if len(lines) == 2 and _title_of(lines[0]) and len(_cells(lines[1])) >= 2:
                blocks.append(Block("title", text=_title_of(lines[0]) or lines[0].strip()))
                chunk = [lines[1]]
                lines = chunk
            if len(lines) == 1:
                if len(_cells(lines[0])) == 1:  # "Title\n-----"
                    blocks.append(Block("title", text=_title_of(lines[0]) or lines[0].strip().rstrip(":")))
                    chunk = []
                else:
                    rule_after_first = True
                continue
            if chunk:
                flush()
            continue
        title = _title_of(line)
        if title and re.match(r"^\s*[=\-*#~]{2,}", line):  # "=== Title ===" always starts a block
            flush()
            blocks.append(Block("title", text=title))
            continue
        chunk.append(line)
    flush()
    return blocks


def sort_key(cell: str) -> tuple[int, float | str]:
    """Numeric-aware sort ('1,200 MHz' > '433 MHz')."""
    m = re.match(r"^\s*([-+]?\d[\d,_]*(?:\.\d+)?(?:[eE][-+]?\d+)?)", cell)
    if m:
        try:
            return (0, float(m.group(1).replace(",", "").replace("_", "")))
        except ValueError:
            pass
    return (1, cell.lower())


# ------------------------------------------------------------- result files

_ABS_PATH = re.compile(r"((?:[A-Za-z]:[\\/]|/|~[\\/])[^\n\"'<>|*?]*?\.(?:json|csv|tsv))(?=[\s\"').,;:]*$|[\s\"')])",
                       re.I)
_REL_PATH = re.compile(r"(?<![\w/\\.-])([\w.\-]+(?:[\\/][\w.\-]+)*\.(?:json|csv|tsv))\b", re.I)
MAX_ROWS = 5000


def find_output_files(lines: list[str], cwd: str | None) -> list[str]:
    """Existing .json/.csv files mentioned in the output (e.g. 'Results written to: x.json')."""
    import os

    found: list[str] = []
    for line in lines:
        for m in _ABS_PATH.finditer(line):
            p = os.path.expanduser(m.group(1).strip())
            if os.path.isfile(p):
                found.append(os.path.abspath(p))
        if cwd:
            for m in _REL_PATH.finditer(line):
                p = os.path.join(cwd, m.group(1))
                if os.path.isfile(p):
                    found.append(os.path.abspath(p))
    out: list[str] = []
    for f in found:
        if f not in out:
            out.append(f)
    return out


def _fmt(v: object) -> str:
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.6g}" if abs(v) >= 1e6 or abs(v) < 1e-3 else f"{v:.6f}".rstrip("0").rstrip(".")
    if isinstance(v, (list, tuple)):
        if all(not isinstance(x, (dict, list, tuple)) for x in v):
            return ", ".join(_fmt(x) for x in v)
        import json
        return json.dumps(v)
    if isinstance(v, dict):
        import json
        return json.dumps(v)
    return str(v)


def _table_from_records(records: list[dict]) -> Block:
    keys: list[str] = []
    for r in records[:MAX_ROWS]:
        for k in r:
            if k not in keys:
                keys.append(k)
    rows = [[_fmt(r.get(k, "")) for k in keys] for r in records[:MAX_ROWS]]
    return Block("table", headers=[str(k) for k in keys], rows=rows)


def _blocks_from_json(data: object, title: str) -> list[Block]:
    blocks: list[Block] = []
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data):
            blocks += [Block("title", text=title), _table_from_records(data)]
        elif data and all(isinstance(x, (list, tuple)) for x in data):
            width = max(len(x) for x in data)
            blocks += [Block("title", text=title),
                       Block("table", headers=[f"Column {i + 1}" for i in range(width)],
                             rows=[[_fmt(c) for c in x] + [""] * (width - len(x)) for x in data[:MAX_ROWS]])]
        else:
            blocks += [Block("title", text=title),
                       Block("table", headers=["Value"], rows=[[_fmt(x)] for x in data[:MAX_ROWS]])]
        return blocks
    if isinstance(data, dict):
        scalars = [(k, v) for k, v in data.items() if not isinstance(v, (dict, list))
                   or (isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v) and len(v) <= 12)]
        if scalars:
            blocks += [Block("title", text=title),
                       Block("kv", headers=["Item", "Value"], rows=[[str(k), _fmt(v)] for k, v in scalars])]
        # {key: {field: value}} -> one table with the key as first column
        nested = {k: v for k, v in data.items() if isinstance(v, dict)}
        if nested and all(not any(isinstance(x, (dict, list)) for x in v.values()) for v in nested.values()) \
                and len(nested) > 1:
            records = [{"Key": k, **v} for k, v in nested.items()]
            blocks += [Block("title", text=title if not scalars else f"{title}: details"),
                       _table_from_records(records)]
            nested = {}
        for k, v in data.items():
            if (k, v) in [(a, b) for a, b in scalars]:
                continue
            if isinstance(v, (dict, list)) and (k in nested or isinstance(v, list)):
                blocks += _blocks_from_json(v, str(k))
        return blocks
    return [Block("title", text=title), Block("text", text=_fmt(data))]


def file_blocks(path: str) -> list[Block]:
    """Render a JSON / CSV result file as titles + tables."""
    import csv
    import json
    import os

    name = os.path.basename(path)
    try:
        if path.lower().endswith(".json"):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            blocks = _blocks_from_json(data, name)
            return blocks or [Block("text", text="(empty)")]
        with open(path, encoding="utf-8", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel_tab if path.lower().endswith(".tsv") else csv.excel
            rows = [r for r in csv.reader(fh, dialect)][: MAX_ROWS + 1]
        if not rows:
            return [Block("text", text="(empty)")]
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        return [Block("title", text=name), Block("table", headers=rows[0], rows=rows[1:])]
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return [Block("text", text=f"Could not read {name}: {exc}")]
