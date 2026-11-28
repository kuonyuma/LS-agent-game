import { Moon, Sun } from "lucide-react";
import { useTheme } from "../theme";

interface ThemeToggleButtonProps {
  className?: string;
  iconClassName?: string;
}

export function ThemeToggleButton({
  className = "",
  iconClassName = "h-[15px] w-[15px]",
}: ThemeToggleButtonProps) {
  const { isDark, toggleTheme } = useTheme();

  return (
    <button
      type="button"
      onClick={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        toggleTheme({
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
        });
      }}
      title={isDark ? "切换到浅色模式" : "切换到暗夜模式"}
      aria-label={isDark ? "切换到浅色模式" : "切换到暗夜模式"}
      className={`group relative inline-flex items-center justify-center overflow-hidden rounded-full border border-neutral-200/80 bg-white text-neutral-500 transition-colors duration-200 hover:bg-neutral-100 hover:text-neutral-900 active:scale-90 dark:border-white/[0.11] dark:bg-white/[0.05] dark:text-[#C8CCE0] dark:hover:border-violet-400/35 dark:hover:bg-white/[0.1] dark:hover:text-[#F8F9FF] ${className}`}
    >
      <span className={`relative block ${iconClassName}`}>
        <Sun
          data-theme-toggle-icon="1"
          className={`absolute inset-0 h-full w-full transition-[transform,opacity] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] ${
            isDark
              ? "rotate-0 scale-100 opacity-100"
              : "rotate-90 scale-50 opacity-0"
          }`}
        />
        <Moon
          data-theme-toggle-icon="1"
          className={`absolute inset-0 h-full w-full transition-[transform,opacity] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] ${
            isDark
              ? "-rotate-90 scale-50 opacity-0"
              : "rotate-0 scale-100 opacity-100"
          }`}
        />
      </span>
    </button>
  );
}
