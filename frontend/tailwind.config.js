/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // ── Phase 1 redesign: Monokai Pro (Filter Octagon) ─
        // Tailwind's convention is preserved: gray-50 = lightest text
        // tone, gray-950 = deepest void. Light-mode pages will look
        // intentionally broken until Monokai Light is added in a later
        // phase — see `UI 再設計仕様書 §6` for the explicit scope-out.
        gray: {
          50:  '#fcfcfa', // ink-1: primary text (Monokai foreground)
          100: '#c1c0c0', // ink-2: secondary text
          200: '#939293', // ink-3: tertiary text / cool border
          300: '#727072', // ink-4: muted text
          400: '#5b595c', // ink-5: deep muted, hairline ridges
          500: '#5f5d62', // bg-5: selected surface
          600: '#46434a', // bg-4: hover surface
          700: '#3d393e', // bg-3: card surface
          800: '#353136', // bg-2: sidebar / panel
          900: '#2a272b', // bg-1: page background
          950: '#232125', // bg-0: outer void
        },

        // ── Phase 1: Monokai Pro accent (pink) ─────────────
        // Mirrors the `--accent-*` tokens documented in the redesign
        // spec (`UI 再設計仕様書 §2.4`). Drop-in replacement for
        // `terracotta-*` once P1.2 codemod completes.
        accent: {
          50:  '#fff1f5',
          100: '#ffd9e3',
          200: '#ffb3c8',
          300: '#ff8caa',
          400: '#ff6188', // hover
          500: '#fc618d', // primary CTA
          600: '#d94d75', // pressed
          700: '#a83b5b',
          800: '#7c2c43',
          900: '#5a1f30',
          950: '#37121d',
        },

        // ── Semantic Colors ────────────────────────────────
        focus:   '#78dce8', // Phase 1: Monokai cyan (was #3898ec)
        crimson: '#b53333', // Error Crimson (legacy)

        // ── Phase 1: status / priority / decision tokens ───
        // Single shades — usage is `bg-status-progress`,
        // `text-decision`, etc. Multi-shade not needed because every
        // dot/badge sits on a fixed surface.
        'status-todo':     '#939293',
        'status-progress': '#78dce8', // cyan
        'status-hold':     '#ffd866', // yellow
        'status-done':     '#a9dc76', // green
        'status-cancel':   '#ff6188', // pink
        'pri-urgent':      '#ff6188',
        'pri-high':        '#fc9867', // orange
        'pri-medium':      '#ffd866',
        'pri-low':         '#727072',
        decision:          '#ab9df2', // purple
        approved:          '#a9dc76',
        blocked:           '#ffd866',
      },

      fontFamily: {
        // Phase 1 redesign: Fraunces serif headlines, Noto Sans JP body.
        // Falls back to system serif/sans if Google Fonts fail to load.
        serif: [
          'Fraunces',
          '"Hiragino Mincho ProN"',
          '"Yu Mincho"',
          'Georgia',
          'Cambria',
          '"Times New Roman"',
          'serif',
        ],
        sans: [
          '"Noto Sans JP"',
          '"Hiragino Sans"',
          '"Yu Gothic"',
          'system-ui',
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          'Roboto',
          '"Helvetica Neue"',
          'Arial',
          'sans-serif',
        ],
        mono: [
          '"JetBrains Mono"',
          'Menlo',
          'Consolas',
          '"Liberation Mono"',
          'monospace',
        ],
      },

      boxShadow: {
        // Ring-based shadow system (DESIGN.md Level 2)
        'ring':      '0px 0px 0px 1px var(--tw-shadow-color, #d1cfc5)',
        'ring-warm': '0px 0px 0px 1px #d1cfc5',
        'ring-deep': '0px 0px 0px 1px #c2c0b6',
        // Whisper shadow (DESIGN.md Level 3)
        'whisper':   'rgba(0,0,0,0.05) 0px 4px 24px',
        // Inset ring (DESIGN.md Level 4)
        'ring-inset': 'inset 0px 0px 0px 1px rgba(0,0,0,0.15)',
      },

      borderRadius: {
        // Named radii from DESIGN.md
        'comfortable': '8px',    // Standard cards, buttons
        'generous':    '12px',   // Primary buttons, inputs
        'very':        '16px',   // Featured containers
        'highlight':   '24px',   // Tag-like elements
        'maximum':     '32px',   // Hero containers
      },

      lineHeight: {
        'tight-serif': '1.10',
        'snug-serif':  '1.20',
        'normal-serif': '1.30',
        'relaxed-body': '1.60',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}
