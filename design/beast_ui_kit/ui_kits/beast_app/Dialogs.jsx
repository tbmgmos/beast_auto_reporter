// Dialogs — Settings + TruePeak Results
const _DC = window.BeastTokens.colors;

function Overlay({ children, onClose }) {
  return React.createElement('div', {
    style: {
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 100,
    },
    onClick: onClose,
  },
    React.createElement('div', {
      onClick: e => e.stopPropagation(),
      style: {
        background: 'white', borderRadius: 12, padding: '20px',
        minWidth: 340, maxWidth: 480, width: '90%',
        boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
      }
    }, children)
  );
}

function SettingsDialog({ onClose }) {
  const [deleteSources, setDeleteSources] = React.useState(false);
  return React.createElement(Overlay, { onClose },
    React.createElement('div', { style: { fontSize: 12, fontWeight: 600, color: _DC.fg, marginBottom: 16 } }, '⚙️  Дополнительно'),
    React.createElement('label', {
      style: { display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }
    },
      React.createElement('div', {
        onClick: () => setDeleteSources(!deleteSources),
        style: {
          width: 16, height: 16, borderRadius: 4, flexShrink: 0, marginTop: 1,
          background: deleteSources ? _DC.primary : 'transparent',
          border: deleteSources ? 'none' : `1.5px solid ${_DC.fg3}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }
      },
        deleteSources && React.createElement('span', { style: { color: 'white', fontSize: 10, fontWeight: 700 } }, '✓')
      ),
      React.createElement('div', null,
        React.createElement('div', { style: { fontSize: 12, color: _DC.fg } }, '🗑  Удалять исходники после копирования'),
        React.createElement('div', { style: { fontSize: 10, color: _DC.fg2, marginTop: 2 } }, 'PDF, CSV и файлы параметров будут удалены из исходной папки после копирования.')
      )
    ),
    React.createElement('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 } },
      React.createElement('button', {
        onClick: onClose,
        style: { background: _DC.surface, border: `1px solid ${_DC.borderStrong}`, borderRadius: 6, padding: '5px 14px', fontSize: 12, cursor: 'pointer', color: _DC.fg }
      }, 'Отмена'),
      React.createElement('button', {
        onClick: onClose,
        style: { background: _DC.primary, border: 'none', borderRadius: 6, padding: '5px 14px', fontSize: 12, cursor: 'pointer', color: 'white', fontWeight: 600 }
      }, 'Сохранить')
    )
  );
}

function TruePeakDialog({ results, onApply, onClose }) {
  return React.createElement(Overlay, { onClose },
    React.createElement('div', { style: { fontSize: 12, fontWeight: 600, color: _DC.fg, marginBottom: 4 } }, 'True Peak — результаты измерения'),
    React.createElement('div', { style: { fontSize: 11, color: _DC.fg2, marginBottom: 12 } }, 'Измерение True Peak (ITU-R BS.1770-4, 4x oversampling).'),
    results.map((r, i) => {
      const pass = r.precise <= -2.0;
      return React.createElement('div', {
        key: i,
        style: { background: _DC.surface, border: `1px solid ${_DC.border}`, borderRadius: 8, padding: '10px 12px', marginBottom: 8 }
      },
        React.createElement('div', { style: { fontSize: 12, fontWeight: 600, color: _DC.fg } }, r.label),
        React.createElement('div', { style: { fontSize: 10, color: _DC.fg3, marginTop: 1 } }, `Файл: ${r.file}`),
        React.createElement('div', { style: { display: 'flex', gap: 12, alignItems: 'center', marginTop: 6 } },
          React.createElement('span', { style: { fontSize: 11, color: _DC.fg2 } }, `Youlean: ${r.youlean} dBTP →`),
          React.createElement('span', { style: { fontSize: 12, fontWeight: 700, color: pass ? _DC.success : _DC.error } }, `Точно: ${r.precise} dBTP`),
          React.createElement('span', { style: { fontSize: 11, fontWeight: 700, color: pass ? _DC.success : _DC.error } }, pass ? 'PASS' : 'FAIL')
        )
      );
    }),
    React.createElement('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 } },
      React.createElement('button', {
        onClick: onClose,
        style: { background: _DC.surface, border: `1px solid ${_DC.borderStrong}`, borderRadius: 6, padding: '5px 14px', fontSize: 12, cursor: 'pointer', color: _DC.fg }
      }, 'Оставить Youlean'),
      React.createElement('button', {
        onClick: onApply,
        style: { background: _DC.primary, border: 'none', borderRadius: 6, padding: '5px 14px', fontSize: 12, cursor: 'pointer', color: 'white', fontWeight: 600 }
      }, 'Применить точные значения')
    )
  );
}

Object.assign(window, { SettingsDialog, TruePeakDialog });
