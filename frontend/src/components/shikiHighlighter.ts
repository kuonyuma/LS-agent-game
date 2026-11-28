import {
  createHighlighterCoreSync,
  type HighlighterCore,
  type LanguageRegistration,
  type ThemeRegistration,
  type ThemedToken,
} from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import jsonLang from "@shikijs/langs/json";
import pythonLang from "@shikijs/langs/python";

const OBSIDIAN_THEME_NAME = "obsidian-css-vars";

/**
 * 复刻 Obsidian 默认亮/暗主题的语义 TextMate 主题。
 * 全部颜色绑定到 CSS 变量（--code-*），使昼夜模式与 View Transition 径向动画能够 0 闪烁瞬间同步切色。
 */
const OBSIDIAN_THEME: ThemeRegistration = {
  name: OBSIDIAN_THEME_NAME,
  type: "light",
  colors: {
    "editor.foreground": "var(--code-fg)",
    "editor.background": "transparent",
  },
  tokenColors: [
    // Python 本地变量/标识符在 TextMate 语法里常落在裸 source.python 上；
    // 先铺底为 Obsidian 标志性青绿色，下方更精细的标点/关键字/函数定义规则会精准覆盖。
    {
      scope: [
        "source.python",
        "meta.indexed-name.python",
        "meta.item-access.arguments.python",
        "meta.function-call.arguments.python",
      ],
      settings: { foreground: "var(--code-variable)" },
    },
    // 标点、括号、分隔符
    {
      scope: [
        "punctuation",
        "meta.brace",
        "meta.delimiter",
        "keyword.operator.accessor",
        "punctuation.separator",
        "punctuation.terminator",
      ],
      settings: { foreground: "var(--code-punctuation)" },
    },
    // 字符串、Python 文档字符串、模板字面量、正则（翠绿）
    {
      scope: [
        "string",
        "string.quoted",
        "string.quoted.docstring",
        "string.quoted.docstring.multi",
        "string.template",
        "string.regexp",
        "punctuation.definition.string",
      ],
      settings: { foreground: "var(--code-string)" },
    },
    // 关键字、控制流、运算符、布尔/空值常量（洋红）
    {
      scope: [
        "keyword",
        "keyword.control",
        "keyword.operator",
        "storage.type.function",
        "storage.type.class",
        "storage.type.interface",
        "storage.type.type",
        "storage.type.enum",
        "storage.type.namespace",
        "storage.type.struct",
        "storage.type.const",
        "storage.type.let",
        "storage.type.var",
        "storage.type.def",
        "storage.modifier",
        "punctuation.separator.annotation.result",
        "punctuation.definition.template-expression",
        "constant.language.boolean",
        "constant.language.null",
        "constant.language.undefined",
        "constant.language.python",
        "constant.language.json",
        "keyword.other.important",
      ],
      settings: { foreground: "var(--code-keyword)" },
    },
    // 数字与转义占位符（紫罗兰）
    {
      scope: [
        "constant.numeric",
        "constant.character.escape",
        "constant.other.placeholder",
        "constant.character.format.placeholder",
      ],
      settings: { foreground: "var(--code-number)" },
    },
    // 变量、形参、内置函数与普通函数调用（青绿）
    {
      scope: [
        "variable",
        "variable.parameter",
        "variable.other.readwrite",
        "variable.other.constant",
        "variable.language",
        "support.function",
        "meta.function-call entity.name.function",
        "meta.function-call.generic",
        "entity.name.tag.yaml",
      ],
      settings: { foreground: "var(--code-variable)" },
    },
    // 原始类型、类型注解、类、对象属性与方法访问（琥珀橙，对应 list / int / .copy）
    {
      scope: [
        "storage.type.built-in",
        "storage.type.primitive",
        "storage.type.numeric",
        "storage.type.boolean",
        "support.type",
        "support.class",
        "entity.name.type",
        "entity.name.class",
        "entity.other.inherited-class",
        "variable.other.property",
        "variable.other.object.property",
        "meta.member.access.python meta.function-call.generic.python",
        "entity.other.attribute-name",
        "entity.name.function.decorator",
        "meta.decorator",
      ],
      settings: { foreground: "var(--code-type)" },
    },
    // JSON 键名及双引号统一保持清透青绿（优先级高于 support.type 与通用 punctuation）
    {
      scope: [
        "support.type.property-name.json",
        "punctuation.support.type.property-name",
      ],
      settings: { foreground: "var(--code-variable)" },
    },
    // 函数/方法定义名保持沉稳炭黑（对应 def bubble_sort / int main）
    {
      scope: [
        "meta.function entity.name.function",
        "meta.definition.function entity.name.function",
        "entity.name.function.python",
        "meta.function.c entity.name.function.c",
        "meta.function.cpp entity.name.function.cpp",
        "meta.function.definition entity.name.function",
        "meta.method.declaration entity.name.function",
      ],
      settings: { foreground: "var(--code-def)" },
    },
    // 函数调用名（如 C 的 printf）保持青绿，高于上面的通用定义规则
    {
      scope: [
        "meta.function-call.c entity.name.function.c",
        "meta.function-call.cpp entity.name.function.cpp",
        "meta.function-call entity.name.function.member",
      ],
      settings: { foreground: "var(--code-variable)" },
    },
    // HTML / JSX / XML 标签名
    {
      scope: ["entity.name.tag"],
      settings: { foreground: "var(--code-tag)" },
    },
    // YAML 键名覆盖 entity.name.tag
    {
      scope: ["entity.name.tag.yaml"],
      settings: { foreground: "var(--code-variable)" },
    },
    // 注释与 C/C++ #include 预处理指令（柔和浅灰，正体以保证中文注释清晰）
    {
      scope: [
        "comment",
        "punctuation.definition.comment",
        "meta.preprocessor.include",
        "meta.preprocessor.include keyword",
        "meta.preprocessor.include string",
        "meta.preprocessor.include string.quoted",
        "meta.preprocessor.include string.quoted.other.lt-gt.include",
        "meta.preprocessor.include punctuation",
        "meta.preprocessor.include punctuation.definition.string",
        "keyword.control.directive",
      ],
      settings: { foreground: "var(--code-comment)" },
    },
    // Diff 增删与 Markdown 标题
    {
      scope: ["markup.inserted", "punctuation.definition.inserted"],
      settings: { foreground: "var(--code-diff-add)" },
    },
    {
      scope: ["markup.deleted", "punctuation.definition.deleted"],
      settings: { foreground: "var(--code-diff-del)" },
    },
    {
      scope: ["markup.heading", "entity.name.section"],
      settings: { foreground: "var(--code-keyword)", fontStyle: "bold" },
    },
    {
      scope: ["markup.bold"],
      settings: { fontStyle: "bold" },
    },
    {
      scope: ["markup.italic"],
      settings: { fontStyle: "italic" },
    },
  ],
};

type LazyLangLoader = () => Promise<{ default: LanguageRegistration[] }>;

const LAZY_LANG_LOADERS: Record<string, LazyLangLoader> = {
  c: () => import("@shikijs/langs/c"),
  cpp: () => import("@shikijs/langs/cpp"),
  javascript: () => import("@shikijs/langs/javascript"),
  typescript: () => import("@shikijs/langs/typescript"),
  bash: () => import("@shikijs/langs/bash"),
  jsx: () => import("@shikijs/langs/jsx"),
  tsx: () => import("@shikijs/langs/tsx"),
  powershell: () => import("@shikijs/langs/powershell"),
  sql: () => import("@shikijs/langs/sql"),
  html: () => import("@shikijs/langs/html"),
  css: () => import("@shikijs/langs/css"),
  yaml: () => import("@shikijs/langs/yaml"),
  toml: () => import("@shikijs/langs/toml"),
  markdown: () => import("@shikijs/langs/markdown"),
  diff: () => import("@shikijs/langs/diff"),
  java: () => import("@shikijs/langs/java"),
  go: () => import("@shikijs/langs/go"),
  rust: () => import("@shikijs/langs/rust"),
  csharp: () => import("@shikijs/langs/csharp"),
  php: () => import("@shikijs/langs/php"),
  ruby: () => import("@shikijs/langs/ruby"),
  swift: () => import("@shikijs/langs/swift"),
  kotlin: () => import("@shikijs/langs/kotlin"),
  dockerfile: () => import("@shikijs/langs/dockerfile"),
  xml: () => import("@shikijs/langs/xml"),
  ini: () => import("@shikijs/langs/ini"),
  lua: () => import("@shikijs/langs/lua"),
  r: () => import("@shikijs/langs/r"),
  vue: () => import("@shikijs/langs/vue"),
  svelte: () => import("@shikijs/langs/svelte"),
  latex: () => import("@shikijs/langs/latex"),
};

const LANG_ALIAS_MAP: Record<string, string> = {
  py: "python",
  python: "python",
  python3: "python",
  c: "c",
  h: "c",
  cpp: "cpp",
  "c++": "cpp",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  javascript: "javascript",
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  typescript: "typescript",
  jsx: "jsx",
  tsx: "tsx",
  json: "json",
  jsonc: "json",
  json5: "json",
  sh: "bash",
  bash: "bash",
  shell: "bash",
  zsh: "bash",
  console: "bash",
  ps1: "powershell",
  pwsh: "powershell",
  powershell: "powershell",
  sql: "sql",
  mysql: "sql",
  postgresql: "sql",
  postgres: "sql",
  sqlite: "sql",
  html: "html",
  htm: "html",
  css: "css",
  scss: "css",
  less: "css",
  yml: "yaml",
  yaml: "yaml",
  toml: "toml",
  md: "markdown",
  markdown: "markdown",
  diff: "diff",
  patch: "diff",
  java: "java",
  go: "go",
  golang: "go",
  rs: "rust",
  rust: "rust",
  cs: "csharp",
  csharp: "csharp",
  "c#": "csharp",
  php: "php",
  rb: "ruby",
  ruby: "ruby",
  swift: "swift",
  kt: "kotlin",
  kotlin: "kotlin",
  docker: "dockerfile",
  dockerfile: "dockerfile",
  xml: "xml",
  svg: "xml",
  ini: "ini",
  conf: "ini",
  cfg: "ini",
  env: "ini",
  lua: "lua",
  r: "r",
  vue: "vue",
  svelte: "svelte",
  tex: "latex",
  latex: "latex",
};

const DISPLAY_LABEL_MAP: Record<string, string> = {
  python: "Python",
  c: "C",
  cpp: "C++",
  javascript: "JavaScript",
  typescript: "TypeScript",
  jsx: "JSX",
  tsx: "TSX",
  json: "JSON",
  bash: "Bash",
  powershell: "PowerShell",
  sql: "SQL",
  html: "HTML",
  css: "CSS",
  yaml: "YAML",
  toml: "TOML",
  markdown: "Markdown",
  diff: "Diff",
  java: "Java",
  go: "Go",
  rust: "Rust",
  csharp: "C#",
  php: "PHP",
  ruby: "Ruby",
  swift: "Swift",
  kotlin: "Kotlin",
  dockerfile: "Dockerfile",
  xml: "XML",
  ini: "INI",
  lua: "Lua",
  r: "R",
  vue: "Vue",
  svelte: "Svelte",
  latex: "LaTeX",
};

const PLAIN_LANGS = new Set(["text", "txt", "plain", "plaintext", "none", ""]);

let highlighterInstance: HighlighterCore | null = null;
const loadingPromises = new Map<string, Promise<boolean>>();
const TOKEN_CACHE_MAX = 80;
const tokenCache = new Map<string, ThemedToken[][]>();

function getHighlighter(): HighlighterCore {
  if (!highlighterInstance) {
    highlighterInstance = createHighlighterCoreSync({
      themes: [OBSIDIAN_THEME],
      langs: [pythonLang, jsonLang],
      engine: createJavaScriptRegexEngine({ forgiving: true }),
    });
  }
  return highlighterInstance;
}

export function resolveLanguageId(raw?: string | null): string | null {
  if (!raw) return null;
  const cleaned = raw.trim().toLowerCase();
  if (PLAIN_LANGS.has(cleaned)) return null;
  return LANG_ALIAS_MAP[cleaned] ?? null;
}

export function formatLanguageLabel(raw?: string | null): string | null {
  if (!raw) return null;
  const cleaned = raw.trim().toLowerCase();
  if (PLAIN_LANGS.has(cleaned)) return null;
  const canonical = LANG_ALIAS_MAP[cleaned];
  if (canonical && DISPLAY_LABEL_MAP[canonical]) {
    return DISPLAY_LABEL_MAP[canonical];
  }
  // 未内置语法的语言仍优雅首字母大写展示标签
  return cleaned.length <= 4 ? cleaned.toUpperCase() : cleaned[0].toUpperCase() + cleaned.slice(1);
}

export function isLanguageLoaded(langId: string): boolean {
  const hl = getHighlighter();
  return hl.getLoadedLanguages().includes(langId);
}

export async function ensureLanguageLoaded(langId: string): Promise<boolean> {
  const hl = getHighlighter();
  if (hl.getLoadedLanguages().includes(langId)) return true;

  const loader = LAZY_LANG_LOADERS[langId];
  if (!loader) return false;

  const inflight = loadingPromises.get(langId);
  if (inflight) return inflight;

  const task = loader()
    .then(async (mod) => {
      await hl.loadLanguage(mod.default);
      return true;
    })
    .catch(() => false)
    .finally(() => {
      loadingPromises.delete(langId);
    });

  loadingPromises.set(langId, task);
  return task;
}

export function tokenizeCodeSync(code: string, langId: string): ThemedToken[][] | null {
  const hl = getHighlighter();
  if (!hl.getLoadedLanguages().includes(langId)) return null;

  const cacheKey = `${langId}\0${code}`;
  const cached = tokenCache.get(cacheKey);
  if (cached) {
    // LRU 刷新
    tokenCache.delete(cacheKey);
    tokenCache.set(cacheKey, cached);
    return cached;
  }

  try {
    const { tokens } = hl.codeToTokens(code, {
      lang: langId,
      theme: OBSIDIAN_THEME_NAME,
    });
    if (tokenCache.size >= TOKEN_CACHE_MAX) {
      const oldestKey = tokenCache.keys().next().value;
      if (oldestKey !== undefined) tokenCache.delete(oldestKey);
    }
    tokenCache.set(cacheKey, tokens);
    return tokens;
  } catch {
    return null;
  }
}
