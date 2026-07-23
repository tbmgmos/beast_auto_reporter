"""Мелкие вспомогательные функции для UI-слоя интеграции с Яндекс.Диском."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def _stop_thread(thread, timeout_ms: int = 3000) -> None:
    """Останавливает фоновый QThread перед закрытием окна.

    Такие потоки создаются без Qt-родителя и держатся только Python-
    ссылкой (self.thread, self._inflight[...] и т.п.) — если объект-
    владелец уничтожится, пока поток ещё isRunning(), PyQt валит процесс
    с "QThread: Destroyed while thread is still running". terminate()
    используется только как крайняя мера, если поток не завершился сам
    за timeout_ms.

    closeEvent/done() в разных местах приложения вызывают эту функцию
    подряд для нескольких потоков — если бы одна из них подняла
    исключение (например, устаревшая C++-обёртка Qt-объекта), все
    последующие вызовы в цепочке не выполнились бы, и какой-то поток
    остался бы неостановленным именно в момент закрытия окна. Поэтому
    ошибки здесь только логируются, а не пробрасываются наружу.
    """
    if thread is None:
        return
    try:
        if not thread.isRunning():
            return
        if not thread.wait(timeout_ms):
            thread.terminate()
            thread.wait()
    except Exception as exc:
        logger.warning(f"Не удалось корректно остановить фоновый поток: {exc}")


def _quick_look_preview(path) -> None:
    """Открывает системный Quick Look (как пробел в Finder на macOS).

    `qlmanage -p` — встроенная в macOS утилита показа Quick Look-панели
    без запуска полноценного приложения. Если недоступна (не macOS) —
    откатываемся на обычное открытие файла системным приложением по умолчанию.
    """
    try:
        subprocess.Popen(
            ["qlmanage", "-p", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        subprocess.Popen(["open", str(path)])


def _send_system_notification(title: str, message: str) -> None:
    """Показывает нативное macOS-уведомление (Центр уведомлений) через osascript."""
    try:
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        subprocess.Popen(["osascript", "-e", script])
    except Exception as exc:
        logger.warning(f"Не удалось отправить системное уведомление: {exc}")


def _play_sound(name: str = "Glass") -> None:
    """Проигрывает системный звук macOS (`afplay`) неблокирующе."""
    try:
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{name}.aiff"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.warning(f"Не удалось проиграть звук: {exc}")


def _format_disk_file_size(size) -> str:
    """Байты -> человекочитаемый размер (Б/КБ/МБ/ГБ)."""
    try:
        size = float(size)
    except (TypeError, ValueError):
        return ""
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _format_disk_modified_date(modified: str) -> str:
    """ISO 8601 из API Диска -> «ДД.ММ.ГГГГ ЧЧ:ММ»."""
    if not modified:
        return ""
    try:
        from datetime import datetime
        return datetime.fromisoformat(modified).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return modified


def _relative_date_label(d) -> str:
    """Человекочитаемая давность даты («сегодня», «вчера», «3 дн. назад»).

    Для дат старше ~30 дней возвращается точная дата — "127 дн. назад"
    неудобно воспринимается, абсолютная дата нагляднее. "дн."/"нед." —
    намеренно несклоняемые сокращения (иначе пришлось бы разбирать
    русское согласование числительных 1/2-4/5+ отдельно).
    """
    if d is None:
        return ""
    from datetime import date as _date
    today = _date.today()
    delta = (today - d).days
    if delta < 0:
        return d.strftime("%d.%m.%Y")
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "вчера"
    if delta < 7:
        return f"{delta} дн. назад"
    if delta < 30:
        return f"{delta // 7} нед. назад"
    return d.strftime("%d.%m.%Y")


def _relative_time_label(iso_string) -> str:
    """Человекочитаемая давность момента времени с минутной/часовой

    гранулярностью («только что», «5 мин. назад», «2 ч. назад») — в
    отличие от _relative_date_label (только день/неделя), нужна там, где
    событие может повторяться чаще раза в день, например статус "когда
    последний раз синхронизировались конфиги". Старше суток — делегирует
    в _relative_date_label (абсолютные дни/недели удобнее "27 ч. назад").
    """
    if not iso_string:
        return ""
    from datetime import datetime, timezone
    try:
        parsed = datetime.fromisoformat(iso_string)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta_seconds = max((now - parsed).total_seconds(), 0)
    if delta_seconds < 60:
        return "только что"
    minutes = int(delta_seconds // 60)
    if minutes < 60:
        return f"{minutes} мин. назад"
    hours = int(delta_seconds // 3600)
    if hours < 24:
        return f"{hours} ч. назад"
    return _relative_date_label(parsed.astimezone().date())
