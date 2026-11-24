"""Discover a script's command-line flags (argparse or hand-rolled sys.argv checks).

Each flag becomes a command in the app:

* **report** - runs once and its output is shown in the results viewer
  (e.g. ``--history``, ``--top``, ``--pairs``);
* **mode** - a long-running way to start the script, supervised like a normal
  run (e.g. ``--scan``);
* flags that take a value get an input box (e.g. ``--block-freq 433.92e6``).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

FLAG_RE = re.compile(r"^--[A-Za-z][\w-]*$")
_MODE_WORDS = {"scan", "scanner", "scanning", "monitor", "monitoring", "listen", "daemon", "serve", "server",
               "run", "loop", "continuous", "live", "watch", "transmit", "tx", "record", "sweep", "start",
               "forever", "service", "jam", "beacon", "stream", "streaming"}
_MODE_HELP = re.compile(r"\b(continuous(ly)?|forever|until (stopped|interrupted|ctrl)|keep(s)? running|"
                        r"run(s)? the|start(s)? the|monitor(s|ing)?|scan(s|ning)?\b(?! results)|loop)", re.I)
_REPORT_HELP = re.compile(r"\b(show|print|list|display|report|dump|export|summar|statistic|stats|top|history|"
                          r"view)\w*", re.I)


@dataclass
class ScriptCommand:
    flags: list[str]
    help: str = ""
    takes_value: bool = False
    optional_value: bool = False
    metavar: str = ""
    default: Any = None
    choices: list[Any] | None = None
    type_name: str = "str"
    kind: str = "report"           # report | mode
    source: str = "argparse"       # argparse | argv
    hidden: bool = False
    line: int = 0

    @property
    def flag(self) -> str:
        longs = [f for f in self.flags if f.startswith("--")]
        return max(longs, key=len) if longs else self.flags[0]

    @property
    def label(self) -> str:
        words = self.flag.lstrip("-").replace("-", " ").replace("_", " ").split()
        acr = {"tx", "rx", "rf", "fft", "iq", "csv", "db", "id", "ip"}
        text = " ".join(w.upper() if w.lower() in acr else w for w in words)
        return text[:1].upper() + text[1:]

    def argv(self, value: str | None = None) -> list[str]:
        if self.takes_value and value not in (None, ""):
            return [self.flag, str(value)]
        return [self.flag]


@dataclass
class CommandSet:
    commands: list[ScriptCommand] = field(default_factory=list)

    @property
    def reports(self) -> list[ScriptCommand]:
        return [c for c in self.commands if c.kind == "report" and not c.hidden]

    @property
    def modes(self) -> list[ScriptCommand]:
        return [c for c in self.commands if c.kind == "mode" and not c.hidden]

    def by_flag(self) -> dict[str, ScriptCommand]:
        return {c.flag: c for c in self.commands}


def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def _is_sys_argv(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "argv" and isinstance(sub.value, ast.Name) \
                and sub.value.id == "sys":
            return True
        if isinstance(sub, ast.Name) and sub.id in ("argv", "args", "arguments", "flags", "cli_args", "opts"):
            return True
    return False


def classify(cmd: ScriptCommand) -> str:
    name_words = set(re.split(r"[-_]+", cmd.flag.lstrip("-").lower()))
    if cmd.help and _MODE_HELP.search(cmd.help) and not _REPORT_HELP.match(cmd.help.strip()):
        return "mode"
    if name_words & _MODE_WORDS and not (cmd.help and _REPORT_HELP.match(cmd.help.strip())):
        return "mode"
    return "report"


def parse_commands(tree: ast.AST) -> CommandSet:
    found: dict[str, ScriptCommand] = {}

    # 1) argparse: <anything>.add_argument("--flag", ..., help=..., action=...)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"):
            continue
        flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
                 and a.value.startswith("-")]
        if not flags:
            continue  # positional arguments are not commands
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        action = _literal(kw.get("action")) or "store"
        if action in ("help", "version"):
            continue
        nargs = _literal(kw.get("nargs"))
        takes_value = action in ("store", "append", "extend") and nargs != 0
        type_node = kw.get("type")
        type_name = type_node.id if isinstance(type_node, ast.Name) else (
            "float" if isinstance(_literal(kw.get("default")), float) else
            "int" if isinstance(_literal(kw.get("default")), int) and not isinstance(_literal(kw.get("default")), bool)
            else "str")
        help_node = kw.get("help")
        help_text = _literal(help_node) or ""
        default = _literal(kw.get("default"))
        cmd = ScriptCommand(
            flags=flags, help=str(help_text).replace("%(default)s", str(default)),
            takes_value=takes_value, optional_value=nargs == "?", metavar=str(_literal(kw.get("metavar")) or ""),
            default=default, choices=_literal(kw.get("choices")), type_name=type_name,
            hidden=help_node is not None and "SUPPRESS" in ast.unparse(help_node),
            line=node.lineno,
        )
        cmd.kind = classify(cmd)
        found.setdefault(cmd.flag, cmd)

    # 2) hand-rolled: if "--scan" in sys.argv / args ...; sys.argv[1] == "--top"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        parts = [node.left, *node.comparators]
        if not any(_is_sys_argv(p) for p in parts):
            continue
        for p in parts:
            for sub in ast.walk(p):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and FLAG_RE.match(sub.value) \
                        and sub.value not in found:
                    cmd = ScriptCommand(flags=[sub.value], source="argv", line=sub.lineno)
                    cmd.kind = classify(cmd)
                    found[sub.value] = cmd
    # match/case or dict dispatch: {"--top": show_top, ...} next to sys.argv usage
    if any(isinstance(n, ast.Attribute) and n.attr == "argv" for n in ast.walk(tree)):
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and FLAG_RE.match(k.value) \
                            and k.value not in found:
                        cmd = ScriptCommand(flags=[k.value], source="argv", line=k.lineno)
                        cmd.kind = classify(cmd)
                        found[k.value] = cmd

    order = sorted(found.values(), key=lambda c: (c.kind != "mode", c.line))
    return CommandSet(commands=order)
