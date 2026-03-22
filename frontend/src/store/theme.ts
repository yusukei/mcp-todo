import { create } from 'zustand'

export type ThemeMode = 'light' | 'dark' | 'system'

interface ThemeState {
  mode: ThemeMode
  setMode: (mode: ThemeMode) => void
}

function getStoredMode(): ThemeMode {
  try {
    const stored = localStorage.getItem('theme_mode')
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // localStorage unavailable
  }
  return 'system'
}

function getSystemPrefersDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return false
  }
}

function getEffectiveDark(mode: ThemeMode): boolean {
  if (mode === 'dark') return true
  if (mode === 'light') return false
  return getSystemPrefersDark()
}

function applyTheme(mode: ThemeMode) {
  const isDark = getEffectiveDark(mode)
  if (typeof document !== 'undefined') {
    document.documentElement.classList.toggle('dark', isDark)
  }
}

export const useThemeStore = create<ThemeState>((set) => ({
  mode: getStoredMode(),
  setMode: (mode) => {
    try {
      localStorage.setItem('theme_mode', mode)
    } catch {
      // localStorage unavailable
    }
    applyTheme(mode)
    set({ mode })
  },
}))

// Apply theme on load
applyTheme(getStoredMode())

// Listen for system preference changes
try {
  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  mediaQuery.addEventListener('change', () => {
    const { mode } = useThemeStore.getState()
    if (mode === 'system') {
      applyTheme('system')
    }
  })
} catch {
  // matchMedia unavailable (e.g., in test environments)
}
