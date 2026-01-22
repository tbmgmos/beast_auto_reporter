#!/usr/bin/env python3
"""
Beast Auto Reporter - macOS Launcher
Запускает Streamlit приложение и открывает браузер
"""

import sys
import os
import subprocess
import time
import webbrowser
from pathlib import Path
import signal

# Добавляем путь к приложению
if getattr(sys, 'frozen', False):
    # Запущено из .app bundle
    application_path = os.path.dirname(sys.executable)
    os.chdir(application_path)
else:
    # Запущено из Python
    application_path = os.path.dirname(os.path.abspath(__file__))
    os.chdir(application_path)

print(f"Application path: {application_path}")

# Проверка Ollama
def check_ollama():
    """Проверка доступности Ollama"""
    try:
        result = subprocess.run(
            ['curl', '-s', 'http://localhost:11434/api/tags'],
            capture_output=True,
            timeout=2
        )
        return result.returncode == 0
    except:
        return False

# Запуск Ollama если не запущена
def start_ollama():
    """Попытка запустить Ollama"""
    try:
        # Проверяем, установлена ли ollama
        result = subprocess.run(['which', 'ollama'], capture_output=True)
        if result.returncode == 0:
            print("Starting Ollama...")
            subprocess.Popen(
                ['ollama', 'serve'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(3)  # Ждем запуска
            return True
    except:
        pass
    return False

# Главная функция
def main():
    """Главная функция запуска"""
    
    print("🎵 Beast Auto Reporter")
    print("=" * 50)
    
    # Проверяем Ollama
    if check_ollama():
        print("✓ Ollama доступна")
    else:
        print("⚠ Ollama не запущена")
        if start_ollama():
            if check_ollama():
                print("✓ Ollama запущена")
            else:
                print("⚠ Будет использован fallback режим без AI")
        else:
            print("⚠ Ollama не установлена - работа без AI заключений")
    
    print("\n🚀 Запуск приложения...")
    
    # Находим путь к app.py
    app_path = Path(application_path) / "app.py"
    if not app_path.exists():
        print(f"❌ Ошибка: app.py не найден в {application_path}")
        input("Нажмите Enter для выхода...")
        sys.exit(1)
    
    # Запускаем Streamlit
    port = 8501
    
    try:
        # Запуск Streamlit в subprocess
        env = os.environ.copy()
        env['STREAMLIT_SERVER_HEADLESS'] = 'true'
        env['STREAMLIT_BROWSER_GATHER_USAGE_STATS'] = 'false'
        
        process = subprocess.Popen(
            [
                sys.executable,
                '-m', 'streamlit', 'run',
                str(app_path),
                '--server.headless', 'true',
                '--server.port', str(port),
                '--browser.gatherUsageStats', 'false'
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        print(f"✓ Приложение запущено (PID: {process.pid})")
        print(f"✓ Порт: {port}")
        
        # Ждем запуска сервера
        print("\n⏳ Ожидание запуска сервера...")
        for i in range(30):
            try:
                import urllib.request
                urllib.request.urlopen(f'http://localhost:{port}', timeout=1)
                break
            except:
                time.sleep(0.5)
                if i % 4 == 0:
                    print(".", end="", flush=True)
        
        print("\n✓ Сервер готов!")
        
        # Открываем браузер
        print("\n🌐 Открытие в браузере...")
        time.sleep(1)
        webbrowser.open(f'http://localhost:{port}')
        
        print(f"\n{'=' * 50}")
        print("✅ Beast Auto Reporter запущен!")
        print(f"   URL: http://localhost:{port}")
        print(f"\n💡 Для остановки закройте это окно")
        print(f"{'=' * 50}\n")
        
        # Обработчик сигналов для корректного завершения
        def signal_handler(sig, frame):
            print("\n\n⏹  Остановка приложения...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            print("✓ Приложение остановлено")
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Ждем завершения процесса
        while True:
            return_code = process.poll()
            if return_code is not None:
                if return_code != 0:
                    print(f"\n❌ Процесс завершился с ошибкой (код: {return_code})")
                    stderr = process.stderr.read()
                    if stderr:
                        print(f"\nОшибка:\n{stderr}")
                break
            time.sleep(1)
        
    except KeyboardInterrupt:
        print("\n\n⏹  Остановка приложения...")
        if 'process' in locals():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        print("✓ Приложение остановлено")
    
    except Exception as e:
        print(f"\n❌ Ошибка запуска: {e}")
        import traceback
        traceback.print_exc()
        input("\nНажмите Enter для выхода...")
        sys.exit(1)

if __name__ == "__main__":
    main()

