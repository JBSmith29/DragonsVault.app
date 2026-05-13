/*
 * card-detail-enhancements.js
 *
 * Two features, both lazy:
 *   1. Inline keyword-ability rule lookups. Reads ``oracle_text`` from a
 *      wrapper element, POSTs to ``/api/rules/keywords``, and renders a
 *      compact list of matches with rule numbers + snippets.
 *   2. Condition editor. A ``<select>`` dropdown that PATCHes the card's
 *      condition via ``/api/card/<id>/condition``.
 *
 * The module is safe to load everywhere: it no-ops when the target elements
 * aren't present.
 */
(function () {
  "use strict";

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

  initOracleKeywords();
  initConditionEditor();

  // ------------------------------------------------------------------
  // 1. Keyword lookup (on-demand, not auto-loaded)
  // ------------------------------------------------------------------
  function initOracleKeywords() {
    const wrapper = document.querySelector("[data-card-oracle]");
    if (!wrapper) return;
    const target = wrapper.querySelector("[data-oracle-keywords]");
    const text = wrapper.getAttribute("data-oracle-text") || "";
    if (!target || !text.trim()) return;

    // Create a "Show Rules" button instead of auto-loading.
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-outline-secondary btn-sm mt-2";
    btn.innerHTML = '<i class="bi bi-journal-text me-1"></i>Show keyword rules';
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Loading…";
      loadKeywordRules(text, target, btn);
    });
    target.appendChild(btn);
  }

  function loadKeywordRules(text, target, btn) {
    fetch("/api/rules/keywords", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
        ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
      },
      body: JSON.stringify({ text }),
    })
      .then((resp) => (resp.ok ? resp.json() : null))
      .then((payload) => {
        const matches = (payload && payload.matches) || [];
        if (!matches.length) {
          target.innerHTML = '<div class="text-muted small">No keyword abilities detected.</div>';
          return;
        }
        target.innerHTML = `
          <div class="small text-muted text-uppercase fw-semibold mb-1">Keyword abilities</div>
          <ul class="list-unstyled mb-0 small">
            ${matches
              .map((m) => `
                <li class="d-flex flex-wrap gap-2 mb-1">
                  <span class="fw-semibold">${escapeHtml(m.keyword)}</span>
                  <span class="text-muted">CR ${escapeHtml(m.rule_number || "?")}</span>
                  ${m.rule_text ? `<span class="flex-grow-1 text-muted">${escapeHtml(m.rule_text)}</span>` : ""}
                </li>
              `)
              .join("")}
          </ul>
        `;
      })
      .catch(() => {
        target.innerHTML = '<div class="text-muted small">Unable to load rules.</div>';
      });
  }

  // ------------------------------------------------------------------
  // 2. Condition editor
  // ------------------------------------------------------------------
  function initConditionEditor() {
    const wrapper = document.querySelector("[data-card-condition]");
    if (!wrapper) return;
    const select = wrapper.querySelector("[data-card-condition-select]");
    const status = wrapper.querySelector("[data-card-condition-status]");
    const endpoint = wrapper.getAttribute("data-endpoint");
    if (!select || !endpoint) return;

    let lastValue = select.value;

    select.addEventListener("change", () => {
      const next = select.value;
      if (next === lastValue) return;
      setStatus("Saving…", "muted");
      select.disabled = true;
      fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
          ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
        },
        body: JSON.stringify({ condition: next || null }),
      })
        .then((resp) => {
          if (!resp.ok) {
            return resp.json().then((body) => {
              throw new Error((body && body.message) || `HTTP ${resp.status}`);
            });
          }
          return resp.json();
        })
        .then(() => {
          lastValue = next;
          setStatus("Saved", "success");
          setTimeout(() => setStatus("", "muted"), 1500);
        })
        .catch((err) => {
          setStatus(err.message || "Save failed", "danger");
          select.value = lastValue;
        })
        .finally(() => {
          select.disabled = false;
        });
    });

    function setStatus(text, kind) {
      if (!status) return;
      status.textContent = text;
      status.className = "small";
      if (kind === "success") status.classList.add("text-success");
      else if (kind === "danger") status.classList.add("text-danger");
      else status.classList.add("text-muted");
    }
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
