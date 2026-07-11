// ProgressPanel component
const _PC = window.BeastTokens.colors;

const STEPS = [
  { pct: 0,   msg: '' },
  { pct: 10,  msg: '📋 Копирование файлов...' },
  { pct: 20,  msg: '🔍 Анализ файлов...' },
  { pct: 40,  msg: '📹 Обработка видео...' },
  { pct: 50,  msg: '📊 Извлечение данных из PDF...' },
  { pct: 70,  msg: '🎛️ Расчёт loudness...' },
  { pct: 85,  msg: '📝 Генерация заключений...' },
  { pct: 95,  msg: '📄 Создание отчёта...' },
  { pct: 100, msg: '✅ Готово!' },
];

function ProgressPanel({ progress, status, onOpen }) {
  const done = progress === 100;
  const fillColor = done ? _PC.success : _PC.primary;

  return React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
    React.createElement('div', {
      style: {
        height: 4, background: _PC.border, borderRadius: 9999, overflow: 'hidden',
      }
    },
      React.createElement('div', {
        style: {
          height: '100%', width: `${progress}%`,
          background: fillColor, borderRadius: 9999,
          transition: 'width 0.4s ease, background 0.3s',
        }
      })
    ),
    React.createElement('div', {
      style: { fontSize: 11, color: done ? _PC.success : _PC.fg2, textAlign: 'center', minHeight: 16 }
    }, status),
    done && React.createElement('button', {
      onClick: onOpen,
      style: {
        background: _PC.surface, border: `1px solid ${_PC.border}`,
        borderRadius: 6, padding: '4px 12px', fontSize: 11, color: _PC.fg,
        cursor: 'pointer', alignSelf: 'center', marginTop: 2,
      }
    }, '📁 Открыть папку')
  );
}

Object.assign(window, { ProgressPanel });
