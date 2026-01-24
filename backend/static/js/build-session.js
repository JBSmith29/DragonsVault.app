(function () {
  const initFilters = (root) => {
    const tableBody = root.querySelector("#buildSessionTable");
    if (!tableBody || tableBody.dataset.buildFiltersReady === "true") {
      return;
    }
    tableBody.dataset.buildFiltersReady = "true";

    let rows = Array.from(tableBody.querySelectorAll(".build-row"));
    const typePills = Array.from(root.querySelectorAll(".type-pill"));
    const cmcButtons = Array.from(root.querySelectorAll(".curve-bar-btn"));
    const typeHint = root.querySelector("#typeFilterHint");
    const typeHintLabel = typeHint ? typeHint.querySelector("[data-type-name]") : null;
    const typeClearBtn = root.querySelector("#typeFilterClear");
    const cmcHint = root.querySelector("#cmcFilterHint");
    const cmcHintLabel = cmcHint ? cmcHint.querySelector("[data-cmc-label]") : null;
    const cmcClearBtn = root.querySelector("#cmcFilterClear");
    let activeType = "";
    let activeCmc = "";

    function applyFilters() {
      rows = Array.from(tableBody.querySelectorAll(".build-row")).filter(
        (row) => row.isConnected,
      );
      rows.forEach((row) => {
        const types = (row.getAttribute("data-type") || "").toLowerCase();
        const cmcBucket = row.getAttribute("data-cmc-bucket") || "";
        const typeMatch = !activeType || types.includes(activeType);
        const cmcMatch = !activeCmc || cmcBucket === activeCmc;
        row.classList.toggle("d-none", !(typeMatch && cmcMatch));
      });
    }

    function updateTypeUI() {
      typePills.forEach((btn) => {
        const value = (btn.dataset.type || "").toLowerCase();
        const isActive = activeType && activeType === value;
        btn.classList.toggle("active", isActive);
        btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      });
      if (typeHint) {
        const show = !!activeType;
        typeHint.classList.toggle("d-none", !show);
        if (show && typeHintLabel) {
          const pill = typePills.find(
            (btn) => (btn.dataset.type || "").toLowerCase() === activeType,
          );
          typeHintLabel.textContent = pill
            ? pill.dataset.typeLabel || pill.textContent || activeType
            : activeType;
        }
      }
      applyFilters();
    }

    function updateCmcUI() {
      cmcButtons.forEach((btn) => {
        const value = btn.dataset.cmcBucket || "";
        const isActive = activeCmc && activeCmc === value;
        btn.classList.toggle("active", isActive);
        btn.setAttribute("aria-pressed", isActive ? "true" : "false");
      });
      if (cmcHint) {
        const show = !!activeCmc;
        cmcHint.classList.toggle("d-none", !show);
        if (show && cmcHintLabel) {
          const btn = cmcButtons.find(
            (el) => (el.dataset.cmcBucket || "") === activeCmc,
          );
          cmcHintLabel.textContent = btn
            ? btn.dataset.cmcLabel || btn.dataset.cmcBucket || activeCmc
            : activeCmc;
        }
      }
      applyFilters();
    }

    if (typePills.length) {
      typePills.forEach((btn) => {
        btn.addEventListener("click", () => {
          const value = (btn.dataset.type || "").toLowerCase();
          activeType = activeType === value ? "" : value;
          updateTypeUI();
        });
      });
    }
    if (typeClearBtn) {
      typeClearBtn.addEventListener("click", () => {
        activeType = "";
        updateTypeUI();
      });
    }

    if (cmcButtons.length) {
      cmcButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
          const value = btn.dataset.cmcBucket || "";
          activeCmc = activeCmc === value ? "" : value;
          updateCmcUI();
        });
      });
    }
    if (cmcClearBtn) {
      cmcClearBtn.addEventListener("click", () => {
        activeCmc = "";
        updateCmcUI();
      });
    }

    applyFilters();
  };

  const initEdhrecProgress = (root) => {
    const form = root.querySelector("#buildEdhrecForm");
    if (!form || form.dataset.buildEdhrecReady === "true") {
      return;
    }
    form.dataset.buildEdhrecReady = "true";

    const progressWrap = root.querySelector("#buildEdhrecProgress");
    const progressBar = progressWrap ? progressWrap.querySelector(".progress-bar") : null;
    const etaLabel = root.querySelector("#buildEdhrecEta");
    if (!progressWrap || !progressBar) {
      return;
    }

    const statusEndpoint = (form.dataset.statusEndpoint || "").trim();
    const storageKey = `dv-build-edhrec:${form.dataset.sessionId || "0"}`;
    let activeJobId = (form.dataset.jobId || "").trim();
    if (!activeJobId && window.sessionStorage) {
      activeJobId = sessionStorage.getItem(storageKey) || "";
    }
    if (activeJobId && window.sessionStorage) {
      sessionStorage.setItem(storageKey, activeJobId);
    }

    let timerId = null;
    let pollTimer = null;
    let startTime = 0;
    const estimatedSeconds = parseInt(form.dataset.estimateSeconds || "20", 10);
    const durationMs = Math.max(5, estimatedSeconds) * 1000;

    function clearTimers() {
      if (timerId) {
        window.clearInterval(timerId);
        timerId = null;
      }
      if (pollTimer) {
        window.clearTimeout(pollTimer);
        pollTimer = null;
      }
    }

    function cleanUrl() {
      try {
        const url = new URL(window.location.href);
        url.searchParams.delete("edhrec_job_id");
        window.history.replaceState({}, "", url.toString());
        return url.toString();
      } catch (_err) {
        return window.location.href;
      }
    }

    function startProgress(label) {
      if (!progressWrap.classList.contains("is-active")) {
        progressWrap.classList.remove("d-none");
        progressWrap.classList.add("is-active");
        progressBar.style.width = "5%";
        progressBar.setAttribute("aria-valuenow", "5");
      }
      if (etaLabel) {
        etaLabel.textContent = label || `Estimated time: ~${estimatedSeconds}s`;
      }
      startTime = Date.now();
      if (!timerId) {
        timerId = window.setInterval(() => {
          if (!form.isConnected) {
            clearTimers();
            return;
          }
          const elapsed = Date.now() - startTime;
          const ratio = Math.min(elapsed / durationMs, 1);
          const pct = Math.min(95, Math.round(5 + ratio * 90));
          progressBar.style.width = `${pct}%`;
          progressBar.setAttribute("aria-valuenow", String(pct));
          if (etaLabel) {
            const remaining = Math.max(1, Math.ceil((durationMs - elapsed) / 1000));
            etaLabel.textContent =
              elapsed >= durationMs
                ? "Finalizing..."
                : `Estimated time: ~${remaining}s`;
          }
        }, 500);
      }
    }

    function finishProgress(message, reload) {
      clearTimers();
      progressWrap.classList.remove("d-none");
      progressWrap.classList.add("is-active");
      progressBar.style.width = "100%";
      progressBar.setAttribute("aria-valuenow", "100");
      if (etaLabel) {
        etaLabel.textContent = message || "EDHREC refresh completed.";
      }
      if (window.sessionStorage) {
        sessionStorage.removeItem(storageKey);
      }
      activeJobId = "";
      if (reload) {
        const target = cleanUrl();
        window.setTimeout(() => {
          window.location.assign(target);
        }, 600);
      } else {
        cleanUrl();
      }
    }

    function applyEvent(event) {
      if (!event) return false;
      const type = event.type || "";
      if (type === "queued") {
        startProgress("Queued...");
        return false;
      }
      if (type === "started") {
        startProgress("Loading EDHREC data...");
        return false;
      }
      if (type === "completed") {
        finishProgress(event.message || "EDHREC refresh completed.", true);
        return true;
      }
      if (type === "failed") {
        finishProgress(event.error || "EDHREC refresh failed.", false);
        return true;
      }
      return false;
    }

    function pollStatus() {
      if (!activeJobId || !statusEndpoint) return;
      if (!form.isConnected) {
        clearTimers();
        return;
      }
      const url = `${statusEndpoint}?job_id=${encodeURIComponent(activeJobId)}`;
      fetch(url, { headers: { Accept: "application/json" } })
        .then((resp) => {
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          return resp.json();
        })
        .then((data) => {
          const events = Array.isArray(data.events) ? data.events : [];
          if (!events.length) {
            pollTimer = window.setTimeout(pollStatus, 1500);
            return;
          }
          const latest = events[events.length - 1];
          const done = applyEvent(latest);
          if (!done) {
            pollTimer = window.setTimeout(pollStatus, 1500);
          }
        })
        .catch(() => {
          pollTimer = window.setTimeout(pollStatus, 2500);
        });
    }

    if (activeJobId) {
      startProgress("Queued...");
      pollStatus();
    }
  };

  const initBuildNav = (root) => {
    const dropdownMenu = root.querySelector("[data-build-nav-links]");
    const toggleBtn = root.querySelector(".build-nav-dropdown .build-nav-toggle");
    if (!dropdownMenu || !toggleBtn) return;

    function collectSections() {
      const items = [];
      const allowedLabels = new Set(["commander", "recommendations", "current build"]);
      root.querySelectorAll("[data-nav-section]").forEach((section) => {
        if (section.classList.contains("d-none")) return;
        if (section.closest(".d-none")) return;
        if (section.closest(".build-rec-source.d-none")) return;
        const label = (section.getAttribute("data-nav-label") || "").trim();
        const level = (section.getAttribute("data-nav-level") || "1").trim();
        const id = section.getAttribute("id");
        if (!label || !id) return;
        const isTop = level === "1";
        if (isTop && !allowedLabels.has(label.toLowerCase())) return;
        items.push({ id, label, level });
      });
      return items;
    }

    function renderLinks() {
      const dropdown =
        typeof bootstrap !== "undefined" && bootstrap.Dropdown
          ? bootstrap.Dropdown.getOrCreateInstance(toggleBtn)
          : null;
      dropdownMenu.innerHTML = "";
      const items = collectSections();
      if (!items.length) {
        const empty = document.createElement("span");
        empty.className = "dropdown-item text-muted small";
        empty.textContent = "No sections available";
        dropdownMenu.appendChild(empty);
        return;
      }
      items.forEach((item) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "dropdown-item build-nav-link";
        btn.textContent = item.label;
        btn.dataset.level = item.level;
        btn.addEventListener("click", () => {
          const target = document.getElementById(item.id);
          if (target) {
            target.scrollIntoView({ behavior: "smooth", block: "start" });
          }
          if (dropdown) {
            dropdown.hide();
          }
        });
        dropdownMenu.appendChild(btn);
      });
    }

    if (toggleBtn.dataset.navBound !== "1") {
      toggleBtn.dataset.navBound = "1";
      toggleBtn.addEventListener("click", renderLinks);
      toggleBtn.addEventListener("show.bs.dropdown", renderLinks);
    }
    renderLinks();
  };

  const scan = (scope) => {
    const root = scope instanceof Element ? scope : document;
    initFilters(root);
    initEdhrecProgress(root);
    initBuildNav(root);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => scan(document));
  } else {
    scan(document);
  }

  document.addEventListener("htmx:afterSwap", (event) => {
    scan(event.target || document);
  });

  document.addEventListener("build-nav-refresh", () => {
    scan(document);
  });
})();
