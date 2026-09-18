export type ThemeName = "grey" | "dark" | "light";
export const THEMES: ThemeName[] = ["grey", "dark", "light"];

export function applyTheme(t: ThemeName) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("sf.theme", t); } catch { /* 무시 */ }
}

export function loadTheme(): ThemeName {
  try {
    const v = localStorage.getItem("sf.theme");
    if (v === "grey" || v === "dark" || v === "light") return v;
  } catch { /* 무시 */ }
  return "grey";
}
