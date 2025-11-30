// Commander recommendation page interactions.
(function () {
  const sliderTrack = document.querySelector("[data-slider-track]");
  const prevBtn = document.querySelector("[data-slider-prev]");
  const nextBtn = document.querySelector("[data-slider-next]");

  function updateNavState() {
    if (!sliderTrack) return;
    const maxScroll = sliderTrack.scrollWidth - sliderTrack.clientWidth - 4;
    if (prevBtn) prevBtn.disabled = sliderTrack.scrollLeft <= 0;
    if (nextBtn) nextBtn.disabled = sliderTrack.scrollLeft >= maxScroll;
  }

  function scrollByDir(dir) {
    if (!sliderTrack) return;
    const delta = sliderTrack.clientWidth * 0.8 * dir;
    sliderTrack.scrollBy({ left: delta, behavior: "smooth" });
    setTimeout(updateNavState, 300);
  }

  if (prevBtn) prevBtn.addEventListener("click", () => scrollByDir(-1));
  if (nextBtn) nextBtn.addEventListener("click", () => scrollByDir(1));
  if (sliderTrack) {
    sliderTrack.addEventListener("scroll", updateNavState);
    updateNavState();
  }

  // Smooth scroll when switching tabs to keep the content in view.
  document.querySelectorAll("#commanderTabs button[data-bs-toggle='tab']").forEach((btn) => {
    btn.addEventListener("shown.bs.tab", () => {
      const page = document.getElementById("commanderRecommendPage");
      if (page) page.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  // Swipe-to-close modals on mobile.
  function bindSwipeClose(modalEl) {
    if (typeof bootstrap === "undefined") return;
    const dialog = modalEl.querySelector(".modal-dialog");
    if (!dialog) return;
    let startY = null;
    let endY = null;

    dialog.addEventListener("touchstart", (e) => {
      if (e.touches && e.touches.length) startY = e.touches[0].clientY;
    });
    dialog.addEventListener("touchmove", (e) => {
      if (e.touches && e.touches.length) endY = e.touches[0].clientY;
    });
    dialog.addEventListener("touchend", () => {
      if (startY === null || endY === null) return;
      if (endY - startY > 60) {
        const instance = bootstrap.Modal.getOrCreateInstance(modalEl);
        instance.hide();
      }
      startY = null;
      endY = null;
    });
  }

  document.querySelectorAll(".commander-modal").forEach(bindSwipeClose);

  // Re-bind swipe when modals are added dynamically (unlikely but keeps HTMX safe).
  document.addEventListener("shown.bs.modal", (event) => {
    if (event.target && event.target.classList.contains("commander-modal")) {
      bindSwipeClose(event.target);
    }
  });

  // ---------- Sorting ----------
  function sortCards(container, mode) {
    if (!container) return;
    const cards = Array.from(container.querySelectorAll(".commander-card"));
    const collator = new Intl.Collator(undefined, { sensitivity: "base" });
    const sorted = cards.sort((a, b) => {
      const nameA = a.dataset.name || "";
      const nameB = b.dataset.name || "";
      if (mode === "name") {
        return collator.compare(nameA, nameB);
      }
      if (mode === "tag") {
        const tagA = a.dataset.tag || "";
        const tagB = b.dataset.tag || "";
        const cmp = collator.compare(tagA, tagB);
        if (cmp !== 0) return cmp;
        return collator.compare(nameA, nameB);
      }
      // default: score desc
      const scoreA = parseFloat(a.dataset.score || "0") || 0;
      const scoreB = parseFloat(b.dataset.score || "0") || 0;
      if (scoreA === scoreB) return collator.compare(nameA, nameB);
      return scoreB - scoreA;
    });
    sorted.forEach((el) => container.appendChild(el));
  }

  function applySort(mode) {
    const recGrid = document.querySelector("#tabRecommended .commander-grid");
    const allGrid = document.querySelector("#tabAllOwned .commander-grid");
    sortCards(recGrid, mode);
    sortCards(allGrid, mode === "score" ? "name" : mode); // all owned defaults to name when score chosen
  }

  function applyTagFilter(tagValue) {
    const normalized = (tagValue || "").trim().toLowerCase();
    const grids = [
      document.querySelector("#tabRecommended .commander-grid"),
      document.querySelector("#tabAllOwned .commander-grid"),
    ];
    grids.forEach((grid) => {
      if (!grid) return;
      const cards = Array.from(grid.querySelectorAll(".commander-card"));
      cards.forEach((card) => {
        const cardTag = (card.dataset.tag || "").trim().toLowerCase();
        const match = !normalized || cardTag === normalized;
        card.classList.toggle("d-none", !match);
      });
    });
  }

  const sortWrapper = document.querySelector('[data-dv-select="commander-sort"]');
  const tagWrapper = document.querySelector('[data-dv-select="commander-tag-filter"]');

  if (sortWrapper) {
    const input = sortWrapper.querySelector("[data-dv-select-input]");
    const currentValue = () => (input?.value || sortWrapper.dataset.dvSelectValue || "score");
    const syncSort = () => applySort(currentValue());
    sortWrapper.addEventListener("dv-select:change", syncSort);
    sortWrapper.addEventListener("dv-select:ready", syncSort);
    syncSort();
  } else {
    applySort("score");
  }

  if (tagWrapper) {
    const input = tagWrapper.querySelector("[data-dv-select-input]");
    const currentValue = () => (input?.value || tagWrapper.dataset.dvSelectValue || "");
    const syncFilter = () => applyTagFilter(currentValue());
    tagWrapper.addEventListener("dv-select:change", syncFilter);
    tagWrapper.addEventListener("dv-select:ready", syncFilter);
    syncFilter();
  }
})();
