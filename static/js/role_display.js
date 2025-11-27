// Enhance role cards with hover details and progress bars
(function () {
  // Hover-to-preview helper for elements with data-hover-src
  const attachHoverPreview = () => {
    document.querySelectorAll('[data-hover-src]').forEach((el) => {
      const src = el.getAttribute('data-hover-src');
      if (!src) return;
      el.addEventListener('mouseenter', () => {
        const preview = document.createElement('div');
        preview.className = 'hover-preview';
        preview.innerHTML = `<img src="${src}" alt="" referrerpolicy="no-referrer">`;
        document.body.appendChild(preview);
        const rect = el.getBoundingClientRect();
        preview.style.left = `${rect.right + 8 + window.scrollX}px`;
        preview.style.top = `${rect.top + window.scrollY}px`;
        el._hoverPreview = preview;
      });
      el.addEventListener('mouseleave', () => {
        if (el._hoverPreview) {
          el._hoverPreview.remove();
          el._hoverPreview = null;
        }
      });
    });
  };

  const roleContainers = document.querySelectorAll('[data-role-card]');
  roleContainers.forEach((card) => {
    const detail = card.querySelector('[data-role-detail]');
    const toggle = card.querySelector('[data-role-toggle]');
    const progress = card.querySelector('[data-role-progress]');
    const target = parseFloat(card.dataset.roleTarget || '0') || 0;
    const current = parseFloat(card.dataset.roleCurrent || '0') || 0;
    const pct = target > 0 ? Math.min(100, Math.round((current / target) * 100)) : 100;
    if (progress) {
      progress.style.width = `${pct}%`;
    }
    const showDetail = () => {
      if (detail) detail.classList.toggle('d-none');
    };
    if (toggle) {
      toggle.addEventListener('click', showDetail);
      toggle.addEventListener('keydown', (evt) => {
        if (evt.key === 'Enter' || evt.key === ' ') {
          evt.preventDefault();
          showDetail();
        }
      });
    }
  });

  attachHoverPreview();
})();
