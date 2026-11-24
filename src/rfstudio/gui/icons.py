"""Small line icons drawn from inline SVG and tinted to the current theme."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_WRAP = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
         'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')

PATHS = {
    "control": '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/>'
               '<line x1="4" y1="18" x2="20" y2="18"/><circle cx="9" cy="6" r="2" fill="{bg}"/>'
               '<circle cx="15" cy="12" r="2" fill="{bg}"/><circle cx="7" cy="18" r="2" fill="{bg}"/>',
    "device": '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/>'
              '<path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
    "transfer": '<path d="M2 12c2-6 4-6 6 0s4 6 6 0 4-6 6 0"/><path d="M20 4v4h-4" opacity="0"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3'
                'M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
    "play": '<path d="M7 4.5v15l12.5-7.5z" fill="{color}"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="{color}"/>',
    "restart": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "reload": '<path d="M4 12a8 8 0 0 1 13.66-5.66L20 8.5"/><path d="M20 4v4.5h-4.5"/>'
              '<path d="M20 12a8 8 0 0 1-13.66 5.66L4 15.5"/><path d="M4 20v-4.5h4.5"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "save": '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "scroll": '<path d="M12 4v14M6 12l6 6 6-6"/><path d="M5 21h14"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "radio": '<circle cx="12" cy="12" r="2" fill="{color}"/><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7'
             'M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8"/>',
    "log": '<path d="M6 3h9l4 4v14H6z"/><path d="M9 11h7M9 15h7M9 19h4"/>',
}


@lru_cache(maxsize=256)
def _pixmap(name: str, color: str, size: int, bg: str) -> QPixmap:
    body = PATHS[name].replace("{color}", color).replace("{bg}", bg)
    svg = _WRAP.format(color=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pm


def icon(name: str, color: str, bg: str = "#000000", disabled: str | None = None) -> QIcon:
    ic = QIcon()
    for s in (16, 20, 24, 32, 48):
        ic.addPixmap(_pixmap(name, color, s, bg))
        if disabled:
            ic.addPixmap(_pixmap(name, disabled, s, bg), QIcon.Disabled)
    return ic


def status_dot(color: str, size: int = 10) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, size, size)
    p.end()
    return pm


def badge_icon(base: QIcon, color: str, size: int = 64) -> QIcon:
    """App icon with a coloured status dot (used for the tray)."""
    pm = base.pixmap(size, size)
    if pm.isNull():
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = size * 0.36
    p.setPen(QColor("#0e1117"))
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(size - r - 1, size - r - 1, r, r))
    p.end()
    return QIcon(pm)
