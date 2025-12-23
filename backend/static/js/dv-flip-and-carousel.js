// static/js/dv-flip-and-carousel.js
// Reusable helpers for Scryfall-style flips + carousels (works in drawers too)

window.DV = window.DV || {};

(function () {
  const facesCache = new Map();

  async function fetchFaces(sid) {
    if (!sid) return { front: null, back: null };
    if (facesCache.has(sid)) return facesCache.get(sid);
    try {
      const res = await fetch(`/api/print/${encodeURIComponent(sid)}/faces`, { credentials: "same-origin" });
      if (!res.ok) throw 0;
      const d = await res.json();
      facesCache.set(sid, d);
      return d;
    } catch {
      const d = { front: null, back: null };
      facesCache.set(sid, d);
      return d;
    }
  }

  async function applyFlipToSlide(slideEl, flipState) {
    const sid = slideEl?.getAttribute("data-sid") || "";
    const img = slideEl?.querySelector("img");
    if (!sid || !img) return;
    const faces = await fetchFaces(sid);
    const url = (flipState === "back") ? (faces.back || faces.front) : (faces.front || faces.back);
    if (url && img.getAttribute("src") !== url) img.setAttribute("src", url);
  }

  // Public API
  window.DV.fetchFaces = fetchFaces;
  window.DV.applyFlipToSlide = applyFlipToSlide;

  // ---------- Public: add flip overlay to any thumbnail that has data-sid ----------
  // Works for scryfall_browser, cards list, set pages, etc.
  DV.initScryfallFlipOverlays = function initScryfallFlipOverlays(root = document) {
    // Add overlay buttons to any thumb wrapper that has data-sid
    const hosts = root.querySelectorAll('.dv-thumb-wrap[data-sid], .sf-thumb-wrap[data-sid]');
    hosts.forEach(host => {
      if (host.querySelector('.dv-flip-overlay')) return; // already decorated
      const sid = host.getAttribute('data-sid') || '';
      const img = host.querySelector('img');
      if (!sid || !img) return;

      // Ensure host is positioning context
      const cs = getComputedStyle(host);
      if (cs.position === 'static') host.style.position = 'relative';

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'dv-flip-overlay';
      btn.title = 'Flip (⇆)';
      btn.textContent = '⇆';
      btn.style.display = 'none'; // only show when a back face exists
      btn.setAttribute('aria-label', 'Flip card face');
      host.appendChild(btn);

      let state = 'front';

      async function ensureFacesAndMaybeShow() {
        const faces = await fetchFaces(sid);
        const hasBack = !!faces.back;
        btn.style.display = hasBack ? 'block' : 'none';
      }
      ensureFacesAndMaybeShow();

      async function toggle() {
        const faces = await fetchFaces(sid);
        if (!faces.back) return;
        state = (state === 'front') ? 'back' : 'front';
        const url = (state === 'back') ? (faces.back || faces.front) : (faces.front || faces.back);
        if (url && img.getAttribute('src') !== url) img.setAttribute('src', url);
      }

      // Important: keep carousel/anchor intact
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation(); // don’t advance carousel or follow anchor
        toggle();
      });
    });
  };

  // ---------- Public: initialize card_detail that was injected into a drawer/modal ----------
  DV.initCardDetailIn = function initCardDetailIn(container) {
    if (!container) return;

    const carouselEl = container.querySelector('#artworkCarousel');
    const flipBtn    = container.querySelector('#flipFaceBtn');
    const flipOver   = container.querySelector('#flipOverlayBtn');

    // Bootstrap carousel
    let carousel = null;
    if (carouselEl && typeof bootstrap !== 'undefined') {
      carousel = new bootstrap.Carousel(carouselEl, { interval: false, ride: false, pause: true, wrap: true });
      // click image to advance
      carouselEl.addEventListener('click', (ev) => {
        if (ev.target && ev.target.tagName === 'IMG') {
          ev.preventDefault();
          carousel.next();
        }
      });
    }

    // Flip logic (same pattern as card_detail, but scoped)
    let flipState = 'front';

    const activeSlide = () => container.querySelector('#artworkCarousel .carousel-item.active') || null;

    function setFlipEnabled(enabled) {
      if (flipBtn) {
        flipBtn.disabled = !enabled;
        flipBtn.classList.toggle('btn-primary', enabled);
        flipBtn.classList.toggle('btn-outline-secondary', !enabled);
        flipBtn.textContent = enabled
          ? (flipState === 'front' ? '⇆ Flip to Back' : '⇆ Flip to Front')
          : '⇆ Flip';
      }
      if (flipOver) flipOver.style.display = enabled ? 'block' : 'none';
    }

    async function applyFlipToActive() {
      const slide = activeSlide();
      if (!slide) return;
      const sid = slide.getAttribute('data-sid') || '';
      const img = slide.querySelector('img');
      if (!sid || !img) return;

      const faces = await fetchFaces(sid);
      const hasBack = !!faces.back;
      setFlipEnabled(hasBack);
      if (!hasBack) return;

      const url = (flipState === 'back') ? (faces.back || faces.front) : (faces.front || faces.back);
      if (url && img.getAttribute('src') !== url) img.setAttribute('src', url);
    }

    async function prepareFlipForActive() {
      flipState = 'front';
      await applyFlipToActive();
    }

    function toggleFlip() {
      if (flipBtn && flipBtn.disabled) return;
      flipState = (flipState === 'front') ? 'back' : 'front';
      applyFlipToActive();
    }

    if (flipBtn)  flipBtn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); toggleFlip(); });
    if (flipOver) flipOver.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); toggleFlip(); });
    if (carouselEl) carouselEl.addEventListener('slid.bs.carousel', prepareFlipForActive);

    // initial run
    prepareFlipForActive();
  };

  // ---------- Public: bind global listeners for drawers / modals / HTMX ----------
  DV.bindDrawerReinit = function bindDrawerReinit() {
    document.addEventListener('shown.bs.offcanvas', (e) => DV.initCardDetailIn(e.target));
    document.addEventListener('shown.bs.modal',     (e) => DV.initCardDetailIn(e.target));
    document.addEventListener('htmx:afterSwap',     (e) => {
      const root = e.target;
      if (root.closest('.offcanvas, .modal') || root.matches('.offcanvas, .modal')) {
        DV.initCardDetailIn(root.closest('.offcanvas, .modal') || root);
      }
      DV.initScryfallFlipOverlays(root); // decorate any new thumbs
    });
  };

})();
