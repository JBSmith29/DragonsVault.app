(function () {
  function currentNonce() {
    return document.querySelector('meta[name="csp-nonce"]')?.content || '';
  }

  function ensureMainStyleNonces(root) {
    const nonce = currentNonce();
    if (!nonce || !root || !root.querySelectorAll) return;

    root.querySelectorAll('style').forEach((styleEl) => {
      if (!(styleEl instanceof HTMLStyleElement)) return;
      if (styleEl.nonce === nonce && styleEl.sheet) return;

      const replacement = document.createElement('style');
      Array.from(styleEl.attributes || []).forEach((attr) => {
        if (!attr || String(attr.name).toLowerCase() === 'nonce') return;
        replacement.setAttribute(attr.name, attr.value);
      });
      replacement.nonce = nonce;
      replacement.textContent = styleEl.textContent || '';
      styleEl.replaceWith(replacement);
    });
  }

  function applyConfig() {
    if (!window.htmx || !window.htmx.config) return false;

    const nonce = currentNonce();
    window.htmx.config.allowScriptTags = true;

    // Nonces on inline style/script tags do not round-trip through HTML
    // serialization reliably, so HTMX history snapshots can restore markup with
    // invalid (empty) nonce values. Disable snapshot caching to prevent
    // back/forward rendering regressions.
    window.htmx.config.historyCacheSize = 0;

    if (nonce) {
      window.htmx.config.inlineScriptNonce = nonce;
    }

    document.addEventListener('htmx:configRequest', function (event) {
      const activeNonce = currentNonce();
      if (!activeNonce || !event?.detail?.headers) return;
      event.detail.headers['X-CSP-Nonce'] = activeNonce;
    });

    document.addEventListener('htmx:afterSwap', function (event) {
      const target = event?.target;
      if (!(target instanceof Element) || target.id !== 'main') return;
      ensureMainStyleNonces(target);
    });

    window.addEventListener('popstate', function () {
      const main = document.getElementById('main');
      if (!(main instanceof Element)) return;
      window.setTimeout(function () {
        ensureMainStyleNonces(main);
      }, 0);
    });

    return true;
  }

  if (!applyConfig()) {
    // If HTMX hasn't loaded yet, retry once DOM is ready.
    document.addEventListener('DOMContentLoaded', applyConfig, { once: true });
  }
})();
