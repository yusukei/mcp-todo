import { Sun, Moon, Monitor } from 'lucide-react'
import { useThemeStore, type ThemeMode } from '../../store/theme'

const OPTIONS: { mode: ThemeMode; icon: React.ReactNode; label: string }[] = [
  { mode: 'light', icon: <Sun className="w-3.5 h-3.5" />, label: 'ライト' },
  { mode: 'dark', icon: <Moon className="w-3.5 h-3.5" />, label: 'ダーク' },
  { mode: 'system', icon: <Monitor className="w-3.5 h-3.5" />, label: 'システム' },
]

export default function ThemeToggle() {
  const { mode, setMode } = useThemeStore()

  return (
    <div className="flex items-center gap-0.5 p-0.5 rounded-lg bg-gray-100 dark:bg-gray-700">
      {OPTIONS.map((opt) => (
        <button
          key={opt.mode}
          onClick={() => setMode(opt.mode)}
          className={`p-1.5 rounded-md transition-colors ${
            mode === opt.mode
              ? 'bg-white dark:bg-gray-600 text-terracotta-500 dark:text-terracotta-400 shadow-sm'
              : 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'
          }`}
          title={opt.label}
          aria-label={`${opt.label}モードに切り替え`}
        >
          {opt.icon}
        </button>
      ))}
    </div>
  )
}
