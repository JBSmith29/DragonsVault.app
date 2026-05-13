/*
 * deck-insights.js
 *
 * Drives the "Deck Insights" panel on the folder detail page. Each tab's
 * data is fetched lazily the first time the tab is activated so the initial
 * page load isn't penalised for users who don't open it.
 *
 * Endpoints come from ``data-insights-endpoints`` on the container so the
 * JS stays route-agnostic.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-deck-insights]");
  if (!root) return;

  const endpoints = safeJson(root.getAttribute("data-insights-endpoints") || "{}");
  const folderId = root.getAttribute("data-folder-id");
  const loaded = new Set();
  const tabButtons = root.querySelectorAll("[data-insights-panel]");

  tabButtons.forEach((btn) => {
    btn.addEventListener("shown.bs.tab", (event) => {
      const key = event.target.getAttribute("data-insights-panel");
      if (!key || loaded.has(key)) return;
      loaded.add(key);
      loadPanel(key);
    });
  });

  // Always load the first (active) tab on mount.
  const firstActive = root.querySelector("[data-insights-panel].active");
  if (firstActive) {
    const key = firstActive.getAttribute("data-insights-panel");
    loaded.add(key);
    loadPanel(key);
  }

  // Compare flow: open folder-picker modal, render list, attach click handlers.
  const compareBtn = root.querySelector("[data-insights-compare-open]");
  if (compareBtn) {
    compareBtn.addEventListener("click", () => openCompareModal());
  }

  // ---------------------------------------------------------------------
  // Panel loaders
  // ---------------------------------------------------------------------
  function loadPanel(key) {
    const target = root.querySelector(`[data-insights-content="${key}"]`);
    if (!target) return;
    const url = endpoints[key];
    if (!url) {
      target.innerHTML = "<div class=\"text-muted small\">Endpoint not configured.</div>";
      return;
    }
    fetchJson(url)
      .then((payload) => renderPanel(key, target, payload))
      .catch((err) => {
        target.innerHTML =
          `<div class="alert alert-warning mb-0 small">Couldn't load ${escapeHtml(key)}: ${escapeHtml(err.message || String(err))}</div>`;
      });
  }

  function renderPanel(key, target, payload) {
    const data = payload && payload.data;
    if (!data) {
      target.innerHTML = "<div class=\"text-muted small\">No data returned.</div>";
      return;
    }
    switch (key) {
      case "legality":
        target.innerHTML = renderLegality(data);
        break;
      case "archetype":
        target.innerHTML = renderArchetype(data);
        break;
      case "manaBase":
        target.innerHTML = renderManaBase(data);
        break;
      case "budget":
        target.innerHTML = renderBudget(data);
        break;
      case "winRate":
        target.innerHTML = renderWinRate(data);
        break;
      default:
        target.textContent = JSON.stringify(data);
    }
  }

  // ---------------------------------------------------------------------
  // Renderers
  // ---------------------------------------------------------------------
  function renderLegality(reports) {
    // /legality/all returns a list of reports
    const list = Array.isArray(reports) ? reports : [reports];
    if (!list.length) return "<div class=\"text-muted small\">No formats evaluated.</div>";
    const rows = list
      .map((report) => {
        const badge = report.legal
          ? '<span class="badge bg-success">Legal</span>'
          : '<span class="badge bg-danger">Issues</span>';
        const issues = (report.issues || [])
          .slice(0, 6)
          .map((issue) => {
            const sev = issue.severity === "error" ? "danger" : issue.severity === "warning" ? "warning" : "info";
            return `<li class="small"><span class="badge bg-${sev} me-1">${escapeHtml(issue.severity)}</span>${escapeHtml(issue.message)}</li>`;
          })
          .join("");
        const more = (report.issues || []).length > 6
          ? `<li class="small text-muted">…and ${report.issues.length - 6} more</li>`
          : "";
        return `
          <tr>
            <td class="fw-semibold">${escapeHtml(report.format.label)}</td>
            <td>${badge}</td>
            <td class="small text-muted">${report.mainboard_size} cards</td>
            <td>
              ${issues ? `<ul class="list-unstyled mb-0">${issues}${more}</ul>` : '<span class="text-muted small">Passes all checks.</span>'}
            </td>
          </tr>
        `;
      })
      .join("");
    return `
      <div class="table-responsive">
        <table class="table table-sm align-middle mb-0">
          <thead><tr><th>Format</th><th>Status</th><th>Size</th><th>Notes</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  function renderArchetype(report) {
    const primary = report.primary || {};
    const secondary = report.secondary;
    const scoreRows = Object.entries(report.scores || {})
      .sort((a, b) => b[1] - a[1])
      .map(([name, score]) => {
        const pct = Math.max(0, Math.min(100, Math.round(score)));
        return `
          <div class="mb-2">
            <div class="d-flex justify-content-between small">
              <span class="text-capitalize">${escapeHtml(name)}</span>
              <span class="text-muted">${score.toFixed(1)}</span>
            </div>
            <div class="progress" style="height:6px;">
              <div class="progress-bar" style="width:${pct}%"></div>
            </div>
          </div>`;
      })
      .join("");
    const reasons = (primary.reasons || []).map((r) => `<li class="small">${escapeHtml(r)}</li>`).join("");
    return `
      <div class="d-flex flex-wrap align-items-start gap-3">
        <div class="flex-grow-1">
          <div class="small text-muted text-uppercase fw-semibold">Primary archetype</div>
          <div class="h4 text-capitalize mb-2">${escapeHtml(primary.name || "—")}</div>
          ${reasons ? `<ul class="mb-2">${reasons}</ul>` : ""}
          ${secondary ? `<div class="small">Also looks like <strong class="text-capitalize">${escapeHtml(secondary.name)}</strong>.</div>` : ""}
        </div>
        <div style="min-width:260px;flex-grow:1;">${scoreRows}</div>
      </div>`;
  }

  function renderManaBase(report) {
    const warnings = (report.warnings || [])
      .map((w) => `<li class="small"><i class="bi bi-exclamation-triangle text-warning me-1" aria-hidden="true"></i>${escapeHtml(w)}</li>`)
      .join("");
    const categoryRows = Object.entries(report.category_counts || {})
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `
        <tr>
          <td class="text-capitalize">${escapeHtml(name.replace(/_/g, " "))}</td>
          <td class="text-end">${count}</td>
        </tr>`)
      .join("");
    const colorRows = Object.entries(report.color_sources || {})
      .filter(([, count]) => count > 0)
      .map(([letter, count]) => {
        const target = (report.recommended_color_sources || {})[letter];
        const targetText = target ? `<span class="text-muted"> / ${target}</span>` : "";
        return `<tr><td>${escapeHtml(letter)}</td><td class="text-end">${count}${targetText}</td></tr>`;
      })
      .join("");
    return `
      <div class="row g-3">
        <div class="col-md-4">
          <div class="border rounded p-3 h-100">
            <div class="small text-muted text-uppercase fw-semibold">Totals</div>
            <div class="h4 mb-0">${report.total_lands} / ${report.total_cards}</div>
            <div class="small text-muted mb-2">${report.land_percent ? report.land_percent.toFixed(1) : "0"}% lands</div>
            <div class="small">${report.untapped_lands} enter untapped</div>
            <div class="small text-muted">${report.tapped_lands} enter tapped</div>
          </div>
        </div>
        <div class="col-md-4">
          <div class="border rounded p-3 h-100">
            <div class="small text-muted text-uppercase fw-semibold">By category</div>
            <table class="table table-sm mb-0">${categoryRows ? `<tbody>${categoryRows}</tbody>` : '<tbody><tr><td colspan="2" class="text-muted small">No lands found.</td></tr></tbody>'}</table>
          </div>
        </div>
        <div class="col-md-4">
          <div class="border rounded p-3 h-100">
            <div class="small text-muted text-uppercase fw-semibold">Color sources</div>
            <table class="table table-sm mb-0">${colorRows ? `<tbody>${colorRows}</tbody>` : '<tbody><tr><td colspan="2" class="text-muted small">None detected.</td></tr></tbody>'}</table>
          </div>
        </div>
      </div>
      ${warnings ? `<ul class="list-unstyled mt-3 mb-0">${warnings}</ul>` : ""}`;
  }

  function renderBudget(report) {
    const slots = report.suggestions || [];
    if (!slots.length) {
      return `<div class="text-muted small">No cards priced above $${escapeHtml(report.threshold_usd)}. Drop the threshold if you want finer-grained suggestions.</div>`;
    }
    const rows = slots.map((slot) => {
      const alts = (slot.alternatives || [])
        .map((alt) => `
          <li class="small">
            <span class="fw-semibold">${escapeHtml(alt.name)}</span>
            ${alt.price_usd ? `<span class="text-muted">($${escapeHtml(alt.price_usd)})</span>` : ""}
            ${alt.in_user_collection ? '<span class="badge bg-success ms-1">Owned</span>' : ""}
          </li>`)
        .join("");
      return `
        <div class="border rounded p-3 mb-2">
          <div class="d-flex justify-content-between align-items-center">
            <div>
              <div class="fw-semibold">${escapeHtml(slot.name)}</div>
              <div class="small text-muted">${escapeHtml(slot.type_line || "")} · $${escapeHtml(slot.price_usd)}</div>
            </div>
          </div>
          ${alts ? `<ul class="mb-0 mt-2">${alts}</ul>` : '<div class="text-muted small mt-2">No cheaper alternatives found that match this role.</div>'}
        </div>`;
    }).join("");
    return `
      <div class="small text-muted mb-3">
        Threshold: $${escapeHtml(report.threshold_usd)}. Suggestions prioritise cards already in your collection.
      </div>
      ${rows}`;
  }

  function renderWinRate(report) {
    if (!report.games) {
      return `<div class="text-muted small">No game logs yet for this deck. Log a few games and come back.</div>`;
    }
    const seatRows = (report.seat_performance || [])
      .map((row) => `
        <tr>
          <td>Seat ${row.seat_number}</td>
          <td class="text-end">${row.games}</td>
          <td class="text-end">${row.wins}</td>
          <td class="text-end">${row.win_rate !== null ? (row.win_rate * 100).toFixed(0) + "%" : "—"}</td>
        </tr>`)
      .join("");
    const matchupRows = (report.matchups || [])
      .slice(0, 8)
      .map((row) => `
        <tr>
          <td>${escapeHtml(row.opponent_commander)}</td>
          <td class="text-end">${row.games}</td>
          <td class="text-end">${row.wins}</td>
          <td class="text-end">${row.win_rate !== null ? (row.win_rate * 100).toFixed(0) + "%" : "—"}</td>
        </tr>`)
      .join("");
    return `
      <div class="row g-3">
        <div class="col-md-4">
          <div class="border rounded p-3">
            <div class="small text-muted text-uppercase fw-semibold">Overall</div>
            <div class="h3 mb-0">${report.wins} / ${report.games}</div>
            <div class="small text-muted">${report.win_rate !== null ? (report.win_rate * 100).toFixed(0) + "%" : "—"} win rate</div>
            <div class="small mt-2">Last ${report.recent_window_days} days: ${report.recent_wins} / ${report.recent_games}</div>
          </div>
        </div>
        <div class="col-md-4">
          <div class="border rounded p-3 h-100">
            <div class="small text-muted text-uppercase fw-semibold">By seat</div>
            <table class="table table-sm mb-0">
              <thead><tr><th>Seat</th><th class="text-end">G</th><th class="text-end">W</th><th class="text-end">WR</th></tr></thead>
              <tbody>${seatRows || '<tr><td colspan="4" class="text-muted small">No seat data.</td></tr>'}</tbody>
            </table>
          </div>
        </div>
        <div class="col-md-4">
          <div class="border rounded p-3 h-100">
            <div class="small text-muted text-uppercase fw-semibold">Matchups</div>
            <table class="table table-sm mb-0">
              <thead><tr><th>Opponent</th><th class="text-end">G</th><th class="text-end">W</th><th class="text-end">WR</th></tr></thead>
              <tbody>${matchupRows || '<tr><td colspan="4" class="text-muted small">No matchup data.</td></tr>'}</tbody>
            </table>
          </div>
        </div>
      </div>`;
  }

  // ---------------------------------------------------------------------
  // Compare flow
  // ---------------------------------------------------------------------
  function openCompareModal() {
    const modalEl = document.getElementById("deckCompareModal");
    if (!modalEl || !window.bootstrap) return;
    const list = modalEl.querySelector("[data-insights-compare-list]");
    const filter = modalEl.querySelector("[data-insights-compare-filter]");
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();

    if (list.dataset.loaded === "true") return;
    fetchJson(endpoints.folders)
      .then((payload) => {
        const folders = (payload && payload.data) || [];
        const others = folders.filter((f) => String(f.id) !== String(folderId));
        if (!others.length) {
          list.innerHTML = '<div class="text-muted small">No other decks to compare with.</div>';
          return;
        }
        list.innerHTML = "";
        others.forEach((f) => {
          const item = document.createElement("button");
          item.type = "button";
          item.className = "list-group-item list-group-item-action d-flex align-items-center gap-2";
          item.dataset.name = (f.name || "").toLowerCase();
          item.innerHTML = `
            <span class="fw-semibold">${escapeHtml(f.name || "Untitled")}</span>
            <span class="badge bg-secondary ms-auto">${escapeHtml(String(f.counts.total || 0))}</span>
          `;
          item.addEventListener("click", () => openCompareResult(f.id, f.name));
          list.appendChild(item);
        });
        list.dataset.loaded = "true";
      })
      .catch((err) => {
        list.innerHTML = `<div class="alert alert-warning mb-0 small">Couldn't load deck list: ${escapeHtml(err.message || String(err))}</div>`;
      });

    if (filter && !filter.dataset.bound) {
      filter.addEventListener("input", () => {
        const needle = filter.value.trim().toLowerCase();
        list.querySelectorAll(".list-group-item").forEach((row) => {
          row.hidden = needle && !(row.dataset.name || "").includes(needle);
        });
      });
      filter.dataset.bound = "true";
    }
  }

  function openCompareResult(otherId, otherName) {
    const modalEl = document.getElementById("deckCompareModal");
    if (modalEl && window.bootstrap) {
      bootstrap.Modal.getInstance(modalEl)?.hide();
    }
    const resultModalEl = document.getElementById("deckCompareResultModal");
    const body = resultModalEl?.querySelector("[data-insights-compare-result]");
    if (!resultModalEl || !body || !window.bootstrap) return;
    body.innerHTML = "<div class=\"text-muted small\">Loading comparison…</div>";
    const resultModal = bootstrap.Modal.getOrCreateInstance(resultModalEl);
    resultModal.show();

    const url = `${endpoints.compare}?left=${encodeURIComponent(folderId)}&right=${encodeURIComponent(otherId)}`;
    fetchJson(url)
      .then((payload) => {
        const data = payload && payload.data;
        if (!data) {
          body.innerHTML = "<div class=\"text-muted small\">No comparison data.</div>";
          return;
        }
        body.innerHTML = renderCompareReport(data, otherName);
      })
      .catch((err) => {
        body.innerHTML = `<div class="alert alert-warning mb-0 small">Couldn't load comparison: ${escapeHtml(err.message || String(err))}</div>`;
      });
  }

  function renderCompareReport(data, otherName) {
    const { left, right, shared, only_left: onlyLeft, only_right: onlyRight, curve_diff: curve, pip_diff: pip, type_diff: types, summary } = data;
    const cardList = (items, emptyMsg) => {
      if (!items || !items.length) return `<div class="text-muted small">${emptyMsg}</div>`;
      return `
        <ul class="list-unstyled mb-0 small">
          ${items.slice(0, 100).map((c) => {
            const qty = `<span class="text-muted">${c.left_quantity}/${c.right_quantity}</span>`;
            return `<li class="d-flex justify-content-between"><span>${escapeHtml(c.name || "")}</span>${qty}</li>`;
          }).join("")}
        </ul>`;
    };
    const diffRows = (label, diffMap) => {
      const rows = Object.entries(diffMap || {})
        .map(([key, entry]) => `
          <tr>
            <td class="text-uppercase">${escapeHtml(key)}</td>
            <td class="text-end">${entry.left}</td>
            <td class="text-end">${entry.right}</td>
            <td class="text-end ${entry.delta > 0 ? "text-success" : entry.delta < 0 ? "text-danger" : "text-muted"}">${entry.delta > 0 ? "+" : ""}${entry.delta}</td>
          </tr>`).join("");
      return `
        <div class="col-md-4">
          <div class="small text-muted text-uppercase fw-semibold">${escapeHtml(label)}</div>
          <table class="table table-sm mb-0">
            <thead><tr><th></th><th class="text-end">${escapeHtml(left.name || "Left")}</th><th class="text-end">${escapeHtml(right.name || "Right")}</th><th class="text-end">Δ</th></tr></thead>
            <tbody>${rows || '<tr><td colspan="4" class="text-muted small">No differences.</td></tr>'}</tbody>
          </table>
        </div>`;
    };
    return `
      <div class="small text-muted mb-3">Comparing <strong>${escapeHtml(left.name)}</strong> vs <strong>${escapeHtml(right.name || otherName || "")}</strong>.</div>
      <div class="row g-3 mb-3">
        <div class="col-md-4">
          <div class="border rounded p-3 h-100">
            <div class="small text-muted text-uppercase fw-semibold">Shared (${summary.shared})</div>
            ${cardList(shared, "No shared cards.")}
          </div>
        </div>
        <div class="col-md-4">
          <div class="border rounded p-3 h-100">
            <div class="small text-muted text-uppercase fw-semibold">Only in ${escapeHtml(left.name)} (${summary.only_left})</div>
            ${cardList(onlyLeft, "No unique cards on the left.")}
          </div>
        </div>
        <div class="col-md-4">
          <div class="border rounded p-3 h-100">
            <div class="small text-muted text-uppercase fw-semibold">Only in ${escapeHtml(right.name || otherName || "")} (${summary.only_right})</div>
            ${cardList(onlyRight, "No unique cards on the right.")}
          </div>
        </div>
      </div>
      <div class="row g-3">
        ${diffRows("Mana curve", curve)}
        ${diffRows("Color pips", pip)}
        ${diffRows("Card types", types)}
      </div>`;
  }

  // ---------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------
  function fetchJson(url) {
    return fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }).then((resp) => {
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      return resp.json();
    });
  }

  function safeJson(value) {
    try { return JSON.parse(value); } catch (_err) { return {}; }
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>\"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
    }[ch]));
  }
})();
