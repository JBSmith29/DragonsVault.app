document.addEventListener("DOMContentLoaded", () => {
  const html = document.documentElement;
  if (!html) return;

  const toggles = Array.from(
    document.querySelectorAll("[data-theme-toggle], #themeToggleBtn")
  );
  if (!toggles.length) return;

  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  let stored = null;
  try {
    stored = localStorage.getItem("dv-theme");
  } catch (_) {
    stored = null;
  }
  const initial = stored || (prefersDark ? "dark" : "light");

  const applyTheme = (theme) => {
    html.setAttribute("data-bs-theme", theme);
    toggles.forEach((btn) => {
      btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
      if (btn.classList.contains("animate-toggle")) {
        btn.classList.remove("animate-toggle");
      }
    });
    try {
      localStorage.setItem("dv-theme", theme);
    } catch (_) {
      /* ignore */
    }
    document.dispatchEvent(new Event("theme-change"));
  };

  applyTheme(initial);

  toggles.forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      applyTheme(next);
      btn.classList.add("animate-toggle");
      setTimeout(() => btn.classList.remove("animate-toggle"), 300);
    });
  });
});
