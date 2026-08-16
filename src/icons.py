"""Векторные иконки приложения, отрисованные вручную через QPainter.

Вынесены в отдельный модуль (а не оставлены в главном файле), чтобы их
мог импортировать src/yandex_ui/* — главный файл имеет пробел в имени и
не может быть импортирован обратно как модуль Python.
"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

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
    elif name == "refresh":
        # Круговая стрелка: дуга ~290° + треугольная стрелка на переднем конце.
        painter.drawArc(rect(2, 2, 12, 12), -40 * 16, 290 * 16)
        arrow = QPainterPath()
        arrow.moveTo(13.6 * s, 2.2 * s)
        arrow.lineTo(14.6 * s, 5.9 * s)
        arrow.lineTo(11.0 * s, 5.1 * s)
        arrow.closeSubpath()
        painter.setBrush(qcolor)
        painter.drawPath(arrow)
    elif name == "refresh_expressive":
        # Material 3 Expressive: более тяжёлая округлая дуга и крупная
        # стрелка, хорошо читающиеся в 20px-контейнере панели инструментов.
        expressive_pen = QPen(qcolor)
        expressive_pen.setWidthF(2.35 * s)
        expressive_pen.setCapStyle(Qt.RoundCap)
        expressive_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(expressive_pen)
        painter.drawArc(rect(2.6, 2.6, 10.8, 10.8), -25 * 16, 285 * 16)
        arrow = QPainterPath()
        arrow.moveTo(12.2 * s, 1.6 * s)
        arrow.lineTo(14.7 * s, 5.5 * s)
        arrow.lineTo(10.1 * s, 5.0 * s)
        arrow.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor)
        painter.drawPath(arrow)
    elif name == "view_list":
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor)
        for y, marker_width, line_width in ((3.0, 3.0, 8.0), (7.0, 2.4, 9.0), (11.0, 3.4, 7.2)):
            painter.drawRoundedRect(rect(1.5, y, marker_width, 2.4), 1.2 * s, 1.2 * s)
            painter.drawRoundedRect(rect(5.6, y, line_width, 2.4), 1.2 * s, 1.2 * s)
    elif name == "view_grid":
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor)
        for x, y, w, h in (
            (1.7, 1.7, 5.5, 5.5), (8.3, 1.7, 6.0, 4.2),
            (1.7, 8.3, 4.2, 6.0), (7.0, 7.0, 7.3, 7.3),
        ):
            painter.drawRoundedRect(rect(x, y, w, h), 1.7 * s, 1.7 * s)
    elif name == "view_columns":
        painter.setPen(Qt.NoPen)
        painter.setBrush(qcolor)
        painter.drawRoundedRect(rect(1.2, 2.0, 3.7, 12.0), 1.6 * s, 1.6 * s)
        painter.drawRoundedRect(rect(6.1, 1.2, 3.9, 13.6), 1.7 * s, 1.7 * s)
        painter.drawRoundedRect(rect(11.2, 3.0, 3.6, 10.0), 1.5 * s, 1.5 * s)
    elif name == "link":
        # Два скруглённых звена цепочки под углом, как классическая
        # пиктограмма «ссылка»/«поделиться».
        painter.drawRoundedRect(
            QRectF(0, 0, 8 * s, 5 * s).translated(1.2 * s, 8.6 * s), 2.4 * s, 2.4 * s
        )
        painter.save()
        painter.translate(9.5 * s, 5.6 * s)
        painter.rotate(-38)
        painter.drawRoundedRect(QRectF(-4 * s, -2.5 * s, 8 * s, 5 * s), 2.4 * s, 2.4 * s)
        painter.restore()
    elif name == "more":
        # Три горизонтальные точки — кнопка «Ещё» с меню второстепенных
        # действий (см. footer в главном окне).
        painter.setBrush(qcolor)
        for cx in (3.2, 8.0, 12.8):
            painter.drawEllipse(rect(cx - 1.3, 6.7, 2.6, 2.6))
    else:
        painter.drawEllipse(rect(2, 2, 12, 12))

    painter.end()
    _ICON_CACHE[cache_key] = pixmap
    return pixmap


def make_icon(name: str, color="#86868B", size: int = 16, stroke: float = 1.6) -> QIcon:
    return QIcon(make_icon_pixmap(name, color, size, stroke))


def make_text_badge_pixmap(text: str, color="#5856D6", size: int = 16) -> QPixmap:
    """Значок-бейдж с текстом (например, «ME»/«AD») — залитый цветной

    прямоугольник с инициалами внутри. В отличие от силуэтных иконок
    (нота, динамик) короткий текст читается однозначно даже на маленьком
    (13px) размере в дереве — не нужно угадывать форму значка.
    """
    cache_key = ("badge", text, QColor(color).rgba(), size)
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    radius = size * 0.28
    painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), radius, radius)

    # Три- и четырёхбуквенные метки форматов (PDF/CSV/NPR/FLAC) должны
    # помещаться и в 18px-иконку компактного списка; короткие ME/AD при
    # этом сохраняют прежний, более крупный кегль.
    if len(text) >= 4:
        font_scale = 0.30
    elif len(text) == 3:
        font_scale = 0.34
    else:
        font_scale = 0.40
    font = QFont(".AppleSystemUIFont", max(5, int(size * font_scale)), QFont.DemiBold)
    painter.setFont(font)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, text)
    painter.end()

    _ICON_CACHE[cache_key] = pixmap
    return pixmap


def make_text_badge_icon(text: str, color="#5856D6", size: int = 16) -> QIcon:
    return QIcon(make_text_badge_pixmap(text, color, size))


def make_tagged_icon(base_icon: QIcon, tag_color: str, size: int = 16) -> QIcon:
    """Накладывает маленький цветной кружок в правый нижний угол готовой

    иконки — индикатор пользовательского тега. Не заменяет саму иконку
    (в отличие от бейджей ME/AD/VO — тег независим от типа отчёта и может
    быть у файла и у папки любого варианта одновременно).
    """
    pixmap = QPixmap(base_icon.pixmap(size, size))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    dot_size = size * 0.42
    pen = QPen(QColor("#FFFFFF"))
    pen.setWidthF(max(1.0, size * 0.06))
    painter.setPen(pen)
    painter.setBrush(QColor(tag_color))
    painter.drawEllipse(QRectF(size - dot_size, size - dot_size, dot_size, dot_size))
    painter.end()
    return QIcon(pixmap)
