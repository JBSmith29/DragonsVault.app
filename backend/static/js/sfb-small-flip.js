/* sfb-small-flip.js — flip faces on the Scryfall browser grid without changing templates.
 * - Injects a flip button per .js-prints-cycler
 * - Tracks the currently displayed print via data-current-index
 * - Fetches faces for that print id and toggles front/back
 */
(function () {
  const facesCache = new Map(); // sid -> {front, back, name}
  const printsCache = new Map(); // printsUri -> [{id, ...}, ...]

  async function fetchJSON(url) {
    try {
      const r = await fetch(url, { credentials: "omit" });
      if (!r.ok) throw 0;
      return await r.json();
    } catch {
      return null;
    }
  }

  function bestImg(u) {
    if (!u) return null;
    return u.large || u.normal || u.small || u.png || null;
  }

  async function fetchAllPrints(printsUri) {
    if (!printsUri) return [];
    if (printsCache.has(printsUri)) return printsCache.get(printsUri);

    const out = [];
    let url = printsUri, guard = 0;
    while (url && guard < 12) {
      guard++;
      const d = await fetchJSON(url);
      if (!d) break;
      out.push(...(Array.isArray(d.data) ? d.data : []));
      url = d.has_more ? d.next_page : null;
    }
    printsCache.set(printsUri, out);
    return out;
  }

  async function fetchFacesBySid(sid) {
    if (!sid) return { front: null, back: null, name: null };
    if (facesCache.has(sid)) return facesCache.get(sid);

    // Try local cache endpoint first if you add it later; otherwise Scryfall fallback:
    let d = await fetchJSON(`/api/print/${encodeURIComponent(sid)}/faces`);
    if (!d || (!d.front && !d.back)) {
      const s = await fetchJSON(`https://api.scryfall.com/cards/${encodeURIComponent(sid)}`);
      if (s) {
        const faces = Array.isArray(s.card_faces) ? s.card_faces : [];
        d = {
          front: bestImg((faces[0] && faces[0].image_uris) || s.image_uris || {}),
          back:  bestImg((faces[1] && faces[1].image_uris) || {}),
          name:  s.name || null
        };
      }
    }
    d = d || { front: null, back: null, name: null };
    facesCache.set(sid, d);
    return d;
  }

  function ensureFlipButton(container) {
    let btn = container.querySelector(":scope > .cycle-flip");
    if (btn) return btn;
    btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cycle-flip glyph-btn text-light";
    btn.title = "Flip face";
    btn.setAttribute("aria-label", "Flip face");
    btn.textContent = "⟲";
    // put it last so it sits above prev/next overlays
    container.appendChild(btn);
    return btn;
  }

  function updateAlt(img, baseName, showing) {
    const base = (baseName || img.alt || "").replace(/\s+(—|-)\s+(front|back)$/i, "").trim();
    img.alt = base ? `${base} — ${showing}` : `Card — ${showing}`;
  }

  async function wire(container) {
    if (!container || container.dataset.sfbFlipBound === "1") return;
    container.dataset.sfbFlipBound = "1";

    const img = container.querySelector("img.sfb-main, img.card-img-top, img");
    if (!img) return;

    const printsUri = container.getAttribute("data-prints-uri") || "";
    let staticSid = container.getAttribute("data-sid") || ""; // optional if already present
    let prints = null; // loaded on demand

    const btn = ensureFlipButton(container);
    // keep it above the prev/next overlays
    btn.style.position = "absolute";
    btn.style.top = "8px";
    btn.style.left = "8px";
    btn.style.zIndex = "3";

    // local state for this card
    const st = { sid: staticSid || null, front: null, back: null, showing: "front", name: null };
    btn.disabled = true;
    btn.setAttribute("aria-disabled", "true");

    async function currentSid() {
      if (staticSid) return staticSid;

      // derive sid from current index via printsUri
      if (!printsUri) return null;
      if (!prints) prints = await fetchAllPrints(printsUri);
      const idx = parseInt(img.getAttribute("data-current-index") || "0", 10) || 0;
      const pr = prints[Math.max(0, Math.min(idx, (prints.length || 1) - 1))];
      return pr && pr.id ? pr.id : null;
    }

    async function prepare() {
      const sid = await currentSid();
      if (!sid) {
        btn.disabled = true;
        btn.setAttribute("aria-disabled", "true");
        return;
      }
      st.sid = sid;
      const faces = await fetchFacesBySid(sid);
      st.front = faces.front || img.src || null;
      st.back  = faces.back || null;
      st.name  = faces.name || null;
      st.showing = "front";

      if (st.front && img.src !== st.front) img.src = st.front;
      updateAlt(img, st.name, st.showing);

      const hasBack = !!st.back;
      btn.disabled = !hasBack;
      btn.setAttribute("aria-disabled", hasBack ? "false" : "true");
    }

    function flip() {
      if (!st.back) return;
      st.showing = st.showing === "front" ? "back" : "front";
      img.src = st.showing === "front" ? (st.front || img.src) : (st.back || img.src);
      updateAlt(img, st.name, st.showing);
      btn.setAttribute("aria-pressed", st.showing === "back" ? "true" : "false");
    }

    // Don’t let the flip click advance the carousel
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      flip();
    });

    // When the cycler changes the artwork (data-current-index), re-prepare
    const mo = new MutationObserver((list) => {
      for (const r of list) {
        if (r.type === "attributes" && r.attributeName === "data-current-index") {
          st.showing = "front";
          prepare();
          break;
        }
      }
    });
    mo.observe(img, { attributes: true, attributeFilter: ["data-current-index"] });

    // Prime once the card is on screen (don’t hammer network on load)
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries) => {
        entries.forEach(ent => {
          if (ent.isIntersecting) {
            prepare();
            io.disconnect();
          }
        });
      }, { rootMargin: "200px" });
      io.observe(container);
    } else {
      container.addEventListener("mouseenter", prepare, { once: true });
      container.addEventListener("focusin", prepare, { once: true });
    }
  }

  function init(root) {
    (root || document).querySelectorAll(".sfb-card .js-prints-cycler").forEach(wire);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init());
  } else {
    init();
  }

  // Re-bind on HTMX updates if the grid is swapped
  ["htmx:afterSwap","htmx:afterSettle","htmx:load"].forEach(ev => {
    document.addEventListener(ev, (e) => init(e && e.target));
  });

  // manual hook if you need it
  window.initSfbSmallFlip = init;
})();
