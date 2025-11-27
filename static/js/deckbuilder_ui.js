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
})();
