(() => {
  const RIPPLE_SELECTOR = "[data-ripple]";
  const CONFIRM_SELECTOR = "[data-confirm]";
  const BUILD_SESSION_SELECTOR = ".build-session[data-build-session-id]";
  const BUILD_DETAILS_SELECTOR = ".build-rec-section[id]";
  const BUILD_DETAILS_SUFFIX = ":details";
  const BUILD_SCROLL_SUFFIX = ":scroll";

  const buildLocalStore = (() => {
    try {
      return window.localStorage;
    } catch (err) {
      return null;
    }
  })();
  const buildSessionStore = (() => {
    try {
      return window.sessionStorage;
    } catch (err) {
      return null;
    }
  })();
  const buildDetailsStore = buildLocalStore || buildSessionStore;
  const buildDetailsFallback = buildDetailsStore
    && buildSessionStore
    && buildDetailsStore !== buildSessionStore
    ? buildSessionStore
    : null;

  let activeBuildRoot = null;
  let activeBuildSessionId = null;
  let buildDetailsKey = null;
  let buildScrollKey = null;
  let buildListenersBound = false;
  let scrollRestoreScheduled = false;
  let scrollRestored = false;

  function createRipple(evt) {
    const target = evt.currentTarget;
    if (!target) return;

    const rect = target.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const ripple = document.createElement("span");
    ripple.className = "ripple";
    ripple.style.width = `${size}px`;
    ripple.style.height = `${size}px`;

    let x = evt.clientX - rect.left - size / 2;
    let y = evt.clientY - rect.top - size / 2;
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      x = rect.width / 2 - size / 2;
      y = rect.height / 2 - size / 2;
    }

    ripple.style.left = `${x}px`;
    ripple.style.top = `${y}px`;

    const previous = target.querySelector(".ripple");
    if (previous) previous.remove();

    target.appendChild(ripple);
    ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
  }

  function bindRipple(el) {
    if (!el || el.dataset.rippleBound) return;
    el.addEventListener("click", createRipple);
    el.dataset.rippleBound = "1";
  }

  function resolveBuildSessionRoot(scope) {
    if (scope instanceof Element) {
      if (scope.matches(BUILD_SESSION_SELECTOR)) return scope;
      return scope.querySelector(BUILD_SESSION_SELECTOR);
    }
    if (scope === document) {
      return document.querySelector(BUILD_SESSION_SELECTOR);
    }
    return null;
  }

  function setActiveBuildSession(root) {
    const sessionId = root ? root.dataset.buildSessionId : null;
    activeBuildRoot = root || null;
    activeBuildSessionId = sessionId || null;
    buildDetailsKey = activeBuildSessionId
      ? `build-session:${activeBuildSessionId}${BUILD_DETAILS_SUFFIX}`
      : null;
    buildScrollKey = activeBuildSessionId
      ? `build-session:${activeBuildSessionId}${BUILD_SCROLL_SUFFIX}`
      : null;
    scrollRestoreScheduled = false;
    scrollRestored = false;
  }

  function getBuildDetails() {
    if (!activeBuildRoot) return [];
    return Array.from(activeBuildRoot.querySelectorAll(BUILD_DETAILS_SELECTOR));
  }

  function markBuildDetailsRestored() {
    document.documentElement.dataset.buildDetailsRestored = "1";
    document.dispatchEvent(new CustomEvent("build-details-restored"));
  }

  function saveBuildDetailsState() {
    if (!buildDetailsStore || !buildDetailsKey) return;
    const details = getBuildDetails();
    if (!details.length) return;
    const state = {};
    details.forEach((item) => {
      state[item.id] = Boolean(item.open);
    });
    try {
      buildDetailsStore.setItem(buildDetailsKey, JSON.stringify(state));
    } catch (err) {
      /* ignore */
    }
  }

  function restoreBuildDetailsState() {
    if (!buildDetailsStore || !buildDetailsKey) return;
    const details = getBuildDetails();
    if (!details.length) return;
    let state = {};
    try {
      let raw = buildDetailsStore.getItem(buildDetailsKey);
      if (!raw && buildDetailsFallback) {
        raw = buildDetailsFallback.getItem(buildDetailsKey);
        if (raw) {
          try {
            buildDetailsStore.setItem(buildDetailsKey, raw);
          } catch (err) {
            /* ignore */
          }
        }
      }
      state = JSON.parse(raw || "{}");
    } catch (err) {
      state = {};
    }
    details.forEach((item) => {
      if (Object.prototype.hasOwnProperty.call(state, item.id)) {
        item.open = Boolean(state[item.id]);
      }
    });
  }

  function bindBuildDetails(details) {
    details.forEach((item) => {
      if (item.dataset.buildDetailsBound === "1") return;
      item.dataset.buildDetailsBound = "1";
      item.addEventListener("toggle", saveBuildDetailsState);
    });
  }

  function buildScrollElement() {
    return document.getElementById("main")
      || document.scrollingElement
      || document.documentElement;
  }

  function saveBuildScroll() {
    if (!buildSessionStore || !buildScrollKey) return;
    const scrollEl = buildScrollElement();
    if (!scrollEl) return;
    try {
      buildSessionStore.setItem(buildScrollKey, String(scrollEl.scrollTop || 0));
    } catch (err) {
      /* ignore */
    }
  }

  function restoreBuildScroll() {
    if (!buildSessionStore || !buildScrollKey) return;
    const scrollEl = buildScrollElement();
    if (!scrollEl) return;
    let raw = null;
    try {
      raw = buildSessionStore.getItem(buildScrollKey);
    } catch (err) {
      return;
    }
    if (!raw) return;
    const value = parseInt(raw, 10);
    if (!Number.isFinite(value)) return;
    scrollEl.scrollTop = value;
  }

  function scheduleBuildScrollRestore() {
    if (!buildSessionStore || !buildScrollKey) return;
    if (scrollRestoreScheduled || scrollRestored) return;
    scrollRestoreScheduled = true;
    const run = () => {
      scrollRestoreScheduled = false;
      if (scrollRestored) return;
      restoreBuildScroll();
      scrollRestored = true;
    };
    if (document.readyState === "complete") {
      window.requestAnimationFrame(run);
    } else {
      window.addEventListener("load", () => window.requestAnimationFrame(run), { once: true });
    }
  }

  function bindBuildSessionListeners() {
    if (buildListenersBound) return;
    const saveAll = () => {
      if (!activeBuildSessionId) return;
      saveBuildDetailsState();
      saveBuildScroll();
    };
    document.addEventListener("submit", saveAll, true);
    window.addEventListener("pagehide", saveAll);
    window.addEventListener("beforeunload", saveAll);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        saveAll();
      }
    });
    document.addEventListener("htmx:beforeRequest", saveAll);
    buildListenersBound = true;
  }

  function initBuildSession(scope) {
    const root = resolveBuildSessionRoot(scope);
    if (!root) {
      if (!document.querySelector(BUILD_SESSION_SELECTOR)) {
        activeBuildRoot = null;
        activeBuildSessionId = null;
        buildDetailsKey = null;
        buildScrollKey = null;
        delete document.documentElement.dataset.buildDetailsRestored;
      }
      return;
    }
    setActiveBuildSession(root);
    if (!buildDetailsStore) {
      markBuildDetailsRestored();
      document.dispatchEvent(new CustomEvent("build-nav-refresh"));
      return;
    }
    const details = getBuildDetails();
    if (details.length) {
      bindBuildDetails(details);
    }
    restoreBuildDetailsState();
    markBuildDetailsRestored();
    document.dispatchEvent(new CustomEvent("build-nav-refresh"));
    scheduleBuildScrollRestore();
    bindBuildSessionListeners();
  }

  function init(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll(RIPPLE_SELECTOR).forEach(bindRipple);
    initBuildSession(scope);
  }

  function applySubmitFeedback(form, submitter) {
    if (!form) return;
    if (!form.hasAttribute("data-ux-submit") && !form.hasAttribute("data-job-trigger")) {
      if (!submitter || !submitter.hasAttribute("data-ux-submit")) return;
    }
    const btn = submitter || form.querySelector('button[type="submit"], input[type="submit"]');
    if (!btn || btn.disabled) return;
    if (btn.dataset.uxFeedbackApplied === "1") return;
    const submitName = btn.getAttribute("name");
    if (submitName) {
      const hasField = Array.from(form.elements).some((el) => {
        if (!(el instanceof HTMLInputElement)) return false;
        if (el === btn) return false;
        if (el.name !== submitName) return false;
        const type = (el.type || "").toLowerCase();
        return type !== "submit" && type !== "button" && type !== "image";
      });
      if (!hasField) {
        let mirror = form.querySelector(`input[data-ux-submit-mirror="1"][name="${submitName}"]`);
        if (!mirror) {
          mirror = document.createElement("input");
          mirror.type = "hidden";
          mirror.name = submitName;
          mirror.dataset.uxSubmitMirror = "1";
          form.appendChild(mirror);
        }
        mirror.value = btn.getAttribute("value") || "";
      }
    }
    const label = btn.dataset.progressLabel || "Working...";
    btn.dataset.uxFeedbackApplied = "1";
    if (!btn.dataset.uxOriginalLabel) {
      btn.dataset.uxOriginalLabel = btn.tagName === "INPUT" ? btn.value : btn.innerHTML;
    }
    btn.disabled = true;
    if (btn.tagName === "INPUT") {
      btn.value = label;
    } else {
      btn.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${label}`;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init(document));
  } else {
    init(document);
  }

  document.addEventListener("htmx:afterSwap", (evt) => {
    if (!evt || !evt.target) return;
    init(evt.target);
  });

  document.addEventListener("submit", (evt) => {
    const form = evt.target;
    if (!(form instanceof HTMLFormElement)) return;
    const submitter = evt.submitter instanceof HTMLElement ? evt.submitter : null;
    let confirmMsg = (submitter && submitter.getAttribute("data-confirm"))
      || form.getAttribute("data-confirm");
    if (submitter && submitter.dataset.confirmed === "1") {
      submitter.dataset.confirmed = "";
      confirmMsg = null;
    }
    if (confirmMsg) {
      const ok = window.confirm(confirmMsg);
      if (!ok) {
        evt.preventDefault();
        return;
      }
    }
    applySubmitFeedback(form, submitter);
  });

  document.addEventListener("click", (evt) => {
    const target = evt.target instanceof Element ? evt.target.closest(CONFIRM_SELECTOR) : null;
    if (!target) return;
    const confirmMsg = target.getAttribute("data-confirm");
    if (!confirmMsg) return;
    if (target.closest("form")) {
      const ok = window.confirm(confirmMsg);
      if (!ok) {
        evt.preventDefault();
        evt.stopImmediatePropagation();
        return;
      }
      target.dataset.confirmed = "1";
      return;
    }
    const ok = window.confirm(confirmMsg);
    if (!ok) {
      evt.preventDefault();
      evt.stopImmediatePropagation();
    }
  });
})();
