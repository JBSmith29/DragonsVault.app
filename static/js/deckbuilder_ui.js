// Deckbuilder UI helpers: floating search, quick-add focus, hover previews
(function () {
  const filterInput = document.querySelector('[data-deck-filter-input]');
  const floatingSearch = document.getElementById('builderFloatingSearch');
  if (filterInput && floatingSearch) {
    floatingSearch.addEventListener('input', (evt) => {
      filterInput.value = evt.target.value;
      filterInput.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  // Hover preview: reuse data-hover-src
  const preview = document.querySelector('.card-hover-preview');
  if (preview) {
    document.addEventListener('mouseover', (evt) => {
      const target = evt.target;
      const hoverSrc = target?.dataset?.hoverSrc || target?.closest('[data-hover-src]')?.dataset?.hoverSrc;
      if (!hoverSrc) return;
      preview.src = hoverSrc;
      preview.classList.add('is-visible');
    });
    document.addEventListener('mouseout', (evt) => {
      const related = evt.relatedTarget;
      if (!related || !preview.contains(related)) {
        preview.classList.remove('is-visible');
      }
    });
  }

  // Shared view toggle for EDHREC / Upgrade Plan / Synergy Picks
  const sharedContainer = document.getElementById('sharedViewContainer');
  const sharedToggle = document.querySelector('[data-shared-view-toggle]');
  if (sharedContainer && sharedToggle) {
    const buttons = sharedToggle.querySelectorAll('button[data-shared-view-mode]');
    const applyMode = (mode) => {
      sharedContainer.classList.remove('shared-view-gallery', 'shared-view-list');
      sharedContainer.classList.add(`shared-view-${mode}`);
      buttons.forEach((btn) => {
        const isActive = btn.dataset.sharedViewMode === mode;
        btn.classList.toggle('view-mode-active', isActive);
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
      });
    };
    buttons.forEach((btn) => {
      btn.addEventListener('click', () => applyMode(btn.dataset.sharedViewMode || 'gallery'));
    });
    applyMode('gallery');
  }
})();
