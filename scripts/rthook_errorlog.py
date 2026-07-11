"""Runtime hook: capture ALL output to crash log for debugging on other machines."""
import sys
import os
import atexit
import datetime

_log_file = None

def _setup_crash_log():
    global _log_file
    try:
        if getattr(sys, 'frozen', False):
            log_dir = os.path.expanduser('~/Library/Logs/BeastAutoReporter')
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, 'crash.log')
            _log_file = open(log_path, 'w')
            _log_file.write(f"=== Beast Auto Reporter Launch ===\n")
            _log_file.write(f"Time: {datetime.datetime.now()}\n")
            _log_file.write(f"Python: {sys.version}\n")
            _log_file.write(f"Executable: {sys.executable}\n")
            _log_file.write(f"Frozen: {getattr(sys, 'frozen', False)}\n")
            _log_file.write(f"MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}\n")
            _log_file.write(f"Platform: {sys.platform}\n")
            _log_file.write(f"===================================\n\n")
            _log_file.flush()
            sys.stderr = _log_file
            sys.stdout = _log_file
    except Exception as e:
        pass

def _excepthook(exc_type, exc_value, exc_tb):
    """Global exception handler — writes traceback to crash log."""
    import traceback
    if _log_file:
        _log_file.write("\n\n!!! UNHANDLED EXCEPTION !!!\n")
        traceback.print_exception(exc_type, exc_value, exc_tb, file=_log_file)
        _log_file.flush()
    # Also try to show a dialog
    try:
        msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        from PyQt5.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        QMessageBox.critical(None, "Beast Auto Reporter — Crash",
                           f"Приложение завершилось с ошибкой:\n\n{msg[:1000]}\n\n"
                           f"Лог сохранён: ~/Library/Logs/BeastAutoReporter/crash.log")
    except Exception:
        pass

def _on_exit():
    if _log_file:
        _log_file.write(f"\n=== App exit at {datetime.datetime.now()} ===\n")
        _log_file.flush()
        _log_file.close()

_setup_crash_log()
sys.excepthook = _excepthook
atexit.register(_on_exit)
