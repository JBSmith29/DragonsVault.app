/* DragonsVault — face-flip.js (two-arrows only; grid-safe) */
(function () {
  const facesCache = new Map();
  const printsCache = new Map();

  const pick = u => (u && (u.large || u.normal || u.small || u.png)) || null;
  const j = async (url, opts) => { try { const r = await fetch(url, opts||{}); if(!r.ok) throw 0; return await r.json(); } catch { return null; } };

  async function facesLocal(sid){
    const d = await j(`/api/print/${encodeURIComponent(sid)}/faces`, {credentials:"same-origin"});
    if (!d) return null;
    // accept either {faces:[...]} or {front,back}
    if (Array.isArray(d.faces)) {
      const f = d.faces;
      return { front: pick(f[0]||{}), back: pick(f[1]||{}), name: d.name || null };
    }
    return { front: d.front||null, back: d.back||null, name: d.name||null };
  }
  async function facesScryfall(sid){
    const d = await j(`https://api.scryfall.com/cards/${encodeURIComponent(sid)}`);
    if (!d) return null;
    const f = Array.isArray(d.card_faces)? d.card_faces : [];
    return { front: pick((f[0]&&f[0].image_uris)||d.image_uris||{}),
             back:  pick((f[1]&&f[1].image_uris)||{}),
             name:  d.name||null };
  }
  async function ensureFacesBySid(sid){
    if (!sid) return {front:null,back:null,name:null};
    if (facesCache.has(sid)) return facesCache.get(sid);
    const viaLocal = await facesLocal(sid);
    const out = viaLocal || await facesScryfall(sid) || {front:null,back:null,name:null};
    facesCache.set(sid,out); return out;
  }

  async function allPrints(uri){
    if (!uri) return [];
    if (printsCache.has(uri)) return printsCache.get(uri);
    const out=[]; let url=uri, guard=0;
    while(url && guard<12){
      guard++;
      const d = await j(url); if(!d) break;
      out.push(...(Array.isArray(d.data)?d.data:[]));
      url = d.has_more ? d.next_page : null;
    }
    printsCache.set(uri,out); return out;
  }

  const pickImg = c =>
    c.querySelector("img.js-face-target") ||
    c.querySelector("img.sfb-main") ||
    c.querySelector("#artworkCarousel .carousel-item.active img") ||
    c.querySelector("img.card-img-top") ||
    c.querySelector("img");

  function getFlipBtn(container){
    container.querySelectorAll(".flip-face").forEach(n => n.remove()); // legacy
    const btn = container.querySelector(":scope > .cycle-flip, .cycle-flip");
    if (!btn) return null;
    if (!btn.textContent.trim()) btn.textContent = "⇄";
    btn.setAttribute("title","Flip face");
    btn.setAttribute("aria-label","Flip face");
    return btn;
  }

  function setAlt(img, name, showing){
    const base = (name || img.alt || "").replace(/\s+(—|-)\s+(front|back)$/i, "").trim();
    img.alt = base ? `${base} — ${showing}` : `Card — ${showing}`;
  }

  function wire(container){
    if (!container || container.dataset.flipBound === "1") return;

    // ✂️ never show flip on grid cards
    if (container.closest(".sfb-card")) {
      container.querySelectorAll(".cycle-flip, .flip-face").forEach(n => n.remove());
      container.dataset.flipBound = "1";
      return;
    }

    container.dataset.flipBound = "1";
    const img = pickImg(container);
    const btn = getFlipBtn(container);
    if (!img || !btn) return;

    const sidAttr   = container.dataset.sid || img.dataset.sid || "";
    const printsUri = container.dataset.printsUri || "";
    const oracleId  = container.dataset.oracleId || img.dataset.oracleId || "";

    const st = { sid: sidAttr || null, front:null, back:null, name:null, showing:"front" };

    function showBtn(hasBack){
      btn.disabled = !hasBack;
      btn.setAttribute("aria-disabled", hasBack ? "false":"true");
      btn.style.display = hasBack ? "" : "none";
    }

    async function currentSid(){
      if (st.sid) return st.sid;
      if (!printsUri) return null;
      const arr = await allPrints(printsUri);
      const idx = parseInt(img.getAttribute("data-current-index") || "0", 10) || 0;
      const pr = arr[Math.max(0, Math.min(idx, (arr.length||1)-1))];
      return pr && pr.id ? pr.id : null;
    }

    async function prepare(){
      const sid = await currentSid();
      if (!sid && !oracleId){ showBtn(false); return; }

      const faces = sid ? await ensureFacesBySid(sid)
                        : await (async ()=>{
                            const q = await j(`https://api.scryfall.com/cards/search?order=released&q=${encodeURIComponent('oracleid:'+oracleId)}&unique=prints`);
                            const p = q && Array.isArray(q.data) && q.data[0];
                            return p ? await ensureFacesBySid(p.id) : {front:null,back:null,name:null};
                          })();

      st.sid = sid || st.sid;
      st.front = faces.front || img.src || null;
      st.back  = faces.back  || null;
      st.name  = faces.name  || null;
      st.showing = "front";

      if (st.front && img.src !== st.front) img.src = st.front;
      setAlt(img, st.name, st.showing);
      showBtn(!!st.back);
    }

    function flip(){
      if (!st.back) return;
      st.showing = (st.showing === "front") ? "back" : "front";
      img.src = (st.showing === "front") ? (st.front || img.src) : (st.back || img.src);
      setAlt(img, st.name, st.showing);
      btn.setAttribute("aria-pressed", st.showing === "back" ? "true":"false");
    }

    btn.addEventListener("click", (e)=>{ e.preventDefault(); e.stopPropagation(); flip(); });

    if (!container.hasAttribute("tabindex")) container.setAttribute("tabindex","0");
    container.addEventListener("keydown", (e)=>{
      if ((e.key||"").toLowerCase()==="f"){ e.preventDefault(); flip(); }
    });

    const mo = new MutationObserver((m)=>{
      for (const r of m){
        if (r.type==="attributes" && r.attributeName==="data-current-index"){ st.showing="front"; prepare(); break; }
      }
    });
    mo.observe(img, {attributes:true, attributeFilter:["data-current-index"]});

    prepare();
  }

  function init(root){
    const scope = root || document;
    scope.querySelectorAll(".js-face-container").forEach(wire);
    scope.querySelectorAll(".js-prints-cycler").forEach(wire);
  }

  if (document.readyState==="loading") document.addEventListener("DOMContentLoaded", ()=>init());
  else init();

  ["htmx:afterSwap","htmx:afterSettle","htmx:load"].forEach(ev=>{
    document.addEventListener(ev, (e)=>init(e && e.target));
  });

  window.initFaceFlips = init;
})();
