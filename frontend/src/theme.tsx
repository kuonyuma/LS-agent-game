import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { flushSync } from "react-dom";

export type Theme = "light" | "dark";

export interface ThemeTransitionOrigin {
  x: number;
  y: number;
}

export const THEME_STORAGE_KEY = "ls_agent_studio_theme_v1";

interface ViewTransitionLike {
  ready: Promise<void>;
  finished: Promise<void>;
}

type DocumentWithViewTransition = Document & {
  startViewTransition?: (updateCallback: () => void | Promise<void>) => ViewTransitionLike;
};

interface ThemeContextValue {
  theme: Theme;
  isDark: boolean;
  setTheme: (next: Theme, origin?: ThemeTransitionOrigin) => void;
  toggleTheme: (origin?: ThemeTransitionOrigin) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readInitialTheme(): Theme {
  if (typeof document !== "undefined") {
    if (document.documentElement.dataset.theme === "dark") return "dark";
    if (document.documentElement.dataset.theme === "light") return "light";
  }
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function applyThemeToDocument(theme: Theme) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

function persistTheme(theme: Theme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // 忽略隐私模式或存储受限错误
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readInitialTheme);
  const themeRef = useRef<Theme>(theme);
  const transitioningRef = useRef(false);
  themeRef.current = theme;

  const commitThemeWithTransition = useCallback(
    (next: Theme, origin?: ThemeTransitionOrigin) => {
      if (next === themeRef.current) return;

      const doc = typeof document !== "undefined" ? (document as DocumentWithViewTransition) : null;
      const reducedMotion =
        typeof window !== "undefined" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      // 无 View Transitions API、开启减少动态偏好、或连续快速点击时直接同步切换
      if (!doc?.startViewTransition || reducedMotion || transitioningRef.current) {
        themeRef.current = next;
        applyThemeToDocument(next);
        persistTheme(next);
        setThemeState(next);
        return;
      }

      const root = doc.documentElement;
      const x = origin?.x ?? window.innerWidth - 36;
      const y = origin?.y ?? 26;
      const endRadius = Math.hypot(
        Math.max(x, window.innerWidth - x),
        Math.max(y, window.innerHeight - y),
      );
      const isToDark = next === "dark";

      // 在启动 View Transition 之前同步写好 CSS 变量与状态标记，让合成器第 0 帧即命中 clip-path 关键帧，零白闪
      transitioningRef.current = true;
      root.style.setProperty("--vt-x", `${x.toFixed(1)}px`);
      root.style.setProperty("--vt-y", `${y.toFixed(1)}px`);
      root.style.setProperty("--vt-r", `${endRadius.toFixed(1)}px`);
      root.dataset.themeTransition = isToDark ? "to-dark" : "to-light";

      const transition = doc.startViewTransition(() => {
        // 冻结无关 DOM 的 CSS transition，避免输入框/按钮在 0ms 截帧瞬间残留浅色中间帧
        root.dataset.themeSwitching = "1";
        themeRef.current = next;
        applyThemeToDocument(next);
        persistTheme(next);
        flushSync(() => {
          setThemeState(next);
        });
        // 强制应用最终样式，保证新快照里的每个元素都已是终态配色
        void root.offsetWidth;
      });

      transition.finished
        .catch(() => undefined)
        .finally(() => {
          delete root.dataset.themeTransition;
          delete root.dataset.themeSwitching;
          root.style.removeProperty("--vt-x");
          root.style.removeProperty("--vt-y");
          root.style.removeProperty("--vt-r");
          transitioningRef.current = false;
        });
    },
    [],
  );

  const setTheme = useCallback(
    (next: Theme, origin?: ThemeTransitionOrigin) => {
      commitThemeWithTransition(next, origin);
    },
    [commitThemeWithTransition],
  );

  const toggleTheme = useCallback(
    (origin?: ThemeTransitionOrigin) => {
      const next: Theme = themeRef.current === "dark" ? "light" : "dark";
      commitThemeWithTransition(next, origin);
    },
    [commitThemeWithTransition],
  );

  useEffect(() => {
    applyThemeToDocument(theme);
  }, [theme]);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY) return;
      const next: Theme = event.newValue === "dark" ? "dark" : "light";
      themeRef.current = next;
      setThemeState(next);
      applyThemeToDocument(next);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({
      theme,
      isDark: theme === "dark",
      setTheme,
      toggleTheme,
    }),
    [theme, setTheme, toggleTheme],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme 必须在 ThemeProvider 内部使用");
  }
  return ctx;
}
