// ReportOptions component — type selector + checkboxes
const _RC = window.BeastTokens.colors;

const REPORT_TYPES = [
  { id: 'main', label: '📋 Осн.' },
  { id: 'me',   label: '🔊 ME' },
  { id: 'me_ours', label: '🔊 ME(наши)' },
  { id: 'tifflo', label: '📺 TIFFLO' },
  { id: 'dcp',  label: '🎬 DCP' },
];

function RadioButton({ checked, onChange, label }) {
  return React.createElement('label', {
    style: { display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 11, color: _RC.fg, userSelect: 'none' }
  },
    React.createElement('div', {
      onClick: onChange,
      style: {
        width: 14, height: 14, borderRadius: '50%', flexShrink: 0, cursor: 'pointer',
        background: checked ? _RC.primary : 'transparent',
        border: checked ? 'none' : `1.5px solid ${_RC.fg3}`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }
    },
      checked && React.createElement('div', { style: { width: 5, height: 5, borderRadius: '50%', background: 'white' } })
    ),
    label
  );
}

function Checkbox({ checked, onChange, label }) {
  return React.createElement('label', {
    style: { display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 11, color: _RC.fg, userSelect: 'none' }
  },
    React.createElement('div', {
      onClick: onChange,
      style: {
        width: 16, height: 16, borderRadius: 4, flexShrink: 0, cursor: 'pointer',
        background: checked ? _RC.primary : 'transparent',
        border: checked ? 'none' : `1.5px solid ${_RC.fg3}`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }
    },
      checked && React.createElement('span', { style: { color: 'white', fontSize: 10, fontWeight: 700, lineHeight: 1 } }, '✓')
    ),
    label
  );
}

function ReportOptions({ reportType, setReportType, aiEnabled, setAiEnabled, fullAnalyze, setFullAnalyze, tpVerify, setTpVerify }) {
  return React.createElement(React.Fragment, null,
    // Report type
    React.createElement('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 12 } },
      REPORT_TYPES.map(t =>
        React.createElement(RadioButton, {
          key: t.id,
          checked: reportType === t.id,
          onChange: () => setReportType(t.id),
          label: t.label,
        })
      )
    ),
    // Options row
    React.createElement('div', { style: { display: 'flex', gap: 16, marginTop: 8, flexWrap: 'wrap' } },
      React.createElement(Checkbox, { checked: aiEnabled, onChange: () => setAiEnabled(!aiEnabled), label: '✨ AI' }),
      React.createElement(Checkbox, { checked: fullAnalyze, onChange: () => setFullAnalyze(!fullAnalyze), label: '✨ Full analyze' }),
      React.createElement(Checkbox, { checked: tpVerify, onChange: () => setTpVerify(!tpVerify), label: '🎯 TP verify' }),
    )
  );
}

Object.assign(window, { ReportOptions, RadioButton, Checkbox });
