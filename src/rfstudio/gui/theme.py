"""Colour palettes and the application style sheet."""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    sidebar: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    success: str
    warning: str
    danger: str
    console_bg: str
    selection: str


DARK = Palette(
    name="dark", bg="#0e1117", sidebar="#0a0c11", surface="#151a23", surface2="#1b212d",
    border="#262d3b", text="#e6e9f0", muted="#8a93a8", accent="#3d8bfd", accent_hover="#5c9dff",
    accent_text="#ffffff", success="#2fbf71", warning="#f2a93b", danger="#ef5350",
    console_bg="#0a0d12", selection="#27406b",
)

LIGHT = Palette(
    name="light", bg="#f4f6f9", sidebar="#e9edf3", surface="#ffffff", surface2="#f6f8fb",
    border="#dce1e9", text="#1a1f29", muted="#5e6779", accent="#2f6fe0", accent_hover="#255cc0",
    accent_text="#ffffff", success="#1f9d5a", warning="#c27c0e", danger="#d23b3b",
    console_bg="#fbfcfd", selection="#cfe0ff",
)

STATE_COLORS = {
    "running": "success", "starting": "accent", "restarting": "warning",
    "stopping": "warning", "stopped": "muted", "failed": "danger",
}

_current = DARK
_scale = 1.15

TEXT_SIZES = {"small": 0.92, "normal": 1.0, "large": 1.15, "xlarge": 1.32}
BASE_PT = 10.5


def scale() -> float:
    return _scale


def px(value: float) -> int:
    """Scale a pixel size with the chosen text size."""
    return int(round(value * _scale))


def pt(value: float) -> float:
    return round(value * _scale, 1)


def current() -> Palette:
    return _current


def state_color(state: str) -> str:
    return getattr(_current, STATE_COLORS.get(state, "muted"))


def _stylesheet(p: Palette, arrow: str) -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p.text}; background: transparent; }}
QMainWindow, #root {{ background: {p.bg}; }}
QToolTip {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; padding: 6px 8px; border-radius: 6px; }}

#sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
#brandTitle {{ font-size: 12pt; font-weight: 700; }}
#brandSub {{ color: {p.muted}; font-size: 8.5pt; }}
#navButton {{ text-align: left; padding: 9px 14px; border: none; border-radius: 8px; color: {p.muted}; font-weight: 600; }}
#navButton:hover {{ background: {p.surface}; color: {p.text}; }}
#navButton:checked {{ background: {p.surface2}; color: {p.text}; border-left: 3px solid {p.accent}; }}
#sidebarFooter {{ color: {p.muted}; font-size: 8.5pt; }}

#pageTitle {{ font-size: 17pt; font-weight: 700; }}
#pageSubtitle {{ color: {p.muted}; }}
#card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
#cardTitle {{ font-size: 9pt; font-weight: 700; color: {p.muted}; letter-spacing: 1px; }}
#cardBadge {{ color: {p.muted}; background: {p.surface2}; border-radius: 8px; padding: 1px 8px; font-size: 8.5pt; }}
#optionLabel {{ font-weight: 600; }}
#optionName {{ color: {p.muted}; font-family: "JetBrains Mono", "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace; font-size: 8.5pt; }}
#optionLabel {{ font-size: 10.5pt; }}
#optionDesc, #muted {{ color: {p.muted}; }}
#modifiedDot {{ color: {p.accent}; font-size: 8pt; }}
#rowDivider {{ background: {p.border}; max-height: 1px; min-height: 1px; }}
#toggleTile {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 10px; }}
#toggleTile:hover {{ border-color: {p.muted}; }}
#toggleTile[danger="true"] {{ border-color: {p.danger}; }}
#txBadge {{ color: #ffffff; background: {p.danger}; border-radius: 6px; padding: 0px 6px; font-size: 8pt; font-weight: 700; }}
#subTitle {{ color: {p.muted}; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px; }}
#scriptName {{ font-size: 15pt; font-weight: 700; }}
#statValue {{ font-size: 12.5pt; font-weight: 700; }}
#statLabel {{ color: {p.muted}; font-size: 8.5pt; letter-spacing: 1px; }}
#banner {{ background: {p.surface2}; border: 1px solid {p.accent}; border-radius: 10px; }}
#bannerTitle {{ font-weight: 700; font-size: 11pt; }}
#bannerError {{ background: {p.surface2}; border: 1px solid {p.danger}; border-radius: 10px; }}
#emptyState {{ color: {p.muted}; font-size: 11pt; }}

QPushButton {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 8px; padding: 7px 14px; font-weight: 600; }}
QPushButton:hover {{ border-color: {p.accent}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.muted}; border-color: {p.border}; background: {p.surface}; }}
QPushButton[variant="primary"] {{ background: {p.accent}; border-color: {p.accent}; color: {p.accent_text}; }}
QPushButton[variant="primary"]:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.surface2}; border-color: {p.border}; color: {p.muted}; }}
QPushButton[variant="danger"] {{ color: {p.danger}; border-color: {p.danger}; background: transparent; }}
QPushButton[variant="danger"]:hover {{ background: {p.danger}; color: #ffffff; }}
QPushButton[variant="danger"]:disabled {{ color: {p.muted}; border-color: {p.border}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; padding: 5px 8px; }}
QPushButton[variant="ghost"]:hover {{ background: {p.surface2}; border-color: {p.border}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {p.surface2}; border-color: {p.border}; }}
QToolButton:checked {{ background: {p.surface2}; border-color: {p.accent}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p.surface2}; border: 1px solid {p.border}; border-radius: 7px; padding: 5px 8px;
    selection-background-color: {p.selection}; selection-color: {p.text};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {p.accent}; }}
QLineEdit[invalid="true"] {{ border-color: {p.danger}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {p.muted}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 10px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border}; selection-background-color: {p.selection}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QPlainTextEdit#console {{ background: {p.console_bg}; border: 1px solid {p.border}; border-radius: 10px; padding: 8px;
    font-family: "JetBrains Mono", "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace; font-size: 9.5pt; }}
QPlainTextEdit#commandPreview {{ font-family: "JetBrains Mono", "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace; font-size: 9pt; }}

QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}

QTableWidget {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; gridline-color: {p.border};
    selection-background-color: {p.selection}; selection-color: {p.text}; }}
QHeaderView::section {{ background: {p.surface2}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border};
    padding: 6px 8px; font-weight: 700; font-size: 8.5pt; }}
QTableCornerButton::section {{ background: {p.surface2}; border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {p.muted}; padding: 7px 14px; margin-right: 4px;
    border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
QTabBar::tab:hover {{ color: {p.text}; }}
QTableWidget {{ alternate-background-color: {p.surface2}; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.surface2}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 6px; }}

QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {p.text}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}

QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 12px; height: 12px; border-radius: 8px; border: 2px solid {p.muted}; background: {p.surface2}; }}
QRadioButton::indicator:checked {{ border: 2px solid {p.accent}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {p.accent}, stop:0.55 {p.accent}, stop:0.6 {p.surface2}, stop:1 {p.surface2}); }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QStatusBar {{ background: {p.sidebar}; border-top: 1px solid {p.border}; color: {p.muted}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {p.muted}; padding: 2px 10px; font-size: 9pt; }}
QMessageBox, QDialog, QFileDialog {{ background: {p.bg}; }}
"""


def _arrow_file(p: Palette) -> str:
    """QSS can't draw a chevron itself; write a tiny SVG for the combo box arrow."""
    from .. import paths

    path = paths.state_dir() / "theme" / f"chevron-{p.name}.svg"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1.5 3.5L5 7l3.5-3.5" '
            f'fill="none" stroke="{p.muted}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
            encoding="utf-8")
    except OSError:
        return ""
    return path.as_posix()


def _scale_qss(qss: str) -> str:
    qss = re.sub(r"font-size: ([\d.]+)pt", lambda m: f"font-size: {pt(float(m.group(1)) * BASE_PT / 10):g}pt", qss)
    return re.sub(r"(padding|min-height|min-width|width|height|margin)( ?: ?)([\d ]+px[^;]*)",
                  lambda m: m.group(1) + m.group(2) + re.sub(r"(\d+)px", lambda n: f"{px(int(n.group(1)))}px",
                                                           m.group(3)), qss)


def apply(app: QApplication, name: str = "dark", text_size: str = "large") -> Palette:
    global _current, _scale
    _current = LIGHT if name == "light" else DARK
    _scale = TEXT_SIZES.get(text_size, 1.15)
    p = _current
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in (
        (QPalette.Window, p.bg), (QPalette.WindowText, p.text), (QPalette.Base, p.surface2),
        (QPalette.AlternateBase, p.surface), (QPalette.Text, p.text), (QPalette.Button, p.surface2),
        (QPalette.ButtonText, p.text), (QPalette.Highlight, p.selection), (QPalette.HighlightedText, p.text),
        (QPalette.ToolTipBase, p.surface2), (QPalette.ToolTipText, p.text), (QPalette.PlaceholderText, p.muted),
        (QPalette.Link, p.accent),
    ):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    font = QFont()
    font.setFamilies(["Inter", "Segoe UI", "Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans"])
    font.setPointSizeF(pt(BASE_PT))
    app.setFont(font)
    app.setStyleSheet(_scale_qss(_stylesheet(p, _arrow_file(p))))
    return p
