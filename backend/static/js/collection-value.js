/*
 * collection-value.js
 *
 * Populates the dashboard's collection-value widget using the new
 * /api/collection/value* endpoints. Renders total value, top cards, a 30-day
 * mini sparkline, and the delta vs the oldest recorded snapshot in the
 * window.
 *
 * The sparkline is an inline SVG path computed client-side so no extra
 * charting library is required.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-collection-value]");
  if (!root) return;

  const endpoints = safeJson(root.getAttribute("data-endpoints") || "{}");
  const elements = {
    subtitle: root.querySelector("[data-cv-subtitle]"),
    total: root.querySelector("[data-cv-total]"),
    counts: root.querySelector("[data-cv-counts]"),
    trend: root.querySelector("[data-cv-trend]"),
    chart: root.querySelector("[data-cv-chart]"),
    noHistory: root.querySelector("[data-cv-no-history]"),
    missing: root.querySelector("[data-cv-missing]"),
    snapshotBtn: root.querySelector("[data-cv-snapshot]"),
    topTable: root.querySelector("[data-cv-top]"),
  };

  elements.snapshotBtn?.addEventListener("click", () => captureSnapshot());

  loadAll();

  function loadAll() {
    Promise.all([
      fetchJson(endpoints.value),
      fetchJson(endpoints.trend + "?days=30"),
      fetchJson(endpoints.history + "?days=30"),
    ])
      .then(([valueResp, trendResp, historyResp]) => {
        renderValue(valueResp?.data || null);
        renderTrend(trendResp?.data || null);
        renderHistory(historyResp?.data || []);
      })
      .catch((err) => {
        if (elements.subtitle) {
          elements.subtitle.textContent = `Couldn't load values: ${err.message || err}`;
        }
      });
  }

  function renderValue(data) {
    if (!data) return;
    if (elements.total) {
      elements.total.textContent = formatMoney(data.total_value, data.currency);
    }
    if (elements.counts) {
      elements.counts.textContent = `${data.unique_cards} unique · ${data.total_cards} total · ${data.priced_cards} priced`;
    }
    if (elements.missing) {
      elements.missing.textContent = data.missing_prices
        ? `${data.missing_prices} card${data.missing_prices === 1 ? "" : "s"} missing a price`
        : "All cards priced";
    }
    if (elements.subtitle) {
      const when = data.captured_at ? new Date(data.captured_at).toLocaleString() : "just now";
      elements.subtitle.textContent = `Live valuation · as of ${when}`;
    }
    renderTopCards(data.top_cards || [], data.currency);
  }

  function renderTrend(data) {
    if (!elements.trend || !data) return;
    const delta = data.delta || {};
    const pct = delta.percent === null || delta.percent === undefined ? null : Number(delta.percent);
    const abs = delta.absolute || "0.00";
    if (pct === null) {
      elements.trend.textContent = `No baseline yet (${data.days}-day window)`;
      return;
    }
    const sign = pct > 0 ? "+" : pct < 0 ? "" : "";
    const cls = pct > 0 ? "text-success" : pct < 0 ? "text-danger" : "text-muted";
    elements.trend.innerHTML = `<span class="${cls} fw-semibold">${sign}${pct.toFixed(1)}%</span> (${abs}) last ${data.days}d`;
  }

  function renderHistory(history) {
    if (!elements.chart) return;
    const rows = history.filter((row) => row && row.total_value !== null);
    if (!rows.length) {
      elements.chart.innerHTML = "";
      return;
    }
    if (elements.noHistory) {
      elements.noHistory.hidden = true;
    }
    const values = rows.map((r) => Number(r.total_value) || 0);
    const min = Math.min.apply(null, values);
    const max = Math.max.apply(null, values);
    const span = max - min || 1;
    const n = values.length;
    const path = values
      .map((v, i) => {
        const x = n === 1 ? 100 : (i / (n - 1)) * 200;
        const y = 60 - ((v - min) / span) * 58 - 1;
        return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
    const fillPath = `${path} L200,60 L0,60 Z`;
    elements.chart.innerHTML = `
      <path d="${fillPath}" fill="rgba(13,110,253,.12)"></path>
      <path d="${path}" fill="none" stroke="#0d6efd" stroke-width="1.5"></path>
    `;
  }

  function renderTopCards(cards, currency) {
    if (!elements.topTable) return;
    const tbody = elements.topTable.querySelector("tbody");
    if (!tbody) return;
    if (!cards || !cards.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="text-muted small">No priced cards yet.</td></tr>';
      return;
    }
    tbody.innerHTML = cards.slice(0, 10).map((card) => `
      <tr>
        <td>
          <div class="fw-semibold">${escapeHtml(card.name || "")}</div>
          <div class="small text-muted">${escapeHtml((card.set_code || "").toUpperCase())} #${escapeHtml(card.collector_number || "")}${card.is_foil ? " · Foil" : ""}</div>
        </td>
        <td class="text-end">${card.quantity}</td>
        <td class="text-end">${formatMoney(card.unit_price, currency)}</td>
        <td class="text-end fw-semibold">${formatMoney(card.total_value, currency)}</td>
      </tr>`).join("");
  }

  function captureSnapshot() {
    const btn = elements.snapshotBtn;
    if (!btn || !endpoints.snapshot) return;
    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Saving…';
    const token = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content")
      || document.cookie.match(/csrf_token=([^;]+)/)?.[1];
    fetch(endpoints.snapshot, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        ...(token ? { "X-CSRFToken": token } : {}),
      },
      body: JSON.stringify({ source: "dashboard" }),
    })
      .then((resp) => {
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
      })
      .then(() => {
        // Reload history and trend so the sparkline reflects the new point.
        return Promise.all([
          fetchJson(endpoints.history + "?days=30"),
          fetchJson(endpoints.trend + "?days=30"),
        ]);
      })
      .then(([historyResp, trendResp]) => {
        renderHistory(historyResp?.data || []);
        renderTrend(trendResp?.data || null);
        btn.innerHTML = '<i class="bi bi-check2 me-1"></i>Saved';
        setTimeout(() => {
          btn.innerHTML = original;
          btn.disabled = false;
        }, 1200);
      })
      .catch((err) => {
        btn.innerHTML = original;
        btn.disabled = false;
        alert(`Couldn't save snapshot: ${err.message || err}`);
      });
  }

  // -----------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------
  function fetchJson(url) {
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } }).then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    });
  }

  function safeJson(value) {
    try { return JSON.parse(value); } catch (_err) { return {}; }
  }

  function formatMoney(value, currency) {
    if (value === null || value === undefined || value === "") return "—";
    const num = Number(value);
    if (!isFinite(num)) return String(value);
    const code = (currency || "usd").toUpperCase();
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(num);
    } catch (_err) {
      return `${num.toFixed(2)} ${code}`;
    }
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>\"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
    }[ch]));
  }
})();
