(function () {
  const MAX_LOG_ENTRIES = 5;
  const POLL_INTERVAL_MS = 4000;
  const TRIGGER_STORAGE_KEY = "dv-admin-job-trigger";
  const TRIGGER_TTL_MS = 10 * 60 * 1000;

  function formatBytes(value) {
    const bytes = Number(value);
    if (!bytes || Number.isNaN(bytes)) {
      return null;
    }
    const units = ["B", "KB", "MB", "GB", "TB"];
    let idx = 0;
    let n = bytes;
    while (n >= 1024 && idx < units.length - 1) {
      n /= 1024;
      idx += 1;
    }
    const precision = n >= 10 || idx === 0 ? 0 : 1;
    return `${n.toFixed(precision)} ${units[idx]}`;
  }

  function formatTimestamp(value) {
    if (!value) return new Date().toLocaleTimeString();
    const dt = new Date(value);
    if (Number.isNaN(dt.getTime())) {
      return new Date().toLocaleTimeString();
    }
    return dt.toLocaleTimeString();
  }

  function describeEvent(node, payload) {
    const label = node.dataset.jobLabel || `${payload.scope || ""} ${payload.dataset || ""}`.trim() || "Job";
    const jobId = payload.job_id || payload.jobId || "n/a";
    const type = payload.type || "";

    if (type === "queued") {
      return {
        badgeTone: "info",
        badgeLabel: "Queued",
        summary: `${label} queued`,
        detail: payload.detail || payload.message || `Job ${jobId} is waiting for a worker.`,
      };
    }
    if (type === "started") {
      const detail = payload.detail || payload.message || (
        payload.scope === "scryfall"
          ? `Job ${jobId} is downloading the latest data from Scryfall.`
          : `Job ${jobId} is running.`
      );
      return {
        badgeTone: "warning",
        badgeLabel: "Running",
        summary: `${label} refresh started`,
        detail,
      };
    }
    if (type === "progress") {
      const override = payload.detail || payload.message || payload.progress_text;
      const bytes = formatBytes(payload.bytes);
      const total = formatBytes(payload.total);
      const percent = Number.isFinite(payload.percent) ? `${payload.percent}%` : null;
      const detail = percent && total
        ? `Downloaded ${bytes || "some data"} of ${total} (${percent}).`
        : bytes
          ? `Downloaded ${bytes}.`
          : "Download in progress.";
      return {
        badgeTone: "warning",
        badgeLabel: "Running",
        summary: `${label} refresh in progress`,
        detail: override || detail,
      };
    }
    if (type === "completed") {
      const size = formatBytes(payload.bytes || payload.bytes_downloaded);
      const status = payload.status || payload.download_status || "OK";
      const detail = payload.detail
        || payload.message
        || (size ? `Downloaded ${size} (status ${status}).` : `Finished with status ${status}.`);
      return {
        badgeTone: "success",
        badgeLabel: "Complete",
        summary: `${label} refresh complete`,
        detail,
      };
    }
    if (type === "failed") {
      return {
        badgeTone: "danger",
        badgeLabel: "Failed",
        summary: `${label} refresh failed`,
        detail: payload.detail || payload.message || payload.error || "Check the logs for details.",
      };
    }
    return {
      badgeTone: "secondary",
      badgeLabel: "Info",
      summary: `${label} update`,
      detail: payload.message || "Status updated.",
    };
  }

  function setIdleState(node) {
    const statusEl = node.querySelector("[data-job-status]");
    const detailEl = node.querySelector("[data-job-detail]");
    const badgeEl = node.querySelector("[data-job-badge]");
    const progressWrap = node.querySelector("[data-job-progress]");
    const progressBar = node.querySelector("[data-job-progress-bar]");
    const progressLabel = node.querySelector("[data-job-progress-label]");
    const progressText = node.querySelector("[data-job-progress-text]");
    if (statusEl) statusEl.textContent = "No refresh in progress.";
    if (detailEl) {
      detailEl.textContent = "Queue a refresh to see live status updates as jobs run.";
    }
    if (badgeEl) {
      badgeEl.textContent = "Idle";
      badgeEl.className = "badge text-bg-secondary job-monitor__badge";
    }
    if (progressWrap) progressWrap.classList.add("d-none");
    if (progressBar) {
      progressBar.style.width = "0%";
      progressBar.setAttribute("aria-valuenow", "0");
      progressBar.classList.add("progress-bar-striped", "progress-bar-animated");
    }
    if (progressLabel) progressLabel.textContent = "0%";
    if (progressText) progressText.textContent = "Working";
    const logEl = node.querySelector("[data-job-log]");
    if (logEl) logEl.innerHTML = "";
  }

  function storeTrigger(scope, dataset) {
    if (!scope) return;
    const payload = {
      scope,
      dataset: dataset || null,
      at: Date.now(),
    };
    try {
      sessionStorage.setItem(TRIGGER_STORAGE_KEY, JSON.stringify(payload));
    } catch (err) {
      console.warn("Unable to store job trigger", err);
    }
  }

  function loadTrigger() {
    try {
      const raw = sessionStorage.getItem(TRIGGER_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.scope || !parsed.at) return null;
      if (Date.now() - parsed.at > TRIGGER_TTL_MS) {
        sessionStorage.removeItem(TRIGGER_STORAGE_KEY);
        return null;
      }
      return parsed;
    } catch (err) {
      return null;
    }
  }

  function clearTrigger(scope, dataset) {
    try {
      const current = loadTrigger();
      if (!current) return;
      if (current.scope === scope && (current.dataset || null) === (dataset || null)) {
        sessionStorage.removeItem(TRIGGER_STORAGE_KEY);
      }
    } catch (err) {
      // no-op
    }
  }

  function renderLog(node, events) {
    const logEl = node.querySelector("[data-job-log]");
    if (!logEl) return;
    logEl.innerHTML = "";
    const visible = events.filter((event) => event.type !== "progress");
    const source = visible.length ? visible : events;
    const recent = source.slice(-MAX_LOG_ENTRIES).reverse();
    recent.forEach((event) => {
      const entry = document.createElement("li");
      const meta = describeEvent(node, event);
      const stamp = formatTimestamp(event.recorded_at);
      entry.dataset.jobEntryType = event.type || "info";
      entry.innerHTML = `<span class="text-body">${meta.summary}</span> <span class="text-muted">(${stamp})</span><br><span>${meta.detail}</span>`;
      logEl.appendChild(entry);
    });
  }

  function applyLatestEvent(node, event) {
    const statusEl = node.querySelector("[data-job-status]");
    const detailEl = node.querySelector("[data-job-detail]");
    const badgeEl = node.querySelector("[data-job-badge]");
    const meta = describeEvent(node, event);
    if (statusEl) statusEl.textContent = meta.summary;
    if (detailEl) detailEl.textContent = meta.detail;
    if (badgeEl) {
      badgeEl.textContent = meta.badgeLabel;
      badgeEl.className = `badge text-bg-${meta.badgeTone} job-monitor__badge`;
    }
    updateProgress(node, event);
  }

  function updateProgress(node, event) {
    const wrap = node.querySelector("[data-job-progress]");
    const bar = node.querySelector("[data-job-progress-bar]");
    const label = node.querySelector("[data-job-progress-label]");
    const text = node.querySelector("[data-job-progress-text]");
    if (!wrap || !bar) return;
    if (!event || !event.type) {
      wrap.classList.add("d-none");
      return;
    }

    const type = event.type;
    if (type === "failed") {
      wrap.classList.add("d-none");
      return;
    }

    wrap.classList.remove("d-none");
    const total = Number(event.total || event.bytes_total || 0);
    const bytes = Number(event.bytes || event.bytes_downloaded || 0);
    let percent = Number.isFinite(event.percent) ? Number(event.percent) : null;
    if (percent === null && total > 0) {
      percent = Math.floor((bytes / total) * 100);
    }

    if (type === "completed") {
      bar.style.width = "100%";
      bar.setAttribute("aria-valuenow", "100");
      bar.classList.remove("progress-bar-animated", "progress-bar-striped");
      if (label) label.textContent = "100%";
      if (text) text.textContent = "Completed";
      return;
    }

    bar.classList.add("progress-bar-striped", "progress-bar-animated");
    if (percent !== null && Number.isFinite(percent)) {
      const clamped = Math.max(0, Math.min(100, percent));
      bar.style.width = `${clamped}%`;
      bar.setAttribute("aria-valuenow", String(clamped));
      if (label) label.textContent = `${clamped}%`;
      if (text) {
        const custom = event.progress_text;
        if (custom) {
          text.textContent = custom;
        } else {
          const totalLabel = formatBytes(total);
          const bytesLabel = formatBytes(bytes);
          text.textContent = totalLabel && bytesLabel
            ? `Downloaded ${bytesLabel} of ${totalLabel}`
            : "Downloading";
        }
      }
    } else {
      bar.style.width = type === "queued" ? "25%" : "100%";
      bar.setAttribute("aria-valuenow", "0");
      if (label) label.textContent = type === "queued" ? "Queued" : "Working";
      if (text) {
        if (event.progress_text) {
          text.textContent = event.progress_text;
        } else {
          const bytesLabel = formatBytes(bytes);
          if (bytesLabel) {
            text.textContent = `Downloaded ${bytesLabel}`;
          } else {
            text.textContent = type === "queued" ? "Waiting for worker" : "Working";
          }
        }
      }
    }
  }

  function renderEvents(node, events) {
    if (!events.length) {
      const trigger = loadTrigger();
      if (trigger) {
        const scope = node.dataset.jobScope || "";
        const dataset = node.dataset.jobDataset || null;
        if (trigger.scope === scope && (trigger.dataset || null) === (dataset || null)) {
          applyLatestEvent(node, {
            scope,
            dataset,
            type: "queued",
            job_id: trigger.jobId || "pending",
          });
          return;
        }
      }
      setIdleState(node);
      return;
    }
    applyLatestEvent(node, events[events.length - 1]);
    renderLog(node, events);
    const last = events[events.length - 1];
    if (last && (last.type === "completed" || last.type === "failed")) {
      clearTrigger(node.dataset.jobScope || "", node.dataset.jobDataset || null);
    }
  }

  function startPolling(node) {
    const scope = node.dataset.jobScope;
    const dataset = node.dataset.jobDataset || "";
    if (!scope) return;
    const query = new URLSearchParams({ scope });
    if (dataset) query.set("dataset", dataset);
    const endpoint = `/admin/job-status?${query.toString()}`;

    const poll = () => {
      fetch(endpoint, { headers: { Accept: "application/json" } })
        .then((resp) => {
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          return resp.json();
        })
        .then((data) => {
          const events = Array.isArray(data.events) ? data.events : [];
          renderEvents(node, events);
        })
        .catch((err) => {
          console.warn("Job monitor poll failed", err);
        })
        .finally(() => {
          window.setTimeout(poll, POLL_INTERVAL_MS);
        });
    };

    poll();
  }

  function initJobTriggers(root) {
    const scope = root || document;
    const forms = Array.from(scope.querySelectorAll("[data-job-trigger]"));
    forms.forEach((form) => {
      if (form.dataset.jobTriggerBound === "1") return;
      form.dataset.jobTriggerBound = "1";
      form.addEventListener("submit", () => {
        const jobScope = form.dataset.jobScope || "";
        const dataset = form.dataset.jobDataset || "";
        storeTrigger(jobScope, dataset);
      });
    });
  }

  function initJobMonitor(root) {
    const scope = root || document;
    initJobTriggers(scope);
    const nodes = Array.from(scope.querySelectorAll("[data-job-monitor]"));
    if (!nodes.length) return;
    nodes.forEach((node) => {
      if (node.dataset.jobMonitorBound === "1") return;
      node.dataset.jobMonitorBound = "1";
      setIdleState(node);
      startPolling(node);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initJobMonitor(document), { once: true });
  } else {
    initJobMonitor(document);
  }
  document.addEventListener("htmx:afterSwap", (event) => {
    initJobMonitor(event.target || document);
  });
})();
