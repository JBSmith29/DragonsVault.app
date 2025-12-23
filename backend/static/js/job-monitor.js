(function () {
  const MAX_LOG_ENTRIES = 5;
  const POLL_INTERVAL_MS = 4000;

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
        detail: `Job ${jobId} is waiting for a worker.`,
      };
    }
    if (type === "started") {
      return {
        badgeTone: "warning",
        badgeLabel: "Running",
        summary: `${label} refresh started`,
        detail: `Job ${jobId} is downloading the latest data from Scryfall.`,
      };
    }
    if (type === "completed") {
      const size = formatBytes(payload.bytes || payload.bytes_downloaded);
      const status = payload.status || payload.download_status || "OK";
      const detail = size ? `Downloaded ${size} (status ${status}).` : `Finished with status ${status}.`;
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
        detail: payload.error || "Check the logs for details.",
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
    if (statusEl) statusEl.textContent = "No refresh in progress.";
    if (detailEl) {
      detailEl.textContent = "Queue a refresh to see live status updates as jobs run.";
    }
    if (badgeEl) {
      badgeEl.textContent = "Idle";
      badgeEl.className = "badge text-bg-secondary job-monitor__badge";
    }
    const logEl = node.querySelector("[data-job-log]");
    if (logEl) logEl.innerHTML = "";
  }

  function renderLog(node, events) {
    const logEl = node.querySelector("[data-job-log]");
    if (!logEl) return;
    logEl.innerHTML = "";
    const recent = events.slice(-MAX_LOG_ENTRIES).reverse();
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
  }

  function renderEvents(node, events) {
    if (!events.length) {
      setIdleState(node);
      return;
    }
    applyLatestEvent(node, events[events.length - 1]);
    renderLog(node, events);
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

  function initJobMonitor() {
    const nodes = Array.from(document.querySelectorAll("[data-job-monitor]"));
    if (!nodes.length) return;
    nodes.forEach((node) => {
      setIdleState(node);
      startPolling(node);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initJobMonitor, { once: true });
  } else {
    initJobMonitor();
  }
})();
