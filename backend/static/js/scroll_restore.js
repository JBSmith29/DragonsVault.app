(function () {
  const STORAGE_PREFIX = "scroll:";
  const MAIN_ID = "main";

  const getPath = () => window.location.pathname || "/";
  const getKey = () => `${STORAGE_PREFIX}${getPath()}`;

  const safeGet = (key) => {
    try {
      return sessionStorage.getItem(key);
    } catch (_) {
      return null;
    }
  };

  const safeSet = (key, value) => {
    try {
      sessionStorage.setItem(key, value);
    } catch (_) {
      /* ignore */
    }
  };

  const safeRemove = (key) => {
    try {
      sessionStorage.removeItem(key);
    } catch (_) {
      /* ignore */
    }
  };

  const getScrollTarget = () => document.getElementById(MAIN_ID);

  const getScrollTop = () => {
    const main = getScrollTarget();
    if (main) return main.scrollTop || 0;
    return window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;
  };

  const setScrollTop = (y) => {
    const main = getScrollTarget();
    if (main) {
      main.scrollTop = y;
    }
    window.scrollTo(0, y);
  };

  const hasNoRestore = (el) => {
    if (!el || !(el instanceof Element)) return false;
    return Boolean(el.closest("[data-no-scroll-restore]"));
  };

  const shouldIgnoreClick = (el) => {
    if (!el || !(el instanceof Element)) return true;
    if (hasNoRestore(el)) return true;
    if (el.tagName === "A") {
      const target = el.getAttribute("target");
      if (target && target.toLowerCase() === "_blank") return true;
    }
    return false;
  };

  const saveScrollPosition = () => {
    const y = getScrollTop();
    safeSet(getKey(), String(y));
  };

  const restoreScrollPosition = () => {
    const key = getKey();
    const raw = safeGet(key);
    if (!raw) return;
    const y = parseInt(raw, 10);
    if (Number.isNaN(y)) {
      safeRemove(key);
      return;
    }
    requestAnimationFrame(() => {
      setScrollTop(y);
      safeRemove(key);
    });
  };

  document.addEventListener(
    "click",
    (event) => {
      const target = event.target instanceof Element ? event.target.closest("a, button") : null;
      if (!target || shouldIgnoreClick(target)) return;
      saveScrollPosition();
    },
    true,
  );

  document.addEventListener(
    "submit",
    (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (hasNoRestore(form)) return;
      saveScrollPosition();
    },
    true,
  );

  document.addEventListener("DOMContentLoaded", () => {
    restoreScrollPosition();
  });

  if (window.htmx) {
    document.addEventListener("htmx:beforeRequest", saveScrollPosition);
    document.addEventListener("htmx:beforeSwap", saveScrollPosition);
    document.addEventListener("htmx:afterSwap", (event) => {
      const target = event && event.target;
      if (!(target instanceof Element)) return;
      if (target.id !== MAIN_ID) return;
      restoreScrollPosition();
    });
  }
})();
