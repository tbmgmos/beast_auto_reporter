# Beast Auto Reporter — App UI Kit

A high-fidelity click-through prototype of the Beast Auto Reporter macOS desktop app.

## Usage

Open `index.html` in a browser. It is self-contained (React via CDN + Google Fonts).

## Screens / States

1. **Idle** — empty state with drop zone
2. **Files loaded** — click drop zone to load demo files
3. **Processing** — click "Создать отчёт" to simulate report generation with step-by-step progress
4. **True Peak dialog** — auto-opens when TP verify is checked and processing completes
5. **Settings dialog** — click the gear icon button

## Components

| File | Description |
|---|---|
| `index.html` | Main self-contained app prototype |
| `Tokens.js` | Shared design token object (colors, radii, fonts) |
| `Header.jsx` | App header with icon + title + settings button |
| `DropZone.jsx` | Drag-and-drop zone + file list |
| `ReportOptions.jsx` | Report type radios + checkbox options |
| `ProgressPanel.jsx` | Progress bar with step labels |
| `Dialogs.jsx` | Settings dialog + True Peak results dialog |

## Design Notes

- Follows macOS native aesthetics: white cards, `#E5E5EA` borders, `#007AFF` primary
- Font: Inter (SF Pro substitute)
- All icons are inline SVG (Lucide-style, 1.5px stroke, rounded)
- No emoji used — all icons are geometric SVGs
- Window chrome simulated with macOS-style traffic light dots
