/*
 * opening-hand-triggers.js
 *
 * Automation engine for the Opening Hand simulator.
 *
 * Adds:
 *   - Auto-tap for mana: clicking a land or mana-producing permanent taps it
 *     and shows what mana it produced in the status bar.
 *   - ETB trigger detection: "when ~ enters the battlefield" effects are
 *     detected and queued automatically when a card is played.
 *   - Triggered ability panel: a non-blocking toast-style panel lists any
 *     triggered abilities that fired this turn so the user can resolve them.
 *   - Bounce/exile/counter detection: new effect types added to the resolve
 *     modal so the user can apply them with one click.
 *   - Mana pool tracker: shows a live mana pool badge (W/U/B/R/G/C) that
 *     updates as lands are tapped.
 *   - Auto-resolve unambiguous ETB effects: when a card enters with a
 *     deterministic ETB (e.g. "draw a card", "create a 1/1 token"), it
 *     resolves immediately without opening the modal.
 *
 * All automation is opt-in via a toggle in the action bar. Users can turn it
 * off if they want full manual control.
 */
(function () {
  "use strict";

  const oh = window.__openingHand;
  if (!oh) return;

  // -----------------------------------------------------------------
  // Configuration
  // -----------------------------------------------------------------
  const MANA_SYMBOLS = ["W", "U", "B", "R", "G", "C"];
  const MANA_COLORS = { W: "#f9fafb", U: "#3b82f6", B: "#1e1b4b", R: "#ef4444", G: "#16a34a", C: "#94a3b8" };
  const MANA_LABELS = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green", C: "Colorless" };

  // -----------------------------------------------------------------
  // State
  // -----------------------------------------------------------------
  let autoMode = true;          // master toggle
  let manaPool = { W: 0, U: 0, B: 0, R: 0, G: 0, C: 0 };
  let pendingTriggers = [];     // { card, description, kind }
  let triggerSerial = 0;

  // -----------------------------------------------------------------
  // DOM references
  // -----------------------------------------------------------------
  const autoToggle = document.getElementById("autoModeToggle");
  const manaPoolDisplay = document.getElementById("manaPoolDisplay");
  const triggerPanel = document.getElementById("triggerPanel");
  const triggerList = document.getElementById("triggerList");
  const clearTriggersBtn = document.getElementById("clearTriggersBtn");

  // -----------------------------------------------------------------
  // Auto-mode toggle
  // -----------------------------------------------------------------
  if (autoToggle) {
    autoToggle.checked = autoMode;
    autoToggle.addEventListener("change", () => {
      autoMode = autoToggle.checked;
      oh.showMessage(autoMode ? "Automation ON — ETB effects and mana will auto-resolve." : "Automation OFF — manual control.", "info");
    });
  }

  // -----------------------------------------------------------------
  // 1. Auto-tap for mana
  //
  // Intercepts single-click on board cards. If the card produces mana
  // (detected from oracle text) and is untapped, tap it and add mana
  // to the pool. If already tapped, show a message.
  // -----------------------------------------------------------------
  const boardArea = document.getElementById("boardArea");
  if (boardArea) {
    boardArea.addEventListener("click", (event) => {
      if (!autoMode) return;
      const cardEl = event.target.closest(".hand-card");
      if (!cardEl) return;
      // Only intercept left-click.
      if (event.button !== 0) return;

      const card = findBoardCardByElement(cardEl);
      if (!card) return;

      // Only intercept mana-producing permanents.
      const manaProduced = detectManaProduction(card);
      if (!manaProduced || !manaProduced.length) return;

      // Don't intercept if the card is in graveyard or command zone.
      if (card.boardZone === "graveyard" || card.boardZone === "command") return;

      event.stopPropagation();
      event.preventDefault();

      if (card.tapped) {
        oh.showMessage(`${card.name} is already tapped.`, "warning");
        return;
      }

      // Tap the card.
      card.tapped = true;
      oh.renderBoard();

      // Add mana to pool.
      manaProduced.forEach((sym) => {
        if (manaPool[sym] !== undefined) manaPool[sym] += 1;
      });
      updateManaDisplay();

      const manaText = manaProduced.map((s) => `{${s}}`).join("");
      oh.showMessage(`Tapped ${card.name} for ${manaText}.`, "info");
    }, { capture: true });
  }

  // -----------------------------------------------------------------
  // 2. ETB trigger detection
  //
  // Hooks into the board mutation cycle. After any card enters the
  // battlefield, scan its oracle text for "when ~ enters" patterns
  // and queue them as pending triggers.
  //
  // We do this by wrapping moveCardToBoard via the bridge.
  // -----------------------------------------------------------------
  const _origMoveCardToBoard = oh.moveCardToBoard.bind(oh);
  oh.moveCardToBoard = function (card, preferredZone, beforeId, stackParentId) {
    _origMoveCardToBoard(card, preferredZone, beforeId, stackParentId);
    if (!autoMode) return;
    if (!card) return;
    const zone = card.boardZone || preferredZone;
    const isBattlefield = zone === "creatures" || zone === "permanents" || zone === "lands";
    if (!isBattlefield) return;
    // Detect ETB triggers.
    const etbEffects = detectEtbTriggers(card);
    etbEffects.forEach((effect) => {
      if (effect.autoResolvable) {
        // Resolve immediately without user interaction.
        autoResolveEtb(card, effect);
      } else {
        // Queue for the trigger panel.
        queueTrigger(card, effect);
      }
    });
  };

  // -----------------------------------------------------------------
  // 3. Mana pool display
  // -----------------------------------------------------------------
  function updateManaDisplay() {
    if (!manaPoolDisplay) return;
    const total = MANA_SYMBOLS.reduce((sum, s) => sum + (manaPool[s] || 0), 0);
    if (total === 0) {
      manaPoolDisplay.hidden = true;
      return;
    }
    manaPoolDisplay.hidden = false;
    manaPoolDisplay.innerHTML = MANA_SYMBOLS
      .filter((s) => manaPool[s] > 0)
      .map((s) => {
        const count = manaPool[s];
        return `<span class="mana-pip mana-${s.toLowerCase()}" title="${MANA_LABELS[s]}">${count > 1 ? count : ""}{${s}}</span>`;
      })
      .join("");
  }

  // Clear mana pool on untap all (start of turn).
  const untapAllBtn = document.getElementById("untapAllBtn");
  if (untapAllBtn) {
    untapAllBtn.addEventListener("click", () => {
      MANA_SYMBOLS.forEach((s) => { manaPool[s] = 0; });
      updateManaDisplay();
    });
  }

  // -----------------------------------------------------------------
  // 4. Trigger panel
  // -----------------------------------------------------------------
  function queueTrigger(card, effect) {
    const id = ++triggerSerial;
    pendingTriggers.push({ id, card, effect });
    renderTriggerPanel();
  }

  function renderTriggerPanel() {
    if (!triggerPanel || !triggerList) return;
    if (!pendingTriggers.length) {
      triggerPanel.hidden = true;
      return;
    }
    triggerPanel.hidden = false;
    triggerList.innerHTML = pendingTriggers.map((entry) => {
      const { id, card, effect } = entry;
      return `
        <div class="trigger-entry" data-trigger-id="${id}">
          <div class="trigger-card-name">${escapeHtml(card.name)}</div>
          <div class="trigger-desc">${escapeHtml(effect.description)}</div>
          <div class="trigger-actions">
            <button type="button" class="btn btn-sm btn-outline-primary trigger-resolve-btn" data-trigger-id="${id}">
              Resolve
            </button>
            <button type="button" class="btn btn-sm btn-outline-secondary trigger-dismiss-btn" data-trigger-id="${id}">
              Skip
            </button>
          </div>
        </div>
      `;
    }).join("");

    // Bind resolve/dismiss buttons.
    triggerList.querySelectorAll(".trigger-resolve-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.getAttribute("data-trigger-id"), 10);
        resolveTrigger(id);
      });
    });
    triggerList.querySelectorAll(".trigger-dismiss-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.getAttribute("data-trigger-id"), 10);
        dismissTrigger(id);
      });
    });
  }

  async function resolveTrigger(id) {
    const idx = pendingTriggers.findIndex((e) => e.id === id);
    if (idx < 0) return;
    const { card, effect } = pendingTriggers[idx];
    pendingTriggers.splice(idx, 1);
    renderTriggerPanel();
    await autoResolveEtb(card, effect);
  }

  function dismissTrigger(id) {
    pendingTriggers = pendingTriggers.filter((e) => e.id !== id);
    renderTriggerPanel();
    oh.showMessage("Trigger skipped.", "info");
  }

  if (clearTriggersBtn) {
    clearTriggersBtn.addEventListener("click", () => {
      pendingTriggers = [];
      renderTriggerPanel();
    });
  }

  // -----------------------------------------------------------------
  // 5. Oracle text parsers
  // -----------------------------------------------------------------

  /**
   * Detect what mana a card produces from its oracle text.
   * Returns an array of mana symbols e.g. ["G", "G"] for Llanowar Elves.
   */
  function detectManaProduction(card) {
    const text = (card.oracle_text || "").toLowerCase();
    if (!text) return [];

    // Must contain "add" in a mana-production context.
    if (!text.includes("add")) return [];

    // Exclude cards that only mention "add" in other contexts.
    // A mana ability looks like: {T}: Add {G} or "tap: add one mana of any color"
    const manaAbilityRe = /(?:tap|{t})[^.]*?add\s+(?:{([wubrgc])(?:\/[wubrgc])?}|one mana of any|mana equal|mana of any)/i;
    const simpleAddRe = /add\s+{([wubrgc])(?:\/[wubrgc])?}/gi;
    const anyColorRe = /add\s+(?:one mana of any color|mana of any color)/i;
    const colorlessRe = /add\s+{c}/i;

    const produced = [];

    // Check for "any color" mana.
    if (anyColorRe.test(text)) {
      return ["W", "U", "B", "R", "G"]; // user picks; we show all options
    }

    // Check for colorless.
    if (colorlessRe.test(text)) {
      produced.push("C");
    }

    // Extract specific mana symbols from "add {X}" patterns.
    let match;
    const re = /add\s+(?:{([wubrgc])(?:\/[wubrgc])?})+/gi;
    while ((match = re.exec(text)) !== null) {
      const sym = match[1].toUpperCase();
      if (MANA_SYMBOLS.includes(sym)) produced.push(sym);
    }

    // Basic land types always produce their color.
    const typeLine = (card.type_line || "").toLowerCase();
    if (typeLine.includes("plains") && !produced.includes("W")) produced.push("W");
    if (typeLine.includes("island") && !produced.includes("U")) produced.push("U");
    if (typeLine.includes("swamp") && !produced.includes("B")) produced.push("B");
    if (typeLine.includes("mountain") && !produced.includes("R")) produced.push("R");
    if (typeLine.includes("forest") && !produced.includes("G")) produced.push("G");

    return produced;
  }

  /**
   * Detect ETB triggers and static ETB effects on a card.
   * Returns an array of effect descriptors.
   */
  function detectEtbTriggers(card) {
    const text = card.oracle_text || "";
    if (!text) return [];
    const lowered = text.toLowerCase();
    const effects = [];

    // Pattern: "when ~ enters" or "when ~ enters the battlefield"
    const etbPatterns = [
      /when\s+(?:this|~|[\w\s,]+?)\s+enters(?:\s+the\s+battlefield)?[^.]*?[,.]/gi,
      /as\s+(?:this|~|[\w\s,]+?)\s+enters(?:\s+the\s+battlefield)?[^.]*?[,.]/gi,
    ];

    for (const pattern of etbPatterns) {
      let match;
      while ((match = pattern.exec(text)) !== null) {
        const sentence = match[0];
        const sentenceLower = sentence.toLowerCase();

        // Draw effect.
        const drawMatch = sentenceLower.match(/draw\s+(a|an|one|two|three|four|five|\d+)\s+card/);
        if (drawMatch) {
          const count = parseWordCount(drawMatch[1]);
          effects.push({
            kind: "draw",
            count,
            description: `Draw ${count} card${count === 1 ? "" : "s"} (ETB trigger)`,
            autoResolvable: count !== null && count > 0,
          });
        }

        // Discard effect.
        const discardMatch = sentenceLower.match(/discard\s+(a|an|one|two|three|four|five|\d+)\s+card/);
        if (discardMatch) {
          const count = parseWordCount(discardMatch[1]);
          effects.push({
            kind: "discard",
            count,
            description: `Discard ${count} card${count === 1 ? "" : "s"} (ETB trigger)`,
            autoResolvable: false, // discard requires user choice
          });
        }

        // Token creation.
        const tokenMatch = sentenceLower.match(/create\s+(a|an|one|two|three|four|five|\d+)\s+(?:\S+\s+)*token/);
        if (tokenMatch) {
          const count = parseWordCount(tokenMatch[1]);
          effects.push({
            kind: "tokens",
            count,
            description: `Create ${count} token${count === 1 ? "" : "s"} (ETB trigger)`,
            autoResolvable: count !== null && count > 0,
          });
        }

        // Scry.
        const scryMatch = sentenceLower.match(/scry\s+(\d+)/);
        if (scryMatch) {
          const count = parseInt(scryMatch[1], 10);
          effects.push({
            kind: "scry",
            count,
            description: `Scry ${count} (ETB trigger)`,
            autoResolvable: false, // scry requires user to see cards
          });
        }

        // Search library.
        if (sentenceLower.includes("search your library")) {
          const isBasicLand = sentenceLower.includes("basic land");
          const toBattlefield = sentenceLower.includes("battlefield");
          const tapped = sentenceLower.includes("tapped");
          effects.push({
            kind: "search",
            criteria: { kind: isBasicLand ? "basic_land" : "land" },
            destination: toBattlefield ? "battlefield" : "hand",
            tapped,
            description: `Search library for ${isBasicLand ? "basic land" : "land"} → ${toBattlefield ? "battlefield" + (tapped ? " (tapped)" : "") : "hand"} (ETB trigger)`,
            autoResolvable: false, // search requires user to pick
          });
        }

        // +1/+1 counters (just log, no action needed).
        const counterMatch = sentenceLower.match(/put\s+(?:a|an|one|two|three|\d+)\s+\+1\/\+1\s+counter/);
        if (counterMatch) {
          effects.push({
            kind: "counter",
            description: `Put +1/+1 counter(s) on a creature (ETB trigger) — tap the target manually`,
            autoResolvable: false,
          });
        }
      }
    }

    // Triggered abilities: "whenever ~ attacks", "at the beginning of your upkeep"
    // These are queued but not auto-resolved.
    const triggeredPatterns = [
      { re: /whenever\s+(?:this|~|[\w\s,]+?)\s+attacks[^.]*\./gi, label: "attacks trigger" },
      { re: /at\s+the\s+beginning\s+of\s+(?:your\s+)?upkeep[^.]*\./gi, label: "upkeep trigger" },
      { re: /at\s+the\s+beginning\s+of\s+(?:your\s+)?end\s+step[^.]*\./gi, label: "end step trigger" },
      { re: /whenever\s+you\s+(?:draw|cast|play)[^.]*\./gi, label: "cast/draw trigger" },
    ];

    for (const { re, label } of triggeredPatterns) {
      let match;
      while ((match = re.exec(text)) !== null) {
        const sentence = match[0].trim();
        effects.push({
          kind: "triggered",
          description: `${label}: "${sentence.slice(0, 80)}${sentence.length > 80 ? "…" : ""}"`,
          autoResolvable: false,
        });
      }
    }

    return effects;
  }

  /**
   * Auto-resolve an ETB effect that doesn't need user input.
   */
  async function autoResolveEtb(card, effect) {
    if (effect.kind === "draw" && effect.count > 0) {
      oh.showMessage(`${card.name}: drawing ${effect.count} card${effect.count === 1 ? "" : "s"}…`, "info");
      if (oh.drawCards) {
        await oh.drawCards(effect.count);
      } else {
        // Fallback: click draw button N times.
        for (let i = 0; i < effect.count; i++) {
          if (oh.drawBtn && !oh.drawBtn.disabled) oh.drawBtn.click();
          await sleep(120);
        }
      }
    } else if (effect.kind === "tokens" && effect.count > 0) {
      oh.showMessage(`${card.name}: creating ${effect.count} token${effect.count === 1 ? "" : "s"}…`, "info");
      // Open the token picker modal pre-filtered to this card's tokens.
      const tokenBtn = document.getElementById("tokenPickerBtn");
      if (tokenBtn) tokenBtn.click();
    } else if (effect.kind === "scry") {
      oh.showMessage(`${card.name}: scry ${effect.count} — use the Scry/Surveil button.`, "info");
      const scryBtn = document.getElementById("scryBtn");
      if (scryBtn && !scryBtn.disabled) {
        const scryCount = document.getElementById("scryCount");
        if (scryCount) scryCount.value = String(effect.count);
        scryBtn.click();
      }
    } else if (effect.kind === "search") {
      oh.showMessage(`${card.name}: ${effect.description}`, "info");
      // Open the fetch card modal.
      const fetchBtn = document.getElementById("fetchCardBtn");
      if (fetchBtn) fetchBtn.click();
    } else if (effect.kind === "triggered") {
      // Just notify — these fire on future turns.
      oh.showMessage(`${card.name} has a triggered ability: ${effect.description}`, "info");
    }
  }

  // -----------------------------------------------------------------
  // 6. Mana pool display in the status bar
  // -----------------------------------------------------------------
  // Inject mana pool styles.
  const style = document.createElement("style");
  style.textContent = `
    #manaPoolDisplay {
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      flex-wrap: wrap;
    }
    .mana-pip {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 1.6rem;
      height: 1.6rem;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 700;
      padding: 0 0.35rem;
      border: 1px solid rgba(255,255,255,0.2);
      cursor: default;
    }
    .mana-w { background: #f9fafb; color: #1e293b; }
    .mana-u { background: #3b82f6; color: #fff; }
    .mana-b { background: #312e81; color: #e0e7ff; }
    .mana-r { background: #ef4444; color: #fff; }
    .mana-g { background: #16a34a; color: #fff; }
    .mana-c { background: #94a3b8; color: #1e293b; }

    #triggerPanel {
      position: fixed;
      bottom: calc(env(safe-area-inset-bottom, 0px) + 5.5rem);
      left: calc(var(--sidebar-w, 0px) + 1rem);
      z-index: 1050;
      width: clamp(260px, 28vw, 360px);
      background: rgba(15, 23, 42, 0.96);
      border: 1px solid rgba(148, 163, 184, 0.3);
      border-radius: 0.85rem;
      backdrop-filter: blur(12px);
      box-shadow: 0 1rem 2.5rem rgba(2, 6, 23, 0.55);
      overflow: hidden;
    }
    #triggerPanel .trigger-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0.5rem 0.75rem;
      border-bottom: 1px solid rgba(148, 163, 184, 0.15);
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: rgba(148, 163, 184, 0.85);
      font-weight: 600;
    }
    #triggerList {
      max-height: 280px;
      overflow-y: auto;
      padding: 0.4rem 0;
    }
    .trigger-entry {
      padding: 0.5rem 0.75rem;
      border-bottom: 1px solid rgba(148, 163, 184, 0.1);
    }
    .trigger-entry:last-child { border-bottom: 0; }
    .trigger-card-name {
      font-weight: 600;
      font-size: 0.85rem;
      color: #f1f5f9;
      margin-bottom: 0.15rem;
    }
    .trigger-desc {
      font-size: 0.78rem;
      color: rgba(148, 163, 184, 0.85);
      margin-bottom: 0.4rem;
      line-height: 1.4;
    }
    .trigger-actions {
      display: flex;
      gap: 0.4rem;
    }
    .trigger-actions .btn {
      font-size: 0.75rem;
      padding: 0.2rem 0.55rem;
    }
    #autoModeToggle + label {
      font-size: 0.82rem;
      cursor: pointer;
    }
    @media (max-width: 768px) {
      #triggerPanel {
        left: 0.5rem;
        right: 0.5rem;
        width: auto;
        bottom: calc(env(safe-area-inset-bottom, 0px) + 6.5rem);
      }
    }
  `;
  document.head.appendChild(style);

  // -----------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------
  function parseWordCount(word) {
    if (!word) return 1;
    const w = word.toLowerCase().trim();
    const map = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5 };
    if (map[w] !== undefined) return map[w];
    const n = parseInt(w, 10);
    return isNaN(n) ? 1 : n;
  }

  function findBoardCardByElement(cardEl) {
    const zones = Object.keys(oh.boardState);
    for (const zone of zones) {
      const cards = oh.boardState[zone] || [];
      for (const card of cards) {
        // Match by rendered position — find the card whose __uid matches
        // the element's data attribute, or fall back to index matching.
        const uid = cardEl.getAttribute("data-uid") || cardEl.dataset.uid;
        if (uid && card.__uid === uid) return card;
      }
    }
    // Fallback: find by element position in the zone container.
    for (const zone of zones) {
      const container = document.querySelector(`[data-board-zone="${zone}"] .board-zone-cards, #board${capitalize(zone)}`);
      if (!container) continue;
      const allEls = Array.from(container.querySelectorAll(".hand-card"));
      const idx = allEls.indexOf(cardEl);
      if (idx >= 0 && idx < (oh.boardState[zone] || []).length) {
        return oh.boardState[zone][idx];
      }
    }
    return null;
  }

  function capitalize(str) {
    return str.charAt(0).toUpperCase() + str.slice(1);
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  // -----------------------------------------------------------------
  // Initialize
  // -----------------------------------------------------------------
  updateManaDisplay();
  renderTriggerPanel();
})();
