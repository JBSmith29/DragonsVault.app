// Simple scroll preservation utility
(function () {
  const KEY = 'dv:scroll-preserve';

  const saveScroll = () => {
    try {
      sessionStorage.setItem(KEY, String(window.scrollY));
    } catch (err) {
      /* ignore */
    }
  };

  const restoreScroll = () => {
    try {
      const val = sessionStorage.getItem(KEY);
      if (!val) return;
      const y = parseInt(val, 10);
      if (!Number.isNaN(y)) {
        window.scrollTo(0, y);
      }
      sessionStorage.removeItem(KEY);
    } catch (err) {
      /* ignore */
    }
  };

  document.addEventListener('DOMContentLoaded', restoreScroll);
  window.addEventListener('beforeunload', saveScroll);

  // Preserve on forms that mutate content
  document.addEventListener('submit', (evt) => {
    const form = evt.target;
    if (form && form.matches('form[data-preserve-scroll], form[data-build-add], form[data-scroll-preserve]')) {
      saveScroll();
    }
  });
})();
