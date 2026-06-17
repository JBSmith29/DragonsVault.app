/*
 * opening-hand-automation.js
 *
 * Adds mana automation to the Opening Hand simulator without touching the
 * ~5000-line inline core script. Hooks the public API on window.__openingHand.
 *
 * Features:
 *   1. Mana model — infers what colours each land taps for (basic subtypes +
 *      oracle "Add {…}" parsing, with a forgiving "any colour" fallback).
 *   2. Auto-tap to cast — clicking a spell taps the right untapped lands to pay
 *      for it (preferring restricted sources so flexible lands stay open), and
 *      blocks casts you can't afford. Toggle with the "Auto-tap" switch.
 *   3. Auto Play — one click (or "A") plays a land and casts every affordable
 *      spell for the turn, tapping mana automatically.
 *   4. Mana HUD — live "untapped mana" readout in the hand status bar.
 *   5. Castable hints — spells you can pay for glow green; ones you can't dim.
 *
 * Everything is best-effort and defensive: a parsing miss never throws, it just
 * falls back to the most permissive interpretation so play is never blocked by
 * a quirk in the data.
 */
(function () {
  "use strict";

  const oh = window.__openingHand;
  if (!oh) return;

  const COLORS = ["W", "U", "B", "R", "G"];
  const ALL = ["W", "U", "B", "R", "G", "C"];

  const manaHud = document.getElementById("manaHud");
  const autoTapToggle = document.getElementById("autoTapToggle");
  const autoPlayBtn = document.getElementById("autoPlayBtn");
  const handGrid = document.getElementById("handGrid");
  const boardLands = document.getElementById("boardLands");

  // -----------------------------------------------------------------
  // Mana model
  // -----------------------------------------------------------------

  const BASIC_BY_NAME = {
    plains: "W", island: "U", swamp: "B", mountain: "R", forest: "G", wastes: "C",
  };

  // What a land taps for: { count, colors:Set } where colors may hold "any".
  function landProduction(card) {
    const name = (card.name || "").toLowerCase();
    const type = (card.type_line || "").toLowerCase();
    const text = (card.oracle_text || "").toLowerCase();
    const colors = new Set();
    let count = 1;

    Object.keys(BASIC_BY_NAME).forEach((basic) => {
      if (name.includes(basic) || type.includes(basic)) colors.add(BASIC_BY_NAME[basic]);
    });

    if (/any\s+(one\s+)?colou?r|mana of any/.test(text)) colors.add("any");

    // Parse every "add {…}" clause; track the largest run of symbols so that
    // bounce/ramp lands ("Add {C}{C}") report producing 2 mana.
    const addRe = /add ((?:\{[^}]+\}|\sand\s|\sor\s|,|\sof\s|\smana\s|\sone\s|\stwo\s|\sthree\s)+)/g;
    let match;
    let maxRun = 0;
    while ((match = addRe.exec(text)) !== null) {
      const symbols = match[1].match(/\{[^}]+\}/g) || [];
      let run = 0;
      symbols.forEach((sym) => {
        const inner = sym.slice(1, -1).toUpperCase();
        if (ALL.indexOf(inner) !== -1) {
          colors.add(inner);
          run += 1;
        } else if (/^\d+$/.test(inner)) {
          run += parseInt(inner, 10);
        }
      });
      if (run > maxRun) maxRun = run;
    }
    if (maxRun > 1) count = maxRun;

    // Unknown producer (utility/man-land/etc.): assume one mana of any colour so
    // the simulator stays permissive rather than wrongly blocking a cast.
    if (colors.size === 0) colors.add("any");

    return { count: count, colors: colors };
  }

  // Parse a mana cost string ("{2}{U}{U}") into payable requirements.
  function parseCost(manaCost) {
    const tokens = (manaCost || "").match(/\{[^}]+\}/g) || [];
    const pips = { W: 0, U: 0, B: 0, R: 0, G: 0, C: 0 };
    const hybrids = [];
    let generic = 0;
    tokens.forEach((tok) => {
      const inner = tok.slice(1, -1).toUpperCase();
      if (/^\d+$/.test(inner)) {
        generic += parseInt(inner, 10);
      } else if (inner === "X" || inner === "Y" || inner === "Z") {
        // Treat variable cost as 0 for affordability.
      } else if (inner === "S") {
        generic += 1; // snow — payable by any mana here.
      } else if (pips[inner] !== undefined) {
        pips[inner] += 1;
      } else if (inner.indexOf("/") !== -1) {
        const parts = inner.split("/");
        if (parts.indexOf("P") !== -1) {
          // Phyrexian: payable with the colour or 2 life.
          hybrids.push({ options: parts.filter((p) => p !== "P"), phyrexian: true });
        } else {
          hybrids.push({ options: parts, phyrexian: false });
        }
      }
    });
    return { generic: generic, pips: pips, hybrids: hybrids };
  }

  function totalPips(cost) {
    let n = cost.generic + cost.hybrids.length;
    ALL.forEach((c) => { n += cost.pips[c]; });
    return n;
  }

  // Try to pay `cost` from the given untapped lands. Returns the list of land
  // uids to tap, or null if it can't be paid.
  function payCost(cost, lands) {
    const sources = [];
    (lands || []).forEach((land) => {
      if (!land || land.tapped) return;
      const prod = landProduction(land);
      for (let i = 0; i < prod.count; i += 1) {
        sources.push({ uid: land.__uid, colors: prod.colors });
      }
    });
    const used = new Array(sources.length).fill(false);

    const canMake = (src, color) =>
      src.colors.has("any") ? color !== "C" : src.colors.has(color);

    // Take the most restricted matching source so flexible lands stay available.
    const take = (predicate) => {
      let best = -1;
      let bestScore = Infinity;
      for (let i = 0; i < sources.length; i += 1) {
        if (used[i] || !predicate(sources[i])) continue;
        const score = sources[i].colors.has("any") ? 99 : sources[i].colors.size;
        if (score < bestScore) {
          bestScore = score;
          best = i;
        }
      }
      if (best < 0) return false;
      used[best] = true;
      return true;
    };

    // Specific colour + colourless pips first.
    for (let i = 0; i < ALL.length; i += 1) {
      const color = ALL[i];
      for (let n = 0; n < cost.pips[color]; n += 1) {
        if (!take((s) => canMake(s, color))) return null;
      }
    }

    // Hybrid / Phyrexian pips.
    let extraGeneric = 0;
    for (let h = 0; h < cost.hybrids.length; h += 1) {
      const hy = cost.hybrids[h];
      const colorOpts = hy.options.filter((o) => ALL.indexOf(o) !== -1);
      const numericOpt = hy.options.filter((o) => /^\d+$/.test(o))[0];
      const paid = colorOpts.length
        ? take((s) => colorOpts.some((c) => canMake(s, c)))
        : false;
      if (!paid) {
        if (hy.phyrexian) continue; // pay 2 life instead.
        if (numericOpt) { extraGeneric += parseInt(numericOpt, 10); continue; }
        return null;
      }
    }

    // Generic (any leftover source works).
    const generic = cost.generic + extraGeneric;
    for (let n = 0; n < generic; n += 1) {
      if (!take(() => true)) return null;
    }

    const tapped = new Set();
    sources.forEach((s, i) => { if (used[i]) tapped.add(s.uid); });
    return Array.from(tapped);
  }

  function untappedLands() {
    return (oh.boardState.lands || []).filter((l) => l && !l.tapped);
  }

  function availableMana() {
    const lands = untappedLands();
    const colorCounts = { W: 0, U: 0, B: 0, R: 0, G: 0, C: 0 };
    const distinct = new Set();
    let total = 0;
    let anyCount = 0;
    lands.forEach((land) => {
      const prod = landProduction(land);
      total += prod.count;
      prod.colors.forEach((c) => {
        if (c === "any") {
          anyCount += prod.count;
          COLORS.forEach((col) => distinct.add(col));
        } else {
          colorCounts[c] += prod.count;
          distinct.add(c);
        }
      });
    });
    return { total: total, colorCounts: colorCounts, anyCount: anyCount, distinctColors: distinct };
  }

  // -----------------------------------------------------------------
  // UI: mana HUD + castable hints
  // -----------------------------------------------------------------

  function refreshHud() {
    if (!manaHud) return;
    const anyLands = (oh.boardState.lands || []).length > 0;
    if (!anyLands) {
      manaHud.hidden = true;
      manaHud.textContent = "";
      return;
    }
    const av = availableMana();
    const frag = document.createDocumentFragment();
    const total = document.createElement("span");
    total.className = "mana-hud-total";
    total.textContent = String(av.total);
    frag.appendChild(total);
    const label = document.createElement("span");
    label.textContent = "mana";
    frag.appendChild(label);
    ALL.forEach((c) => {
      if (av.colorCounts[c] > 0) {
        const pip = document.createElement("span");
        pip.className = "mana-pip mana-pip-" + c;
        pip.textContent = String(av.colorCounts[c]);
        frag.appendChild(pip);
      }
    });
    if (av.anyCount > 0) {
      const pip = document.createElement("span");
      pip.className = "mana-pip mana-pip-any";
      pip.textContent = String(av.anyCount);
      pip.title = "Lands that can make any colour";
      frag.appendChild(pip);
    }
    manaHud.textContent = "";
    manaHud.appendChild(frag);
    manaHud.hidden = false;
  }

  function refreshHandHighlights() {
    if (!handGrid) return;
    const els = Array.from(handGrid.querySelectorAll(".hand-card"));
    const lands = untappedLands();
    const hasLands = (oh.boardState.lands || []).length > 0;
    els.forEach((el, idx) => {
      el.classList.remove("oh-castable", "oh-uncastable");
      const card = oh.handCards[idx];
      if (!card || card.is_land) return;
      const cost = parseCost(card.mana_cost);
      if (payCost(cost, lands) !== null) {
        el.classList.add("oh-castable");
      } else if (hasLands) {
        el.classList.add("oh-uncastable");
      }
    });
  }

  let refreshQueued = false;
  function scheduleRefresh() {
    if (refreshQueued) return;
    refreshQueued = true;
    window.requestAnimationFrame(() => {
      refreshQueued = false;
      try { refreshHud(); refreshHandHighlights(); } catch (_) { /* never break the page */ }
    });
  }

  // -----------------------------------------------------------------
  // Casting
  // -----------------------------------------------------------------

  function tapLands(uids) {
    const set = new Set(uids);
    (oh.boardState.lands || []).forEach((l) => { if (l && set.has(l.__uid)) l.tapped = true; });
  }

  // Programmatic cast (used by Auto Play). The manual click path taps lands then
  // lets the core script's own click handler play the card.
  async function castSpell(card, opts) {
    opts = opts || {};
    const cost = parseCost(card.mana_cost);
    const toTap = payCost(cost, untappedLands());
    if (toTap === null) {
      if (!opts.silent) oh.showMessage("Not enough untapped mana to cast " + (card.name || "that spell") + ".", "warning");
      return false;
    }
    tapLands(toTap);
    const idx = oh.handCards.indexOf(card);
    if (idx >= 0) oh.handCards.splice(idx, 1);
    oh.renderHand();
    try {
      await oh.playCardFromHand(card);
    } catch (_) { /* keep going */ }
    if (!opts.silent) {
      const n = toTap.length;
      oh.showMessage("Tapped " + n + " land" + (n !== 1 ? "s" : "") + " to cast " + (card.name || "spell") + ".", "success");
    }
    scheduleRefresh();
    return true;
  }

  // -----------------------------------------------------------------
  // Auto-tap on manual click (capture phase, before the core handler)
  // -----------------------------------------------------------------
  if (handGrid) {
    handGrid.addEventListener(
      "click",
      (event) => {
        if (event.button !== 0) return;
        if (!autoTapToggle || !autoTapToggle.checked) return;
        const cardEl = event.target.closest(".hand-card");
        if (!cardEl) return;
        const els = Array.from(handGrid.querySelectorAll(".hand-card"));
        const idx = els.indexOf(cardEl);
        if (idx < 0 || idx >= oh.handCards.length) return;
        const card = oh.handCards[idx];
        if (!card || card.is_land) return; // lands handled by the enhancements module.

        const toTap = payCost(parseCost(card.mana_cost), untappedLands());
        if (toTap === null) {
          // Block the cast: stop the core click handler from playing it.
          event.preventDefault();
          event.stopImmediatePropagation();
          oh.showMessage("Not enough untapped mana to cast " + (card.name || "that spell") + ".", "warning");
          return;
        }
        // Affordable: pay now, then let the core handler play the card normally.
        tapLands(toTap);
        const count = toTap.length;
        const name = card.name || "spell";
        window.setTimeout(() => {
          oh.showMessage("Tapped " + count + " land" + (count !== 1 ? "s" : "") + " to cast " + name + ".", "success");
          scheduleRefresh();
        }, 30);
      },
      true
    );
  }

  // -----------------------------------------------------------------
  // Auto Play — play a land, then cast everything affordable
  // -----------------------------------------------------------------

  function pickBestLand() {
    const lands = (oh.handCards || []).filter((c) => c && c.is_land);
    if (!lands.length) return null;
    const have = availableMana().distinctColors;
    let best = null;
    let bestScore = -Infinity;
    lands.forEach((land) => {
      const prod = landProduction(land);
      let newColors = 0;
      prod.colors.forEach((c) => {
        if (c === "any") { if (have.size < 5) newColors += 1; }
        else if (!have.has(c)) newColors += 1;
      });
      const entersTapped = oh.cardEntersTapped ? oh.cardEntersTapped(land) : false;
      const score = newColors * 2 + (entersTapped ? 0 : 1);
      if (score > bestScore) { bestScore = score; best = land; }
    });
    return best;
  }

  function pickBestCastable() {
    const lands = untappedLands();
    let best = null;
    let bestValue = -Infinity;
    (oh.handCards || []).forEach((card) => {
      if (!card || card.is_land) return;
      const cost = parseCost(card.mana_cost);
      if (payCost(cost, lands) === null) return;
      const value = typeof card.mana_value === "number" ? card.mana_value : totalPips(cost);
      if (value > bestValue) { bestValue = value; best = card; }
    });
    return best;
  }

  let autoPlayBusy = false;
  async function autoPlayTurn() {
    if (autoPlayBusy) return;
    if (!oh.stateInput || !oh.stateInput.value) {
      oh.showMessage("Shuffle a deck first.", "warning");
      return;
    }
    autoPlayBusy = true;
    if (autoPlayBtn) autoPlayBtn.disabled = true;
    try {
      const cast = [];
      let playedLand = null;

      if ((oh.landsPlayedThisTurn || 0) < 1) {
        const land = pickBestLand();
        if (land) {
          const idx = oh.handCards.indexOf(land);
          if (idx >= 0) oh.handCards.splice(idx, 1);
          oh.moveCardToBoard(land, "lands");
          oh.landsPlayedThisTurn = (oh.landsPlayedThisTurn || 0) + 1;
          oh.renderHand();
          playedLand = land.name || "a land";
        }
      }

      let guard = 0;
      while (guard < 50) {
        guard += 1;
        const spell = pickBestCastable();
        if (!spell) break;
        const ok = await castSpell(spell, { silent: true });
        if (!ok) break;
        cast.push(spell.name || "spell");
      }

      scheduleRefresh();
      if (!playedLand && !cast.length) {
        oh.showMessage("Auto Play: no land to play and nothing affordable to cast.", "info");
      } else {
        const bits = [];
        if (playedLand) bits.push("played " + playedLand);
        if (cast.length) bits.push("cast " + cast.join(", "));
        oh.showMessage("Auto Play — " + bits.join("; ") + ".", "success");
      }
    } finally {
      autoPlayBusy = false;
      if (autoPlayBtn) autoPlayBtn.disabled = false;
    }
  }

  if (autoPlayBtn) {
    autoPlayBtn.addEventListener("click", autoPlayTurn);
  }

  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key.toLowerCase() === "a") {
      event.preventDefault();
      autoPlayTurn();
    }
  });

  // -----------------------------------------------------------------
  // Keep the HUD + hints fresh as the board/hand change
  // -----------------------------------------------------------------
  if (window.MutationObserver) {
    const observer = new MutationObserver(scheduleRefresh);
    if (handGrid) observer.observe(handGrid, { childList: true, subtree: true });
    if (boardLands) observer.observe(boardLands, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  }

  scheduleRefresh();
})();
