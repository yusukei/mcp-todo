/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // ── Warm Gray Override (DESIGN.md) ─────────────────
        // Every gray carries a yellow-brown undertone.
        gray: {
          50:  '#f5f4ed', // Parchment – light page bg
          100: '#faf9f5', // Ivory – card surface, dark-mode primary text
          200: '#f0eee6', // Border Cream
          300: '#e8e6dc', // Warm Sand / Border Warm
          400: '#d1cfc5', // Ring Warm
          500: '#b0aea5', // Warm Silver
          600: '#87867f', // Stone Gray
          700: '#5e5d59', // Olive Gray
          800: '#30302e', // Dark Surface – dark cards, sidebar
          900: '#141413', // Near Black – dark page bg, light primary text
          950: '#0d0d0c', // Deepest dark
        },

        // ── Terracotta Brand Accent ────────────────────────
        // Replaces indigo as the primary interactive color.
        terracotta: {
          50:  '#fdf6f2',
          100: '#fbe9df',
          200: '#f6d0ba',
          300: '#f0b08f',
          400: '#d97757', // Coral Accent
          500: '#c96442', // Brand Terracotta (primary CTA)
          600: '#b05538',
          700: '#8f4430',
          800: '#743929',
          900: '#5f3025',
          950: '#3a1a13',
        },

        // ── Semantic Colors ────────────────────────────────
        focus:   '#3898ec', // Focus Blue – only cool color in system
        crimson: '#b53333', // Error Crimson
      },

      fontFamily: {
        // Anthropic Serif fallback → Georgia
        serif: ['Georgia', 'Cambria', '"Times New Roman"', 'Times', 'serif'],
        // Anthropic Sans fallback → system stack
        sans: [
          'system-ui', '-apple-system', 'BlinkMacSystemFont',
          '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial',
          '"Noto Sans"', '"Noto Sans JP"', 'sans-serif',
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
