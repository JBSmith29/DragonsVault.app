(function () {
  function applyConfig() {
    if (!window.htmx || !window.htmx.config) return false;
    const nonce = document.querySelector('meta[name="csp-nonce"]')?.content || '';
    window.htmx.config.allowScriptTags = true;
    if (nonce) {
      window.htmx.config.inlineScriptNonce = nonce;
    }
    return true;
  }

  if (!applyConfig()) {
    // If HTMX hasn't loaded yet, retry once DOM is ready.
    document.addEventListener('DOMContentLoaded', applyConfig, { once: true });
  }
})();
