/*
 * opening-hand-automation.js
 *
 * Adds mana automation to the Opening Hand simulator without touching the
 * ~5000-line inline core script. Hooks the public API on window.__openingHand.
 *
 * Scope: this module assists the *player's* decisions — it never plays the turn
 * for you. You decide what to play; it handles the bookkeeping:
 *   1. Mana model — infers what colours each land taps for (basic subtypes +
 *      oracle "Add {…}" parsing, with a forgiving "any colour" fallback).
 *   2. Auto-tap to cast — when you click a spell, it taps the right untapped
 *      lands to pay for it (preferring restricted sources so flexible lands stay
 *      open), then lets the core play it (whose Auto mode auto-resolves fetches,
 *      tokens, draws and other ETB effects). Casts you can't afford are blocked.
 *      Toggle with the "Auto-tap" switch.
 *   3. Mana HUD — live "untapped mana" readout in the hand status bar.
 *   4. Castable hints — spells you can pay for glow green; ones you can't dim.
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

    // Produced mana = the largest single option after "add" (options are
    // separated by "," / "or", so "Add {C}{C}, {C}{W}, or {W}{W}" makes 2, not
    // 6) and also seeds the colour set with everything it could make.
    let produced = 0;
    let match;
    const addRe = /add\s+([^.;]*)/gi;
    while ((match = addRe.exec(text)) !== null) {
      match[1].split(/,|\bor\b/).forEach((option) => {
        let optionCount = 0;
        (option.match(/\{[^}]+\}/g) || []).forEach((sym) => {
          const inner = sym.slice(1, -1).toUpperCase();
          if (ALL.indexOf(inner) !== -1) {
            colors.add(inner);
            optionCount += 1;
          } else if (/^\d+$/.test(inner)) {
            optionCount += parseInt(inner, 10);
          }
        });
        if (optionCount > produced) produced = optionCount;
      });
    }

    // Mana paid to activate the ability (filter lands: "{1}, {T}: Add {C}{C}"
    // taps for 2 but costs 1, so it nets only 1). Sum mana symbols before the
    // ": add" colon, ignoring {T}/{Q}/{E}.
    let paid = 0;
    const costRe = /([^.;]*):\s*add\b/gi;
    let costMatch;
    while ((costMatch = costRe.exec(text)) !== null) {
      let clausePaid = 0;
      (costMatch[1].match(/\{[^}]+\}/g) || []).forEach((sym) => {
        const inner = sym.slice(1, -1).toUpperCase();
        if (/^\d+$/.test(inner)) clausePaid += parseInt(inner, 10);
        else if (ALL.indexOf(inner) !== -1) clausePaid += 1;
      });
      if (clausePaid > paid) paid = clausePaid;
    }

    const net = produced - paid;
    if (net > 1) count = net;

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

  // -----------------------------------------------------------------
  // Auto-tap on manual click (capture phase). We own the click: tap mana when
  // we can, then play the card ourselves so it always reaches the battlefield.
  // -----------------------------------------------------------------
  if (handGrid) {
    handGrid.addEventListener(
      "click",
      async (event) => {
        if (event.button !== 0) return;
        if (!autoTapToggle || !autoTapToggle.checked) return;
        const cardEl = event.target.closest(".hand-card");
        if (!cardEl) return;
        // Let action/flip buttons on the card do their own thing.
        if (event.target.closest(".card-action-btn, .card-flip-btn")) return;
        const els = Array.from(handGrid.querySelectorAll(".hand-card"));
        const idx = els.indexOf(cardEl);
        if (idx < 0 || idx >= oh.handCards.length) return;
        const card = oh.handCards[idx];
        if (!card || card.is_land) return; // lands handled by the enhancements module.

        // Take ownership of this click so the core handler doesn't also play it.
        event.preventDefault();
        event.stopImmediatePropagation();

        const toTap = payCost(parseCost(card.mana_cost), untappedLands());
        if (toTap && toTap.length) tapLands(toTap);

        // Remove from hand and play to its preferred zone (same path the core
        // uses), so ETB effects, attach prompts, and zone routing all run.
        const handIdx = oh.handCards.indexOf(card);
        if (handIdx >= 0) oh.handCards.splice(handIdx, 1);
        oh.renderHand();
        try {
          await oh.playCardFromHand(card);
        } catch (_) { /* keep the UI responsive */ }

        const name = card.name || "card";
        if (toTap === null) {
          oh.showMessage("Played " + name + " — not enough untapped mana, so no lands were tapped.", "info");
        } else if (toTap.length) {
          oh.showMessage("Tapped " + toTap.length + " land" + (toTap.length !== 1 ? "s" : "") + " to cast " + name + ".", "success");
        } else {
          oh.showMessage("Played " + name + ".", "success");
        }
        scheduleRefresh();
      },
      true
    );
  }

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
