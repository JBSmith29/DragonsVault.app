/* prints-cycler.js
 * Drop-in alt-art/face cycler for elements like:
 *
 * <div class="js-prints-cycler"
 *      data-prints-uri="https://api.scryfall.com/cards/search?order=released&q=oracleid:...&unique=prints"
 *      data-oracle-id="..."
 *      data-scryfall-id="...">
 *   <img class="card-img-top sfb-main" src="..." alt="..." loading="lazy" data-current-index="0">
 *   <button class="cycle-prev glyph-btn">‹</button>
 *   <button class="cycle-next glyph-btn">›</button>
 *   <button class="cycle-flip glyph-btn" title="Flip">⟲</button>
 *   <div class="cycle-indicator badge bg-dark bg-opacity-75">1 / 1</div>
 * </div>
 */

(function () {
  const ACTIVE_CLASS = 'js-prints-cycler-active';

  /** Small utility: best image url from a Scryfall image_uris object */
  function bestImg(iu) {
    if (!iu) return null;
    return iu.normal || iu.large || iu.small || iu.png || null;
  }

  /** Transform one Scryfall print record -> our normalized entry */
  function toEntry(print) {
    // Single-face
    if (print.image_uris) {
      const front = bestImg(print.image_uris);
      return {
        id: print.id,
        set: String(print.set || '').toUpperCase(),
        cn: print.collector_number || '',
        faces: [{ src: front, label: `${(print.set_name || '').trim()} • ${String(print.collector_number || '').trim()}` }],
        hasBack: false,
      };
    }
    // Double/multi-face
    const faces = Array.isArray(print.card_faces) ? print.card_faces : [];
    const list = faces
      .map(f => ({ src: bestImg(f.image_uris || {}), label: (f.name || '').trim() }))
      .filter(f => !!f.src);
    return {
      id: print.id,
      set: String(print.set || '').toUpperCase(),
      cn: print.collector_number || '',
      faces: list.length ? list : [{ src: null, label: '' }],
      hasBack: list.length >= 2,
    };
  }

  /** Fetch helper with simple error handling */
  async function httpGet(url) {
    const res = await fetch(url, { credentials: 'omit', mode: 'cors' });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return res.json();
  }

  /** Load ALL prints for an oracle via prints_search_uri (paginate) */
  async function loadAllPrints(printsUri) {
    const out = [];
    let url = printsUri;
    let guard = 0;
    while (url && guard < 12) {
      guard++;
      const data = await httpGet(url);
      const chunk = Array.isArray(data.data) ? data.data : [];
      out.push(...chunk);
      url = data.has_more ? data.next_page : null;
    }
    return out;
  }

  /** If only a scryfall id is given, fetch single card to discover faces + prints uri */
  async function loadFromSingleId(cardId) {
    const card = await httpGet(`https://api.scryfall.com/cards/${encodeURIComponent(cardId)}`);
    return { card, printsUri: card.prints_search_uri || null };
  }

  /** Preload a few images ahead for snappy UX */
  function preload(src) {
    if (!src) return;
    const img = new Image();
    img.decoding = 'async';
    img.src = src;
  }

  /** Keep focus/keyboard behavior per active cycler */
  let activeCycler = null;

  function setActiveCycler(el) {
    if (activeCycler === el) return;
    if (activeCycler) activeCycler.classList.remove(ACTIVE_CLASS);
    activeCycler = el;
    if (el) el.classList.add(ACTIVE_CLASS);
  }

  /** Initialize one cycler element */
  function initCycler(el) {
    if (el.__printsReady) return; // idempotent
    el.__printsReady = true;

    const imgEl = el.querySelector('img');
    const prevBtn = el.querySelector('.cycle-prev');
    const nextBtn = el.querySelector('.cycle-next');
    const flipBtn = el.querySelector('.cycle-flip');
    const indicator = el.querySelector('.cycle-indicator');

    const data = {
      loaded: false,
      index: 0,
      face: 0,
      entries: [], // [{ id, set, cn, faces:[{src,label}...], hasBack }]
    };

    function updateIndicator() {
      if (indicator) {
        const total = Math.max(1, data.entries.length);
        indicator.textContent = `${Math.min(data.index + 1, total)} / ${total}`;
      }
    }

    function updateFlipState() {
      const entry = data.entries[data.index];
      if (!flipBtn) return;
      if (entry && entry.hasBack) {
        flipBtn.disabled = false;
        flipBtn.style.display = '';
      } else {
        flipBtn.disabled = true;
        flipBtn.style.display = ''; // keep visible but disabled; switch to 'none' to hide
      }
    }

    function render() {
      const entry = data.entries[data.index];
      if (!entry || !imgEl) return;
      const face = entry.faces[Math.min(data.face, entry.faces.length - 1)] || entry.faces[0];
      if (face && face.src) {
        imgEl.src = face.src;
        imgEl.alt = face.label || imgEl.alt || '';
      }
      updateIndicator();
      updateFlipState();

      // Preload neighbors
      const n = data.entries.length;
      if (n > 1) {
        const prev = data.entries[(data.index - 1 + n) % n]?.faces[0];
        const next = data.entries[(data.index + 1) % n]?.faces[0];
        preload(prev?.src);
        preload(next?.src);
      }
      // Also preload opposite face for current if present
      if (entry.hasBack && entry.faces[1]) preload(entry.faces[1].src);
    }

    function next() {
      if (!data.loaded || data.entries.length <= 1) return;
      data.index = (data.index + 1) % data.entries.length;
      data.face = 0; // reset to front on alt change
      render();
    }
    function prev() {
      if (!data.loaded || data.entries.length <= 1) return;
      data.index = (data.index - 1 + data.entries.length) % data.entries.length;
      data.face = 0;
      render();
    }
    function flip() {
      const entry = data.entries[data.index];
      if (!entry || !entry.hasBack) return;
      data.face = data.face ? 0 : 1;
      render();
    }

    // Wire events
    if (imgEl) {
      imgEl.addEventListener('click', (e) => { e.preventDefault(); next(); });
      imgEl.addEventListener('mouseenter', () => setActiveCycler(el));
      imgEl.addEventListener('focus', () => setActiveCycler(el));
    }
    if (prevBtn) prevBtn.addEventListener('click', (e) => { e.preventDefault(); prev(); });
    if (nextBtn) nextBtn.addEventListener('click', (e) => { e.preventDefault(); next(); });
    if (flipBtn) flipBtn.addEventListener('click', (e) => { e.preventDefault(); flip(); });

    el.addEventListener('mouseenter', () => setActiveCycler(el));
    el.addEventListener('focusin', () => setActiveCycler(el));

    // Keyboard control (delegated once per document; see below)
    el.__controls = { next, prev, flip };

    // Lazy-load content when visible or on first interaction
    let started = false;
    async function startLoading() {
      if (started) return;
      started = true;

      const printsUri   = el.getAttribute('data-prints-uri');
      const oracleId    = el.getAttribute('data-oracle-id');
      const scryfallId  = el.getAttribute('data-scryfall-id');

      try {
        let workingPrintsUri = printsUri;
        let firstCard = null;

        // If no prints uri, try to derive it
        if (!workingPrintsUri) {
          if (scryfallId) {
            const { card, printsUri: puri } = await loadFromSingleId(scryfallId);
            firstCard = card;
            workingPrintsUri = puri;
          } else if (oracleId) {
            workingPrintsUri = `https://api.scryfall.com/cards/search?order=released&q=oracleid:${encodeURIComponent(oracleId)}&unique=prints`;
          }
        }

        // Build entries
        const entries = [];

        // If we fetched the first card already, seed it so user can flip immediately
        if (firstCard) {
          entries.push(toEntry(firstCard));
        }

        if (workingPrintsUri) {
          const prints = await loadAllPrints(workingPrintsUri);
          // Deduplicate by id in case firstCard overlaps
          const seen = new Set(entries.map(e => e.id));
          for (const p of prints) {
            if (!p || !p.id || seen.has(p.id)) continue;
            seen.add(p.id);
            entries.push(toEntry(p));
          }
        }

        // Fallback: if still nothing, at least use the current <img> src
        if (!entries.length && imgEl?.src) {
          entries.push({ id: 'local', set: '', cn: '', faces: [{ src: imgEl.src, label: '' }], hasBack: false });
        }

        data.entries = entries.filter(e => e && e.faces && e.faces[0] && e.faces[0].src);
        data.loaded = true;
        data.index = 0;
        data.face = 0;
        render();
      } catch (err) {
        console.warn('[prints-cycler] load failed:', err);
        // Still render something with the current image
        data.entries = [{ id: 'local', set: '', cn: '', faces: [{ src: imgEl?.src || null, label: '' }], hasBack: false }];
        data.loaded = true;
        data.index = 0; data.face = 0;
        render();
      }
    }

    // IntersectionObserver to lazy-load
    if ('IntersectionObserver' in window) {
      const io = new IntersectionObserver((entries) => {
        entries.forEach(ent => {
          if (ent.isIntersecting) {
            startLoading();
            io.disconnect();
          }
        });
      }, { rootMargin: '200px' });
      io.observe(el);
    } else {
      // Older browsers: load on first mouseenter
      el.addEventListener('mouseenter', startLoading, { once: true });
    }

    // Show initial indicator
    updateIndicator();
    updateFlipState();
  }

  // Initialize all current cyclers
  function initAll() {
    document.querySelectorAll('.js-prints-cycler').forEach(initCycler);
  }

  // Keyboard handler (single, global)
  document.addEventListener('keydown', (e) => {
    if (!activeCycler) return;
    // Avoid when typing in inputs
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.isComposing) return;

    const controls = activeCycler.__controls;
    if (!controls) return;

    if (e.key === 'ArrowRight') {
      e.preventDefault(); controls.next();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault(); controls.prev();
    } else if (e.key.toLowerCase() === 'f') {
      e.preventDefault(); controls.flip();
    }
  });

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }

  // In case content is added dynamically later (htmx/turbo, etc.)
  // you can call window.initPrintCyclers() to re-scan.
  window.initPrintCyclers = initAll;
})();
