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
