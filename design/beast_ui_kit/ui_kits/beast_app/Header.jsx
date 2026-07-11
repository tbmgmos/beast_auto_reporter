// Header component
const { BeastTokens: T } = window;
const C = T.colors;

function Header({ onSettings }) {
  return React.createElement('div', {
    style: {
      height: 52,
      background: 'rgba(245,245,247,0.92)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      borderBottom: `1px solid ${C.border}`,
      display: 'flex',
      alignItems: 'center',
      padding: '0 16px',
      gap: 10,
      flexShrink: 0,
    }
  },
    React.createElement('img', {
      src: '../../assets/app_icon.png',
      alt: '',
      width: 28, height: 28,
      style: { borderRadius: 6, objectFit: 'cover', flexShrink: 0 },
      onError: e => { e.target.style.display='none'; }
    }),
    React.createElement('div', { style: { fontSize: 13, fontWeight: 600, color: C.fg, letterSpacing: '-0.01em', lineHeight: 1 } }, 'Beast Auto Reporter'),
    React.createElement('div', { style: { fontSize: 10, color: C.fg3, marginTop: 1 } }, 'v5.2'),
    React.createElement('div', { style: { flex: 1 } }),
    React.createElement('button', {
      onClick: onSettings,
      style: {
        background: 'transparent', border: `1px solid ${C.borderStrong}`,
        borderRadius: 6, width: 30, height: 30, cursor: 'pointer',
        fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center',
      }
    }, '⚙️')
  );
}

Object.assign(window, { Header });
