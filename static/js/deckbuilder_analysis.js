(function () {
  const slugifyRole = (role) => {
    return String(role || "")
      .toLowerCase()
      .replace(/\s+/g, "-")
      .replace(/_+/g, "-");
  };

  async function updateRoleAnalysis(deckId) {
    if (!deckId || !window.fetch) return;
    const tbody = document.getElementById("role-analysis-table");
    if (!tbody) return;

    try {
      const res = await fetch(`/analysis/${deckId}`);
      if (!res.ok) throw new Error(`Request failed: ${res.status}`);
      const data = await res.json();
      const counts = data.counts || {};
      const labels = data.labels || {};
      const entries = Object.entries(counts);

      if (!entries.length) {
        tbody.innerHTML = '<tr><td colspan="2" class="text-muted small">No role data yet.</td></tr>';
        return;
      }

      entries.sort((a, b) => a[0].localeCompare(b[0]));
      const frag = document.createDocumentFragment();

      entries.forEach(([roleKey, count]) => {
        const display = labels[roleKey] || roleKey;
        const slug = slugifyRole(roleKey);
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${display}</td><td><span class="badge role-${slug}">${count}</span></td>`;
        frag.appendChild(tr);
      });

      tbody.innerHTML = "";
      tbody.appendChild(frag);
    } catch (err) {
      console.error("Role analysis fetch failed", err);
      tbody.innerHTML = '<tr><td colspan="2" class="text-danger small">Unable to load role analysis.</td></tr>';
    }
  }

  const binders = { htmx: false };

  function bindRoleAnalysisListeners(deckId) {
    if (!deckId) return;
    const selectors = [
      `form[action$="/build-a-deck/${deckId}/add-card"]`,
      `form[action$="/build-a-deck/${deckId}/queue-add"]`,
      `form[action$="/build-a-deck/${deckId}/bulk-add"]`,
      `form[action$="/build-a-deck/${deckId}/remove-card"]`,
      `form[action$="/build-a-deck/${deckId}/update-card-quantity"]`,
    ];
    const scheduleRefresh = () => setTimeout(() => updateRoleAnalysis(deckId), 100);

    selectors.forEach((selector) => {
      document.querySelectorAll(selector).forEach((form) => {
        if (form.dataset.roleAnalysisBound === "1") return;
        form.dataset.roleAnalysisBound = "1";
        form.addEventListener("submit", scheduleRefresh);
      });
    });

    if (!binders.htmx) {
      document.addEventListener("htmx:afterSwap", () => {
        bindRoleAnalysisListeners(deckId);
        updateRoleAnalysis(deckId);
      });
      binders.htmx = true;
    }
  }

  function initRoleAnalysis(deckId) {
    if (!deckId) return;
    updateRoleAnalysis(deckId);
    bindRoleAnalysisListeners(deckId);
  }

  window.updateRoleAnalysis = updateRoleAnalysis;
  window.initRoleAnalysis = initRoleAnalysis;
})();
