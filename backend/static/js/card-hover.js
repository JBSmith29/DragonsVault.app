/* Global card hover preview (site-wide).
 * Shows a larger image when hovering any element with card hints:
 *   data-card-id, data-scry-id, data-scryfall-id, data-hover-src, data-img,
 *   or href="/cards/<id>".
 * Also exposes window.dvHoverPreview.show/hide for pages that already
 * call into a preview helper.
 */

(function () {
  if (window.dvHoverPreview) return;
  const supportsHover = window.matchMedia ? window.matchMedia('(pointer:fine)').matches : true;
  if (!supportsHover) {
    window.dvHoverPreview = { show() {}, hide() {} };
    return;
  }

  const cacheByCardId = new Map();
  const cacheByScryId = new Map();
  const overlay = document.createElement('div');
  overlay.className = 'card-hover-preview';
  overlay.innerHTML = '<img alt="Card preview">';
  const imgEl = overlay.querySelector('img');
  document.body.appendChild(overlay);

  const PLACEHOLDER = (window.CARD_BACK_PLACEHOLDER || '/static/img/card-back-placeholder.png');
  let activeTarget = null;
  let lastPointer = { x: 0, y: 0 };

  function hide(target) {
    if (target && activeTarget && target !== activeTarget) return;
    overlay.classList.remove('is-visible');
    activeTarget = null;
  }

  function hideAll() {
    hide(null);
  }

  function positionOverlay(x, y) {
    const offset = 18;
    const maxW = overlay.offsetWidth || imgEl.offsetWidth || 280;
    const maxH = overlay.offsetHeight || imgEl.offsetHeight || 380;
    let left = x + offset;
    let top = y + offset;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (left + maxW > vw - 12) left = x - maxW - offset;
    if (top + maxH > vh - 12) top = vh - maxH - 12;
    if (top < 8) top = 8;
    overlay.style.left = `${left}px`;
    overlay.style.top = `${top}px`;
  }

  async function show(target, src, pointerEvent) {
    if (!src) return hide(target);
    activeTarget = target || null;
    imgEl.src = src;
    overlay.classList.add('is-visible');
    const evt = pointerEvent || { clientX: lastPointer.x, clientY: lastPointer.y };
    positionOverlay(evt.clientX, evt.clientY);
  }

  async function fetchCardImage(cardId) {
    if (cacheByCardId.has(cardId)) return cacheByCardId.get(cardId);
    try {
      const resp = await fetch(`/api/card/${cardId}`);
      if (!resp.ok) throw new Error('card api error');
      const data = await resp.json();
      const imgs = (data && data.images) || [];
      const src = imgs[0]?.png || imgs[0]?.large || imgs[0]?.normal || imgs[0]?.small || null;
      cacheByCardId.set(cardId, src);
      return src;
    } catch {
      cacheByCardId.set(cardId, null);
      return null;
    }
  }

  async function fetchScryImage(sid) {
    if (cacheByScryId.has(sid)) return cacheByScryId.get(sid);
    try {
      const resp = await fetch(`https://api.scryfall.com/cards/${sid}`);
      if (!resp.ok) throw new Error('scry api error');
      const data = await resp.json();
      let src = null;
      if (data?.image_uris) {
        src = data.image_uris.png || data.image_uris.large || data.image_uris.normal || data.image_uris.small || null;
      } else if (Array.isArray(data?.card_faces) && data.card_faces.length) {
        const f = data.card_faces[0];
        src = f?.image_uris?.png || f?.image_uris?.large || f?.image_uris?.normal || f?.image_uris?.small || null;
      }
      cacheByScryId.set(sid, src);
      return src;
    } catch {
      cacheByScryId.set(sid, null);
      return null;
    }
  }

  function extractCardInfo(el) {
    const data = el.dataset || {};
    let cardId = data.cardId || null;
    let scryId = data.scryId || data.scryfallId || null;
    let hoverSrc = data.hoverSrc || data.img || null;

    if (!cardId && el.tagName === 'A' && el.getAttribute('href')) {
      const m = el.getAttribute('href').match(/\/cards\/(\d+)/);
      if (m) cardId = m[1];
    }
    if (!hoverSrc && el.tagName === 'IMG') {
      hoverSrc = el.currentSrc || el.src || null;
    }
    return { cardId, scryId, hoverSrc };
  }

  async function resolveImage(el) {
    if (!el) return null;
    const info = extractCardInfo(el);
    // Prefer the owned printing (card id) when available, then other hints.
    if (info.cardId) {
      const src = await fetchCardImage(info.cardId);
      if (src) return src;
    }
    if (info.hoverSrc) return info.hoverSrc;
    if (info.scryId) return (await fetchScryImage(info.scryId)) || PLACEHOLDER;
    return null;
  }

  const NAME_SELECTOR = [
    '[data-card-name]',
    'a.card-link',
    'a.checker-card-link',
    'a.wl-card-link',
    '.combo-card-name',
    '.calc-card-name',
    '.deck-name-link',
    '.deck-hover-target',
    '.rec-card-title',
    '.rec-row-title',
    '.edhrec-card__title',
    '.card-chip-link',
  ].join(',');
  const ACTIVATION_SELECTOR = `img, ${NAME_SELECTOR}`;
  const DATA_SELECTOR = '[data-card-id],[data-scry-id],[data-scryfall-id],[data-hover-src],[data-img],a[href*="/cards/"],img';

  function isTarget(el) {
    if (!el || el === document.body) return false;
    if (el.dataset && el.dataset.ignoreHover === 'true') return false;
    const classList = el.classList;
    if (!classList || typeof classList.contains !== 'function') return false;
    if (classList.contains('mana') || classList.contains('mana-symbol') || classList.contains('mana-cost')) return false;
    if (el.tagName === 'IMG') {
      const src = el.getAttribute('src') || '';
      if (src.includes('/symbols/') || src.includes('card-symbols')) return false;
      const hasHint = el.dataset.cardId || el.dataset.scryId || el.dataset.scryfallId || el.dataset.hoverSrc || el.dataset.img;
      if (!hasHint) return false;
    }
    const hasDataHint = el.dataset && (el.dataset.cardId || el.dataset.scryId || el.dataset.scryfallId || el.dataset.hoverSrc || el.dataset.img);
    const anchorCard = el.tagName === 'A' && el.getAttribute('href') && el.getAttribute('href').includes('/cards/');
    return !!(hasDataHint || anchorCard || (el.tagName === 'IMG' && !classList.contains('mana')));
  }

  async function handleEnter(evt) {
    const rawTarget = evt.target;
    const activation = rawTarget && rawTarget.closest ? rawTarget.closest(ACTIVATION_SELECTOR) : rawTarget;
    if (!activation) return;
    if (activation && (activation.dataset?.ignoreHover === 'true' || activation.closest?.('[data-ignore-hover="true"]'))) {
      return;
    }
    const hoverTarget = activation.closest ? activation.closest(DATA_SELECTOR) : activation;
    if (!hoverTarget || !isTarget(hoverTarget)) return;
    const src = await resolveImage(hoverTarget);
    await show(activation, src, evt);
  }

  function handleLeave(evt) {
    const related = evt.relatedTarget;
    if (related && (related === overlay || overlay.contains(related))) {
      return;
    }
    hide(evt.target);
  }

  function handleMove(evt) {
    lastPointer = { x: evt.clientX, y: evt.clientY };
    if (overlay.classList.contains('is-visible')) {
      positionOverlay(evt.clientX, evt.clientY);
    }
  }

  document.addEventListener('pointerenter', handleEnter, true);
  document.addEventListener('pointerleave', handleLeave, true);
  document.addEventListener('pointermove', handleMove, true);
  document.addEventListener('pointerdown', hideAll, true);
  window.addEventListener('pagehide', hideAll);
  window.addEventListener('beforeunload', hideAll);

  window.dvHoverPreview = {
    show: show,
    hide: hide,
  };
})();
