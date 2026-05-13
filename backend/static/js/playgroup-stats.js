/*
 * playgroup-stats.js
 *
 * Lazy loader for the "Playgroup stats" panels on the games/players page.
 * When a user clicks the button, we fetch
 * /api/games/pods/<id>/playgroup-stats and render a compact summary of
 * games played, win leaders, commander meta diversity, and streaks.
 */
(function () {
  "use strict";

  const toggles = document.querySelectorAll("[data-pod-stats-toggle]");
  if (!toggles.length) return;

  const loaded = new WeakSet();

  toggles.forEach((button) => {
    button.addEventListener("click", () => {
      const row = button.closest("[data-pod-row]");
      if (!row) return;
      const panel = row.querySelector("[data-pod-stats-panel]");
      const content = row.querySelector("[data-pod-stats-content]");
      const endpoint = row.getAttribute("data-pod-stats-endpoint");
      if (!panel || !content) return;

      const isOpen = panel.classList.toggle("show");
      if (!isOpen) return;

      if (loaded.has(row) || !endpoint) return;
      loaded.add(row);

      fetch(endpoint, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then((resp) => {
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          return resp.json();
        })
        .then((payload) => {
          content.innerHTML = renderStats(payload.data || {});
        })
        .catch((err) => {
          content.innerHTML = `<div class="text-danger">Couldn't load stats: ${escapeHtml(err.message || err)}</div>`;
        });
    });
  });

  function renderStats(report) {
    if (!report.total_games) {
      return '<div class="text-muted">No pod games logged yet. Log some games and come back.</div>';
    }
    const players = (report.players || [])
      .map(
        (p) => `
          <tr>
            <td>${escapeHtml(p.display_name)}</td>
            <td class="text-end">${p.games}</td>
            <td class="text-end">${p.wins}</td>
            <td class="text-end">${p.win_rate !== null ? (p.win_rate * 100).toFixed(0) + "%" : "—"}</td>
            <td class="text-end">${p.longest_streak}</td>
          </tr>`,
      )
      .join("");
    const commanders = (report.commanders || [])
      .slice(0, 8)
      .map(
        (c) => `
          <tr>
            <td>${escapeHtml(c.commander_name)}</td>
            <td class="text-end">${c.games}</td>
            <td class="text-end">${c.wins}</td>
            <td class="text-end">${c.win_rate !== null ? (c.win_rate * 100).toFixed(0) + "%" : "—"}</td>
          </tr>`,
      )
      .join("");

    return `
      <div class="row g-3">
        <div class="col-md-6">
          <div class="text-muted small text-uppercase fw-semibold">Players</div>
          <table class="table table-sm mb-0">
            <thead><tr><th>Name</th><th class="text-end">G</th><th class="text-end">W</th><th class="text-end">WR</th><th class="text-end">Streak</th></tr></thead>
            <tbody>${players || '<tr><td colspan="5" class="text-muted">No player data.</td></tr>'}</tbody>
          </table>
        </div>
        <div class="col-md-6">
          <div class="text-muted small text-uppercase fw-semibold">Commanders</div>
          <table class="table table-sm mb-0">
            <thead><tr><th>Commander</th><th class="text-end">G</th><th class="text-end">W</th><th class="text-end">WR</th></tr></thead>
            <tbody>${commanders || '<tr><td colspan="4" class="text-muted">No commander data.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
      <div class="mt-3 text-muted">
        ${report.total_games} games logged · Meta diversity (Shannon entropy):
        <strong>${report.meta_entropy !== null && report.meta_entropy !== undefined ? report.meta_entropy.toFixed(2) : "—"}</strong>
      </div>
    `;
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }
})();
