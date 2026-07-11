# Beast Auto Reporter — Design System

## Product Overview

**Beast Auto Reporter** is a macOS desktop application (PyQt5, v5.2 STABLE) that automates the generation of audio/video quality-control (QC) reports for broadcast and post-production workflows. Target users are QC engineers ("звуковое ОТК") who review episodic audio and video deliverables.

The app accepts audio files (WAV, 2.0/5.1, censored/uncensored versions), video files (MOV/MP4/MKV/MXF), CSV marker lists, and Youlean Loudness PDF reports — then automatically generates a formatted DOCX QC report, complete with loudness metrics (LUFS, True Peak, LRA), defect tables, and AI-generated subjective conclusions (via Ollama/local LLM).

**Company:** Beast Audio QC  
**Version:** 5.2.0 STABLE (built Jan 26, 2026)  
**Platform:** macOS 11+ Universal (Apple Silicon + Intel)  
**Language:** Russian UI  

### Sources provided
- **Codebase:** `Beast_auto_reporter/` (mounted local folder)
  - Main UI: `beast_auto_reporter (v2 beta).py` (3085 lines, PyQt5)
  - Backend: `src/` — audio analysis, PDF extraction, report generation, LLM integration
  - Config: `config/settings.yaml`
  - Docs: `README.md`, `AI_README.txt`, `INSTALL.md`
- **App icon:** `Beast_auto_reporter/app_icon_new.png` (gray cat)
- No Figma link was provided

---

## CONTENT FUNDAMENTALS

### Tone & Voice
- **Language:** Russian throughout. Technical terms remain in English (LUFS, True Peak, LRA, dBTP, CSV, PDF, M&E, DCP).
- **Tone:** Professional, efficient, tool-first. No marketing language. Concise labels, no padding.
- **Casing:** Sentence case for labels ("Создать отчет", "Выбрать папку"). ALL CAPS for severity labels (БЛОКЕР, ТРЕБУЕТ ИСПРАВЛЕНИЯ). Title case for report types.
- **Person:** Second person implied, no pronouns (imperative form: "Перетащите файлы сюда", "Нажмите Создать").
- **Emoji:** Used sparingly in UI as icon substitutes: 📋 ✨ 🔊 📺 🎬 🗑 ⚙️ ✅ ⚠️ ❌ 📁 📄 🔍. Not decorative — each emoji maps to a clear meaning. Emoji only appear in labels/buttons, never in headings or report output.
- **Numbers:** Always include unit: `-23.0 LUFS`, `-2.0 dBTP`, `LRA: 18.0 LU`.
- **Status messages:** Short, emoji-prefixed: "✅ Готово!", "🔍 Анализ файлов...", "📄 Создание отчета..."

### Copy Examples
- Drop zone: "Перетащите файлы сюда" / "аудио, видео, CSV, PDF"
- Primary CTA: "✨ Создать"
- Report types: "📋 Осн.", "🔊 ME", "🔊 ME(наши)", "📺 TIFFLO", "🎬 DCP"
- Options: "✨ AI", "✨ Full analyze", "🎯 TP verify"
- Success: "✅ Отчет создан!\n📁 Папка: {name}\n📄 Файлов: N"
- Error hint: "Ollama недоступна" / "Проверьте, что Ollama запущена"

---

## VISUAL FOUNDATIONS

### Color System
Rooted in Apple's Human Interface Guidelines palette with macOS blue as the single brand accent.

| Role | Token | Hex |
|---|---|---|
| Primary action | `--color-primary` | `#007AFF` |
| Primary hover | `--color-primary-hover` | `#0063D1` |
| Primary press | `--color-primary-press` | `#004EA3` |
| Background | `--color-bg` | `#FFFFFF` |
| Surface / alt bg | `--color-surface` | `#F5F5F7` |
| Border light | `--color-border` | `#E5E5EA` |
| Border | `--color-border-strong` | `#D2D2D7` |
| Text primary | `--color-fg` | `#1D1D1F` |
| Text secondary | `--color-fg-2` | `#86868B` |
| Text tertiary / disabled | `--color-fg-3` | `#AEAEB2` |
| Success / PASS | `--color-success` | `#34C759` |
| Error / FAIL | `--color-error` | `#FF3B30` |
| Warning | `--color-warning` | `#FBBA18` |

QC-specific colors (used in the technical table inside DOCX reports):
- **Green** `#00FA02` — parameters within spec
- **Red** `#E83121` — parameters out of spec
- **Yellow** `#FBBA18` — warning / incomplete data

### Typography
Font: **SF Pro** on-device (macOS system font, `.AppleSystemUIFont`). Substitute: **Inter** from Google Fonts for design artifacts.
Mono: **SF Mono** / substitute: **JetBrains Mono**.

| Level | Size | Weight | Usage |
|---|---|---|---|
| Window title | 16px | 600 | App title bar |
| Section label | 11px | 400 | Gray section headers |
| Card header | 12px | 600 | Dialog/card titles |
| Body | 12px | 400 | File names, values |
| Label | 11px | 400 | Form labels, options |
| Hint | 10px | 400 | Secondary hints |
| Caption | 9px | 400 | Per-channel data |
| Primary button | 13px | 600 | Main CTAs |
| Small button | 11px | 400 | Utility buttons |

### Spacing
Base unit: 2px. Common values: 4, 6, 8, 10, 12, 16, 20, 24px.

### Corner Radius
- `4px` — small buttons, tags, list items
- `6px` — inputs, small cards, secondary buttons
- `8px` — main cards, primary button, dialogs, drop zone
- `12px` — modal dialogs
- `9999px` — radio button indicators (circles)

### Cards
White background (`#FFFFFF`), `1px` border (`#E5E5EA`), `8px` border-radius, inner padding `12px 10px`. No drop shadow by default. Section titles above cards in `#86868B` at 11px.

### Drop Zone
Dashed `1.5px` border (`rgba(0,0,0,0.15)`), `8px` radius, `rgba(0,0,0,0.02)` fill. On hover: border becomes `rgba(0,122,255,0.35)`, fill `rgba(0,122,255,0.04)`. On active drag: solid `2px` border `rgba(0,122,255,0.5)`, fill `rgba(0,122,255,0.08)`.

### Backgrounds
Flat white or `#F5F5F7` surface. No gradients. No images. No textures. Clean, minimal macOS aesthetic.

### Shadows
Minimal. Only for dialogs: `0 4px 12px rgba(0,0,0,0.08)`. Cards use border, not shadow.

### Animation
Subtle. Hover transitions `100–180ms` with `ease`. No bounces or spring animations. No entrance animations. macOS-native feel.

### Hover / Press States
- Hover: `#F5F5F7` background (surfaces), darker shade for colored buttons
- Press: `#E8E8ED` background, `#004EA3` for primary button
- Disabled: `#E8E8ED` background, `#AEAEB2` text

### Borders
`1px solid` with soft gray. Input focus: `1px solid #007AFF`. No thick borders or accent-left borders.

### Transparency / Blur
Occasional transparency on label backgrounds (`background: transparent`). No frosted glass / backdrop blur.

### Imagery
None. The app is pure UI — no photography, no illustrations, no full-bleed images.

### Logo / Mascot
Gray cat — used as the app icon. The cat should feel part of the product identity (friendly, precise, watching everything). The icon uses a rounded-rectangle frame (macOS-style).

---

## ICONOGRAPHY

The codebase uses **emoji as inline icon substitutes** within PyQt5 button labels and status messages. No dedicated icon font or SVG sprite system exists in the codebase.

### Emoji icon mapping
| Emoji | Meaning |
|---|---|
| 📋 | Main/standard report |
| 🔊 | M&E audio report |
| 📺 | TIFFLO report |
| 🎬 | DCP report |
| ✨ | AI / enhanced feature |
| 🗑 | Clear / delete |
| ⚙️ | Settings |
| ✅ | Success / done |
| ⚠️ | Warning |
| ❌ | Error |
| 📁 | Folder |
| 📄 | File / report |
| 🔍 | Analyzing |
| 🎯 | True Peak verify |

### SVG Icons
The drop zone uses a **hand-drawn Lucide-style** FileUp SVG icon (drawn in code via QPainter). For web artifacts, use **Lucide** icons from CDN: `https://unpkg.com/lucide@latest/dist/umd/lucide.min.js`

### App Icon
Gray cat mascot — stored at `assets/app_icon.png`. Use in headers, about screens, splash states.

### Recommended icon set for web artifacts
**Lucide** (consistent stroke weight 1.5px, rounded ends) — matches the hand-drawn FileUp icon style already in the codebase.

---

## FILE INDEX

```
/
├── README.md                      ← This file
├── colors_and_type.css            ← All CSS design tokens
├── SKILL.md                       ← Agent skill definition
├── assets/
│   └── app_icon.png               ← Gray cat app icon (PNG)
├── preview/                       ← Design System tab cards
│   ├── colors-primary.html
│   ├── colors-neutral.html
│   ├── colors-semantic.html
│   ├── type-scale.html
│   ├── type-specimens.html
│   ├── spacing-tokens.html
│   ├── radius-shadows.html
│   ├── comp-buttons.html
│   ├── comp-inputs.html
│   ├── comp-cards.html
│   ├── comp-dropzone.html
│   ├── comp-status-badges.html
│   ├── comp-progress.html
│   └── brand-icon.html
└── ui_kits/
    └── beast_app/
        ├── README.md
        ├── index.html             ← Main app UI kit (click-thru prototype)
        ├── Tokens.jsx
        ├── Header.jsx
        ├── DropZone.jsx
        ├── FilesList.jsx
        ├── ReportOptions.jsx
        ├── ProgressPanel.jsx
        └── Dialogs.jsx
```
