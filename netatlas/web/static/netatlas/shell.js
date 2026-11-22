// The page shell: the light/dark switch and the current item in the navigation.
// Loaded before the page paints, so a saved theme never flashes. Without JavaScript,
// NetAtlas follows the system setting and the switch stays hidden.
(() => {
  const KEY = "netatlas-theme";
  const root = document.documentElement;
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

  const saved = () => {
    try {
      const value = localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch {
      return null;
    }
  };

  const save = (theme) => {
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      // Private browsing can refuse storage. The switch still works on this page.
    }
  };

  const initial = saved();
  if (initial) root.dataset.theme = initial;

  const current = () => root.dataset.theme || (systemDark.matches ? "dark" : "light");

  const showTheme = (button) => {
    const theme = current();
    button.dataset.active = theme;
    button.setAttribute("aria-pressed", String(theme === "dark"));
  };

  const markCurrentLink = () => {
    const path = window.location.pathname;
    let best = null;
    let length = -1;
    for (const link of document.querySelectorAll("body > header nav a")) {
      const target = new URL(link.href).pathname;
      const matches = target === "/" ? path === "/" : path.startsWith(target);
      if (matches && target.length > length) {
        best = link;
        length = target.length;
      }
    }
    if (best) best.setAttribute("aria-current", "page");
  };

  document.addEventListener("DOMContentLoaded", () => {
    markCurrentLink();
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    button.hidden = false;
    showTheme(button);
    button.addEventListener("click", () => {
      const next = current() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      save(next);
      showTheme(button);
    });
    // Follows the system until someone has chosen a theme.
    systemDark.addEventListener("change", () => showTheme(button));
  });
})();
