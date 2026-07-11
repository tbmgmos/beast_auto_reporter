// DropZone + FilesList component
const _C = window.BeastTokens.colors;

function DropZone({ onDrop, isDragging }) {
  const [hovering, setHovering] = React.useState(false);

  const style = {
    border: hovering || isDragging
      ? `${isDragging ? '2px' : '1.5px'} dashed rgba(0,122,255,${isDragging ? '0.5' : '0.35'})`
      : '1.5px dashed rgba(0,0,0,0.15)',
    borderRadius: 8,
    background: hovering || isDragging
      ? `rgba(0,122,255,${isDragging ? '0.08' : '0.04'})`
      : 'rgba(0,0,0,0.02)',
    minHeight: 90,
    display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center', gap: 4,
    cursor: 'pointer',
    transition: 'all 150ms ease',
  };

  return React.createElement('div', {
    style,
    onMouseEnter: () => setHovering(true),
    onMouseLeave: () => setHovering(false),
    onClick: onDrop,
  },
    React.createElement('svg', {
      width: 28, height: 28, viewBox: '0 0 32 32', fill: 'none',
      stroke: hovering || isDragging ? '#007AFF' : '#AEAEB2',
      strokeWidth: isDragging ? 2 : 1.5, strokeLinecap: 'round', strokeLinejoin: 'round',
    },
      React.createElement('rect', { x: 6, y: 4, width: 20, height: 24, rx: 2 }),
      React.createElement('line', { x1: 16, y1: 22, x2: 16, y2: 12 }),
      React.createElement('polyline', { points: '12,16 16,12 20,16' })
    ),
    React.createElement('div', {
      style: { fontSize: 12, fontWeight: 600, color: hovering || isDragging ? '#007AFF' : '#3D3D3D' }
    }, isDragging ? 'Отпустите файлы' : 'Перетащите файлы сюда'),
    React.createElement('div', {
      style: { fontSize: 10, color: '#6B7280' }
    }, 'аудио, видео, CSV, PDF')
  );
}

function FilesList({ files, onClear }) {
  if (!files.length) return React.createElement('div', {
    style: { fontSize: 12, color: _C.fg3, textAlign: 'center', padding: '10px 0' }
  }, 'Пусто');

  const icons = { wav: '🎵', mp4: '🎬', mov: '🎬', csv: '📋', pdf: '📄', mkv: '🎬' };
  const ext = name => (name.split('.').pop() || '').toLowerCase();

  return React.createElement('div', { style: { display: 'flex', flexDirection: 'column' } },
    files.map((f, i) =>
      React.createElement('div', {
        key: i,
        style: {
          fontSize: 12, color: _C.fg, padding: '4px 0',
          borderBottom: i < files.length - 1 ? `1px solid ${_C.surface}` : 'none',
          display: 'flex', alignItems: 'center', gap: 6,
        }
      },
        React.createElement('span', null, icons[ext(f)] || '📁'),
        React.createElement('span', { style: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, f)
      )
    )
  );
}

Object.assign(window, { DropZone, FilesList });
