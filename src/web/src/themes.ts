export type ThemePresetId = "dark" | "light";
export type ThemeId = "system" | ThemePresetId;
export type ThemeMode = ThemePresetId;

export interface ThemePreset {
  id: ThemePresetId;
  label: string;
  mode: ThemeMode;
  variables: Record<string, string>;
}

export interface ThemeOption {
  id: ThemeId;
  label: string;
}

export const DEFAULT_THEME_ID: ThemeId = "dark";
export const SYSTEM_THEME_MEDIA_QUERY = "(prefers-color-scheme: dark)";

export const THEME_OPTIONS: ThemeOption[] = [
  { id: "system", label: "System" },
  { id: "dark", label: "Dark" },
  { id: "light", label: "Light" },
];

function createThemePreset(
  preset: Omit<ThemePreset, "variables"> & {
    variables: Record<string, string>;
  }
): ThemePreset {
  function requiredVariable(name: string) {
    const value = preset.variables[name];
    if (!value) throw new Error(`Theme ${preset.id} is missing ${name}`);
    return value;
  }

  const variables: Record<string, string> = {
    ...preset.variables,
    "--app-bg": requiredVariable("--background"),
    "--panel-bg": requiredVariable("--panel"),
    "--sidebar-bg": requiredVariable("--sidebar"),
    "--right-panel-bg": requiredVariable("--right-panel"),
    "--text-primary": requiredVariable("--foreground"),
    "--text-secondary": requiredVariable("--muted-foreground"),
    "--text-muted": requiredVariable("--subtle-foreground"),
    "--text-faint": requiredVariable("--faint-foreground"),
    "--border-subtle": requiredVariable("--border"),
    "--border-muted": requiredVariable("--border-muted"),
    "--border-layout": requiredVariable("--border-layout"),
    "--accent-contrast": requiredVariable("--accent-foreground"),
  };

  return {
    ...preset,
    variables,
  };
}

export const THEME_PRESETS: ThemePreset[] = [
  createThemePreset({
    id: "dark",
    label: "Dark",
    mode: "dark",
    variables: {
      "--background": "#101010",
      "--foreground": "#eeeeee",
      "--panel": "#101010",
      "--sidebar": "#1f1f1f",
      "--right-panel": "#1f1f1f",
      "--surface": "#1f1f1f",
      "--surface-muted": "#2f2f2f",
      "--surface-hover": "#343434",
      "--surface-active": "#383838",
      "--border-muted": "#3a3a3ab3",
      "--border-layout": "rgb(64 64 64 / 0.72)",
      "--border": "#444444",
      "--input": "#4a4a4a",
      "--ring": "#1f6feb",
      "--muted-foreground": "#e6e6e6",
      "--subtle-foreground": "#a6a6a6",
      "--faint-foreground": "#858585",
      "--accent": "#4493f8",
      "--accent-soft": "#388bfd1a",
      "--accent-foreground": "#ffffff",
      "--destructive": "#f85149",
      "--destructive-foreground": "#ffffff",
      "--destructive-soft": "#f851491a",
      "--success": "#3fb950",
      "--success-foreground": "#ffffff",
      "--success-soft": "#2ea04326",
      "--warning": "#d29922",
      "--warning-foreground": "#ffffff",
      "--warning-soft": "#bb800926",
      "--info": "#4493f8",
      "--info-foreground": "#ffffff",
      "--info-soft": "#388bfd1a",
      "--overlay": "rgb(1 4 9 / 0.66)",
      "--scrollbar-track": "transparent",
      "--scrollbar-thumb": "#656c7633",
      "--scrollbar-thumb-hover": "#656c7666",
      "--code-bg": "#101010",
      "--code-fg": "#eeeeee",
      "--code-border": "#444444",
      "--markdown-fg": "#eeeeee",
      "--user-message-bg": "#ffffff",
      "--user-message-fg": "#1f2328",
      "--user-message-border": "#ffffff",
      "--shadow-color": "rgb(0 0 0 / 0.42)",
    },
  }),
  createThemePreset({
    id: "light",
    label: "Light",
    mode: "light",
    variables: {
      "--background": "#ffffff",
      "--foreground": "#1f2328",
      "--panel": "#ffffff",
      "--sidebar": "#f6f8fa",
      "--right-panel": "#f6f8fa",
      "--surface": "#ffffff",
      "--surface-muted": "#f6f8fa",
      "--surface-hover": "#eff2f5",
      "--surface-active": "#e6eaef",
      "--border-muted": "#d1d9e0b3",
      "--border-layout": "rgb(209 217 224 / 0.33)",
      "--border": "#d1d9e0",
      "--input": "#d1d9e0",
      "--ring": "#0969da",
      "--muted-foreground": "#59636e",
      "--subtle-foreground": "#59636e",
      "--faint-foreground": "#6e7781",
      "--accent": "#0969da",
      "--accent-soft": "#ddf4ff",
      "--accent-foreground": "#ffffff",
      "--destructive": "#d1242f",
      "--destructive-foreground": "#ffffff",
      "--destructive-soft": "#ffebe9",
      "--success": "#1a7f37",
      "--success-foreground": "#ffffff",
      "--success-soft": "#dafbe1",
      "--warning": "#9a6700",
      "--warning-foreground": "#ffffff",
      "--warning-soft": "#fff8c5",
      "--info": "#0969da",
      "--info-foreground": "#ffffff",
      "--info-soft": "#ddf4ff",
      "--overlay": "rgb(31 35 40 / 0.45)",
      "--scrollbar-track": "transparent",
      "--scrollbar-thumb": "#818b9833",
      "--scrollbar-thumb-hover": "#818b9866",
      "--code-bg": "#f6f8fa",
      "--code-fg": "#1f2328",
      "--code-border": "#d1d9e0",
      "--markdown-fg": "#1f2328",
      "--user-message-bg": "#101010",
      "--user-message-fg": "#ffffff",
      "--user-message-border": "#101010",
      "--shadow-color": "rgb(31 35 40 / 0.08)",
    },
  }),
];

const THEME_PRESET_IDS: readonly ThemePresetId[] = ["dark", "light"];
const THEME_IDS: readonly ThemeId[] = ["system", ...THEME_PRESET_IDS];

export function normalizeThemeId(
  themeId: string | null | undefined
): ThemeId {
  return THEME_IDS.includes(themeId as ThemeId)
    ? (themeId as ThemeId)
    : DEFAULT_THEME_ID;
}

export function resolveSystemThemeMode(): ThemeMode {
  if (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(SYSTEM_THEME_MEDIA_QUERY).matches
  ) {
    return "dark";
  }
  return "light";
}

export function resolveThemeMode(
  themeId: string | null | undefined
): ThemeMode {
  const normalizedThemeId = normalizeThemeId(themeId);
  return normalizedThemeId === "system"
    ? resolveSystemThemeMode()
    : normalizedThemeId;
}

export function resolveThemePreset(
  themeId: string | null | undefined
): ThemePreset {
  const themeMode = resolveThemeMode(themeId);
  return (
    THEME_PRESETS.find((preset) => preset.id === themeMode) ??
    THEME_PRESETS.find((preset) => preset.id === DEFAULT_THEME_ID)!
  );
}

export function isDarkTheme(themeId: string | null | undefined) {
  return resolveThemeMode(themeId) === "dark";
}

export function applyThemePreset(themeId: string | null | undefined) {
  const normalizedThemeId = normalizeThemeId(themeId);
  const preset = resolveThemePreset(themeId);
  const root = document.documentElement;
  root.dataset.theme = normalizedThemeId;
  root.dataset.themeMode = preset.mode;
  root.dataset.themePreset = preset.id;
  root.classList.toggle("dark", preset.mode === "dark");
  root.style.colorScheme = preset.mode;
  for (const [key, value] of Object.entries(preset.variables)) {
    root.style.setProperty(key, value);
  }
  return normalizedThemeId;
}
