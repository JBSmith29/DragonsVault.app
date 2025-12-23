/* DragonsVault — prints-cycler.js
 * Alt-art/face cycler for elements like:
 *
 * <div class="js-prints-cycler"
 *      data-prints-uri="https://api.scryfall.com/cards/search?order=released&q=oracleid:...&unique=prints"
 *      data-oracle-id="..."
 *      data-scryfall-id="...">
 *   <img class="card-img-top sfb-main" src="..." alt="..." loading="lazy" data-current-index="0">
 *   <button class="cycle-prev glyph-btn" type="button">‹</button>
 *   <button class="cycle-next glyph-btn" type="button">›</button>
 *   <button class="cycle-flip glyph-btn" type="button" title="Flip">⟲</button>
 *   <div class="cycle-indicator badge bg-dark bg-opacity-75">1 / 1</div>
 * </div>
 *
 * Notes:
 * - Stops flip clicks from bubbling into the cycler (so flip ≠ cycle).
 * - Updates <img data-current-index="..."> so face-flip.js can reload faces.
 * - Idempotent: safe to re-run after HTMX swaps (uses data-dv-bound).
 */

(function () {
  const ACTIVE_CLASS = "js-prints-cycler-active";
  const cache = new Map(); // printsUri -> entries [{id,set,cn,faces:[{src,label}],hasBack}]
  let activeCycler = null;

  function bestImg(iu) {
    if (!iu) return null;
    return iu.large || iu.normal || iu.small || iu.png || null;
  }

  function toEntry(print) {
    if (!print) return null;

    const purchaseUris = print.purchase_uris || {};
    const entry = {
      id: print.id,
      set: String(print.set || "").toUpperCase(),
      setName: (print.set_name || "").trim(),
      cn: String(print.collector_number || ""),
      name: (print.name || "").trim(),
      rarity: (print.rarity || "").trim(),
      lang: String(print.lang || "").toUpperCase(),
      prices: print.prices && typeof print.prices === "object" ? { ...print.prices } : null,
      scryUri: print.scryfall_uri || print.uri || null,
      tcgUri: purchaseUris.tcgplayer || purchaseUris.cardmarket || purchaseUris.tcgplayer_direct || null,
      releasedAt: print.released_at || "",
      hasBack: false,
      faces: []
    };

    if (Array.isArray(print.card_faces) && print.card_faces.length) {
      const list = print.card_faces
        .map(f => ({ src: bestImg(f.image_uris || {}), label: (f.name || "").trim() }))
        .filter(f => !!f.src);
      entry.faces = list.length ? list : [{ src: bestImg(print.image_uris || {}), label: (print.name || "").trim() }];
      entry.hasBack = entry.faces.length >= 2;
      if (!entry.name && entry.faces.length) entry.name = entry.faces[0].label || entry.name;
      return entry;
    }

    const front = bestImg(print.image_uris || {});
    entry.faces = [{
      src: front,
      label: (print.set_name ? print.set_name.trim() + " • " : "") + String(print.collector_number || "").trim()
    }];
    return entry;
  }

  async function httpGet(url) {
    const res = await fetch(url, { credentials: "omit", mode: "cors" });
    if (!res.ok) throw new Error("HTTP " + res.status + " for " + url);
    return res.json();
  }

  async function loadAllPrints(printsUri) {
    if (!printsUri) return [];
    if (cache.has(printsUri)) return cache.get(printsUri);

    const out = [];
    let url = printsUri;
    let guard = 0;
    try {
      while (url && guard < 12) {
        guard++;
        const data = await httpGet(url);
        const chunk = Array.isArray(data.data) ? data.data : [];
        out.push(...chunk);
        url = data.has_more ? data.next_page : null;
      }
      const entries = out.map(toEntry).filter(Boolean);
      cache.set(printsUri, entries);
      return entries;
    } catch {
      // cache empty result so we don't hammer the API
      cache.set(printsUri, []);
      return [];
    }
  }

  async function loadFromSingleId(cardId) {
    try {
      const card = await httpGet(`https://api.scryfall.com/cards/${encodeURIComponent(cardId)}`);
      return { card, printsUri: card.prints_search_uri || null };
    } catch {
      return { card: null, printsUri: null };
    }
  }

  function preload(src) {
    if (!src) return;
    const img = new Image();
    img.decoding = "async";
    img.src = src;
  }

  function setActiveCycler(el) {
    if (activeCycler === el) return;
    if (activeCycler) activeCycler.classList.remove(ACTIVE_CLASS);
    activeCycler = el;
    if (el) el.classList.add(ACTIVE_CLASS);
  }

  function bindCycler(el) {
    if (!el || el.dataset.dvBound === "1") return;
    el.dataset.dvBound = "1";

    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");

    const imgEl = el.querySelector("img.sfb-main, img.card-img-top, img");
    const prevBtn = el.querySelector(".cycle-prev");
    const nextBtn = el.querySelector(".cycle-next");
    const flipBtn = el.querySelector(".cycle-flip");
    const indicator = el.querySelector(".cycle-indicator");
    const card = el.closest(".sfb-card");
    const nameEl = card ? card.querySelector(".js-print-name") : null;
    const setEl = card ? card.querySelector(".js-print-set") : null;
    const rarityEl = card ? card.querySelector(".js-print-rarity") : null;
    const priceTargetId = el.getAttribute("data-price-target");
    const priceEl = priceTargetId ? document.getElementById(priceTargetId) : null;
    const pricePrefix = priceEl ? (priceEl.dataset.prefix || "") : "";
    const pricePlaceholder = priceEl ? (priceEl.dataset.placeholder || "N/A") : "N/A";
    const scryLink = card ? card.querySelector(".js-print-scryfall") : null;
    const scryDefaultHref = scryLink ? (scryLink.getAttribute("data-default-href") || "#") : "#";
    const tcgLink = card ? card.querySelector(".js-print-tcg") : null;
    const tcgDefaultHref = tcgLink ? (tcgLink.getAttribute("data-default-href") || "#") : "#";

    const state = {
      loaded: false,
      index: 0,
      face: 0,
      entries: []
    };

    function updateIndicator() {
      if (!indicator) return;
      const total = Math.max(1, state.entries.length);
      const value = Math.min(state.index + 1, total);
      indicator.textContent = String(value) + " / " + String(total);
    }

    function updateFlipEnabled() {
      if (!flipBtn) return;
      const entry = state.entries[state.index];
      const hasBack = !!(entry && entry.hasBack && entry.faces && entry.faces[1]);
      flipBtn.disabled = !hasBack;
      flipBtn.setAttribute("aria-disabled", hasBack ? "false" : "true");
    }

    function formatAmount(value, prefix) {
      if (value === null || value === undefined || value === "") return null;
      const num = Number(value);
      if (!Number.isFinite(num) || num <= 0) return null;
      return prefix + num.toFixed(2);
    }

    function formatPrice(prices) {
      if (!prices) return null;
      const parts = [];
      const usd = formatAmount(prices.usd, "$");
      const usdFoil = formatAmount(prices.usd_foil, "$");
      const usdEtched = formatAmount(prices.usd_etched, "$");
      if (usd) parts.push("Normal " + usd);
      if (usdFoil) parts.push("Foil " + usdFoil);
      if (usdEtched) parts.push("Etched " + usdEtched);

      if (!parts.length) {
        const eur = formatAmount(prices.eur, "EUR ");
        const eurFoil = formatAmount(prices.eur_foil, "EUR ");
        if (eur) parts.push("Normal " + eur);
        if (eurFoil) parts.push("Foil " + eurFoil);
      }

      if (!parts.length) {
        const tix = formatAmount(prices.tix, "");
        if (tix) parts.push(tix + " tix");
      }

      if (!parts.length) return null;
      return parts.join(" | ");
    }

    function toggleLink(btn, url, fallbackHref) {
      if (!btn) return;
      const cleaned = (url || "").trim();
      const hasUrl = cleaned.length > 0;
      btn.href = hasUrl ? cleaned : (fallbackHref || '#');
      btn.classList.toggle('disabled', !hasUrl);
      if (hasUrl) {
        btn.setAttribute('target', '_blank');
        btn.setAttribute('rel', 'noopener');
        btn.setAttribute('aria-disabled', 'false');
        btn.setAttribute('tabindex', '0');
      } else {
        btn.removeAttribute('target');
        btn.removeAttribute('rel');
        btn.setAttribute('aria-disabled', 'true');
        btn.setAttribute('tabindex', '-1');
      }
    }

    function updatePrice() {
      if (!priceEl) return;
      const entry = state.entries[state.index];
      const formatted = entry ? formatPrice(entry.prices) : null;
      priceEl.textContent = pricePrefix + (formatted || pricePlaceholder);
    }

    function updateDetails() {
      const entry = state.entries[state.index];
      if (!entry) return;

      if (nameEl) {
        if (entry.name) nameEl.textContent = entry.name;
        const template = nameEl.getAttribute('data-detail-template');
        if (template && entry.id) {
          nameEl.href = template.replace('__SID__', entry.id);
        } else if (entry.scryUri) {
          nameEl.href = entry.scryUri;
        }
        if (entry.id) nameEl.setAttribute('data-current-sid', entry.id);
      }

      if (setEl) {
        const parts = [];
        if (entry.setName) parts.push(entry.setName);
        if (entry.set) parts.push('(' + entry.set + ')');
        if (entry.cn) parts.push('#' + entry.cn);
        if (entry.lang) parts.push(entry.lang);
        setEl.textContent = parts.length ? parts.join(' · ') : "\u00A0";
      }

      if (rarityEl) {
        rarityEl.textContent = entry.rarity ? capitalize(entry.rarity) : "\u00A0";
      }

      toggleLink(scryLink, entry.scryUri, scryDefaultHref);
      toggleLink(tcgLink, entry.tcgUri, tcgDefaultHref);
    }

    function render() {
      const entry = state.entries[state.index];
      if (!entry || !imgEl) return;

      const faces = entry.faces || [];
      const face = faces[Math.min(state.face, faces.length - 1)] || faces[0];
      if (face && face.src) {
        imgEl.src = face.src;
        if (face.label) imgEl.alt = face.label;
      }

      imgEl.setAttribute('data-current-index', String(state.index));
      if (entry.id) el.setAttribute('data-sid', entry.id);

      updateIndicator();
      updateFlipEnabled();
      updatePrice();
      updateDetails();

      const total = state.entries.length;
      if (total > 1) {
        const prev = state.entries[(state.index - 1 + total) % total];
        const next = state.entries[(state.index + 1) % total];
        preload(prev && prev.faces && prev.faces[0] && prev.faces[0].src);
        preload(next && next.faces && next.faces[0] && next.faces[0].src);
      }
      if (entry.hasBack && entry.faces && entry.faces[1]) preload(entry.faces[1].src);
    }

    function advance(delta) {
      if (!state.loaded || state.entries.length <= 1) return;
      state.index = (state.index + delta + state.entries.length) % state.entries.length;
      state.face = 0;
      render();
    }

    function flip() {
      const entry = state.entries[state.index];
      if (!entry || !entry.hasBack) return;
      state.face = state.face ? 0 : 1;
      render();
    }

    if (prevBtn) prevBtn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); advance(-1); });
    if (nextBtn) nextBtn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); advance(+1); });
    if (flipBtn) flipBtn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); flip(); });

    if (imgEl) {
      imgEl.addEventListener('click', (e) => { e.preventDefault(); advance(+1); });
      imgEl.addEventListener('mouseenter', () => setActiveCycler(el));
      imgEl.addEventListener('focus', () => setActiveCycler(el));
    }

    el.addEventListener('click', (e) => {
      if (e.target && e.target.closest('.cycle-flip')) return;
    });
    el.addEventListener('mouseenter', () => setActiveCycler(el));
    el.addEventListener('focusin', () => setActiveCycler(el));
    el.addEventListener('keydown', (e) => {
      const key = (e.key || '').toLowerCase();
      if (key === 'arrowright') { e.preventDefault(); advance(+1); }
      else if (key === 'arrowleft') { e.preventDefault(); advance(-1); }
      else if (key === 'f') { e.preventDefault(); flip(); }
    });

    let started = false;
    async function startLoading() {
      if (started) return;
      started = true;

      const printsUri = el.getAttribute('data-prints-uri');
      const oracleId = el.getAttribute('data-oracle-id');
      const scryId = el.getAttribute('data-scryfall-id');
      const entries = [];

      try {
        let workingUri = printsUri;
        let seeded = null;

        if (!workingUri && scryId) {
          const meta = await loadFromSingleId(scryId);
          if (meta.card) seeded = toEntry(meta.card);
          workingUri = meta.printsUri;
        } else if (!workingUri && oracleId) {
          workingUri = `https://api.scryfall.com/cards/search?order=released&q=oracleid:${encodeURIComponent(oracleId)}&unique=prints`;
        }

        if (seeded) entries.push(seeded);

        if (workingUri) {
          const all = await loadAllPrints(workingUri);
          const seen = new Set(entries.map(e => e && e.id));
          for (const p of all) {
            if (!p || !p.id || seen.has(p.id)) continue;
            seen.add(p.id);
            entries.push(p);
          }
        }

        if (!entries.length && imgEl && imgEl.src) {
          entries.push({
            id: 'local',
            set: '',
            setName: '',
            cn: '',
            prices: {},
            name: imgEl.alt || '',
            rarity: '',
            lang: '',
            scryUri: null,
            tcgUri: null,
            releasedAt: '',
            faces: [{ src: imgEl.src, label: imgEl.alt || '' }],
            hasBack: false
          });
        }

        state.entries = entries.filter(e => e && e.faces && e.faces[0] && e.faces[0].src);
        state.loaded = true;
        state.index = 0;
        state.face = 0;
        render();
      } catch (err) {
        console.warn('[prints-cycler] load failed:', err);
        const fallback = {
          id: 'local',
          set: '',
          setName: '',
          cn: '',
          prices: {},
          name: imgEl && imgEl.alt ? imgEl.alt : '',
          rarity: '',
          lang: '',
          scryUri: null,
          tcgUri: null,
          releasedAt: '',
          faces: [{ src: (imgEl && imgEl.src) || null, label: (imgEl && imgEl.alt) || '' }],
          hasBack: false
        };
        state.entries = [fallback];
        state.loaded = true;
        state.index = 0;
        state.face = 0;
        render();
      }
    }

    if ('IntersectionObserver' in window) {
      const io = new IntersectionObserver((ents) => {
        ents.forEach(ent => {
          if (ent.isIntersecting) {
            startLoading();
            io.disconnect();
          }
        });
      }, { rootMargin: '200px' });
      io.observe(el);
    } else {
      el.addEventListener('mouseenter', startLoading, { once: true });
      el.addEventListener('focusin', startLoading, { once: true });
    }

    updateIndicator();
    updateFlipEnabled();
    updatePrice();
    updateDetails();
  }

  function initAll() {
    document.querySelectorAll(".js-prints-cycler").forEach(bindCycler);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Re-scan if content is swapped in (HTMX)
  document.addEventListener("htmx:afterSwap", (evt) => {
    const root = evt && evt.target ? evt.target : document;
    root.querySelectorAll(".js-prints-cycler").forEach(bindCycler);
  });

  // Public re-init hook
  window.initPrintCyclers = initAll;
})();
