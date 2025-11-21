document.addEventListener("DOMContentLoaded", () => {
  const html = document.documentElement;
  const toggleBtn = document.getElementById("themeToggleBtn");
  if (!html || !toggleBtn) return;

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
    toggleBtn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    try {
      localStorage.setItem("dv-theme", theme);
    } catch (_) {
      /* ignore */
    }
  };

  applyTheme(initial);

  toggleBtn.addEventListener("click", () => {
    const next = html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    toggleBtn.classList.add("animate-toggle");
    setTimeout(() => toggleBtn.classList.remove("animate-toggle"), 300);
  });
});
