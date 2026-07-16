"""Векторные иконки приложения, отрисованные вручную через QPainter.

Вынесены в отдельный модуль (а не оставлены в главном файле), чтобы их
мог импортировать src/yandex_ui/* — главный файл имеет пробел в имени и
не может быть импортирован обратно как модуль Python.
"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_ICON_CACHE = {}


def make_icon_pixmap(name: str, color="#86868B", size: int = 16, stroke: float = 1.6) -> QPixmap:
    qcolor = QColor(color)
    cache_key = (name, qcolor.rgba(), size, round(stroke, 2))
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    pen = QPen(qcolor)
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    s = size / 16.0
    def rect(x, y, w, h):
        return QRectF(x * s, y * s, w * s, h * s)

    if name == "doc":
        painter.drawRoundedRect(rect(2, 1.5, 12, 13), 1.5 * s, 1.5 * s)
        painter.drawLine(QPoint(int(5 * s), int(5.5 * s)), QPoint(int(11 * s), int(5.5 * s)))
        painter.drawLine(QPoint(int(5 * s), int(8 * s)), QPoint(int(11 * s), int(8 * s)))
        painter.drawLine(QPoint(int(5 * s), int(10.5 * s)), QPoint(int(8.5 * s), int(10.5 * s)))
    elif name == "speaker":
        path = QPainterPath()
        path.moveTo(3 * s, 6 * s)
        path.lineTo(4.5 * s, 6 * s)
        path.lineTo(7.5 * s, 3 * s)
        path.lineTo(7.5 * s, 13 * s)
        path.lineTo(4.5 * s, 10 * s)
        path.lineTo(3 * s, 10 * s)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(rect(8.7, 4.6, 3.1, 6.8), -45 * 16, 90 * 16)
    elif name == "tv":
        painter.drawRoundedRect(rect(1.2, 3, 13.6, 9.4), 1.5 * s, 1.5 * s)
        painter.drawEllipse(rect(6.2, 5.8, 3.6, 3.6))
        painter.drawLine(QPoint(int(8 * s), int(12.5 * s)), QPoint(int(8 * s), int(14.5 * s)))
        painter.drawLine(QPoint(int(5 * s), int(14.5 * s)), QPoint(int(11 * s), int(14.5 * s)))
    elif name == "film":
        painter.drawEllipse(rect(1.5, 1.5, 13, 13))
        painter.drawEllipse(rect(6, 6, 4, 4))
        path = QPainterPath()
        path.moveTo(6.6 * s, 5.3 * s)
        path.lineTo(11 * s, 8 * s)
        path.lineTo(6.6 * s, 10.7 * s)
        path.closeSubpath()
        painter.fillPath(path, qcolor)
    elif name == "sparkle":
        path = QPainterPath()
        path.moveTo(8 * s, 1.5 * s)
        path.lineTo(9.6 * s, 6.1 * s)
        path.lineTo(14.2 * s, 8 * s)
        path.lineTo(9.6 * s, 9.9 * s)
        path.lineTo(8 * s, 14.5 * s)
        path.lineTo(6.4 * s, 9.9 * s)
        path.lineTo(1.8 * s, 8 * s)
        path.lineTo(6.4 * s, 6.1 * s)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "target":
        painter.drawEllipse(rect(3, 3, 10, 10))
        painter.drawEllipse(rect(7, 7, 2, 2))
        painter.drawLine(QPoint(int(8 * s), int(1.5 * s)), QPoint(int(8 * s), int(4 * s)))
    elif name == "user":
        painter.drawEllipse(rect(5.2, 2.7, 5.6, 5.6))
        path = QPainterPath()
        path.moveTo(3 * s, 13.3 * s)
        path.cubicTo(3.5 * s, 10.5 * s, 5.7 * s, 9.2 * s, 8 * s, 9.2 * s)
        path.cubicTo(10.3 * s, 9.2 * s, 12.5 * s, 10.5 * s, 13 * s, 13.3 * s)
        painter.drawPath(path)
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(1.5 * s, 4.8 * s)
        path.cubicTo(1.5 * s, 3.5 * s, 2.6 * s, 2.5 * s, 3.9 * s, 2.5 * s)
        path.lineTo(6.2 * s, 2.5 * s)
        path.lineTo(7.7 * s, 4.3 * s)
        path.lineTo(12.3 * s, 4.3 * s)
        path.cubicTo(13.7 * s, 4.3 * s, 14.5 * s, 5.2 * s, 14.5 * s, 6.5 * s)
        path.lineTo(14.5 * s, 11.8 * s)
        path.cubicTo(14.5 * s, 13.2 * s, 13.4 * s, 14.2 * s, 12.1 * s, 14.2 * s)
        path.lineTo(3.9 * s, 14.2 * s)
        path.cubicTo(2.6 * s, 14.2 * s, 1.5 * s, 13.2 * s, 1.5 * s, 11.8 * s)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "trash":
        painter.drawLine(QPoint(int(3 * s), int(5 * s)), QPoint(int(13 * s), int(5 * s)))
        painter.drawLine(QPoint(int(5 * s), int(5 * s)), QPoint(int(4.3 * s), int(13.5 * s)))
        painter.drawLine(QPoint(int(11 * s), int(5 * s)), QPoint(int(11.7 * s), int(13.5 * s)))
        painter.drawLine(QPoint(int(7 * s), int(8 * s)), QPoint(int(7 * s), int(12 * s)))
        painter.drawLine(QPoint(int(9 * s), int(8 * s)), QPoint(int(9 * s), int(12 * s)))
        painter.drawRoundedRect(rect(5.5, 2.7, 5, 2.2), 0.8 * s, 0.8 * s)
    elif name == "gear":
        painter.drawEllipse(rect(5.4, 5.4, 5.2, 5.2))
        for x1, y1, x2, y2 in (
            (8, 1.8, 8, 4.0), (8, 12, 8, 14.2), (1.8, 8, 4.0, 8), (12, 8, 14.2, 8),
            (3.2, 3.2, 4.6, 4.6), (11.4, 11.4, 12.8, 12.8), (3.2, 12.8, 4.6, 11.4), (11.4, 4.6, 12.8, 3.2),
        ):
            painter.drawLine(QPoint(int(x1 * s), int(y1 * s)), QPoint(int(x2 * s), int(y2 * s)))
    elif name == "search":
        painter.drawEllipse(rect(2.5, 2.5, 9, 9))
        painter.drawLine(QPoint(int(10.2 * s), int(10.2 * s)), QPoint(int(14 * s), int(14 * s)))
    elif name == "check":
        painter.drawEllipse(rect(2, 2, 12, 12))
        painter.drawLine(QPoint(int(5 * s), int(8 * s)), QPoint(int(7 * s), int(10.5 * s)))
        painter.drawLine(QPoint(int(7 * s), int(10.5 * s)), QPoint(int(11 * s), int(6 * s)))
    elif name == "copy":
        painter.drawRoundedRect(rect(5.2, 5, 8, 9), 1.4 * s, 1.4 * s)
        painter.drawRoundedRect(rect(2.5, 2, 8, 9), 1.4 * s, 1.4 * s)
    elif name == "music":
        painter.drawLine(QPoint(int(6 * s), int(3 * s)), QPoint(int(6 * s), int(11 * s)))
        painter.drawLine(QPoint(int(6 * s), int(3 * s)), QPoint(int(12 * s), int(1.8 * s)))
        painter.drawLine(QPoint(int(12 * s), int(1.8 * s)), QPoint(int(12 * s), int(9.4 * s)))
        painter.drawEllipse(rect(2, 10, 3.2, 3.2))
        painter.drawEllipse(rect(10, 8.2, 3.2, 3.2))
    elif name == "video":
        painter.drawRoundedRect(rect(1.5, 3.5, 9.5, 8.5), 1.4 * s, 1.4 * s)
        path = QPainterPath()
        path.moveTo(10.8 * s, 6.3 * s)
        path.lineTo(14.2 * s, 4.8 * s)
        path.lineTo(14.2 * s, 10.7 * s)
        path.lineTo(10.8 * s, 9.2 * s)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "csv":
        painter.drawRoundedRect(rect(2, 1.5, 12, 13), 1.5 * s, 1.5 * s)
        painter.drawLine(QPoint(int(4.5 * s), int(6 * s)), QPoint(int(11.5 * s), int(6 * s)))
        painter.drawLine(QPoint(int(4.5 * s), int(8.5 * s)), QPoint(int(11.5 * s), int(8.5 * s)))
        painter.drawLine(QPoint(int(4.5 * s), int(11 * s)), QPoint(int(8 * s), int(11 * s)))
    elif name == "pdf":
        painter.drawRoundedRect(rect(2, 1.5, 12, 13), 1.5 * s, 1.5 * s)
        painter.drawLine(QPoint(int(5 * s), int(10.8 * s)), QPoint(int(5 * s), int(6.2 * s)))
        painter.drawLine(QPoint(int(5 * s), int(6.2 * s)), QPoint(int(7.2 * s), int(6.2 * s)))
        painter.drawArc(rect(6.2, 6.2, 2.2, 2.0), 90 * 16, -180 * 16)
        painter.drawLine(QPoint(int(9.1 * s), int(6.2 * s)), QPoint(int(9.1 * s), int(10.8 * s)))
        painter.drawArc(rect(9.0, 6.2, 2.5, 2.8), 90 * 16, -180 * 16)
    elif name == "params":
        painter.drawLine(QPoint(int(3 * s), int(4.5 * s)), QPoint(int(13 * s), int(4.5 * s)))
        painter.drawLine(QPoint(int(3 * s), int(8 * s)), QPoint(int(13 * s), int(8 * s)))
        painter.drawLine(QPoint(int(3 * s), int(11.5 * s)), QPoint(int(13 * s), int(11.5 * s)))
        painter.drawEllipse(rect(5.1, 3.1, 2.8, 2.8))
        painter.drawEllipse(rect(9.1, 6.6, 2.8, 2.8))
        painter.drawEllipse(rect(6.7, 10.1, 2.8, 2.8))
    elif name == "x":
        painter.drawLine(QPoint(int(4 * s), int(4 * s)), QPoint(int(12 * s), int(12 * s)))
        painter.drawLine(QPoint(int(12 * s), int(4 * s)), QPoint(int(4 * s), int(12 * s)))
    elif name == "folder_open":
        path = QPainterPath()
        path.moveTo(1.5 * s, 5.2 * s)
        path.lineTo(5.8 * s, 5.2 * s)
        path.lineTo(7.2 * s, 3.4 * s)
        path.lineTo(14.4 * s, 3.4 * s)
        path.lineTo(12.7 * s, 13.2 * s)
        path.lineTo(2.7 * s, 13.2 * s)
        path.closeSubpath()
        painter.drawPath(path)
    else:
        painter.drawEllipse(rect(2, 2, 12, 12))

    painter.end()
    _ICON_CACHE[cache_key] = pixmap
    return pixmap


def make_icon(name: str, color="#86868B", size: int = 16, stroke: float = 1.6) -> QIcon:
    return QIcon(make_icon_pixmap(name, color, size, stroke))
