/*
 * opening-hand-triggers.js
 *
 * Automation engine for the Opening Hand simulator.
 *
 * Fixes in this version:
 *   - Mana pool clears on Next Turn (untapAllBtn click fires the listener)
 *   - "Any color" mana shows a color-picker instead of adding all 5 at once
 *   - discard ETB effect now opens the discard modal correctly
 *   - findBoardCardByElement uses data-cardId attribute (reliable)
 *   - Dead variables removed from detectManaProduction
 *   - Trigger panel has count badge and fade transition
 *   - "Clear all" has a confirmation step
 *   - Auto toggle has a descriptive title
 *   - Error boundary around moveCardToBoard wrapper
 */
(function () {
  "use strict";

  const oh = window.__openingHand;
  if (!oh) return;

  // -----------------------------------------------------------------
  // Configuration
  // -----------------------------------------------------------------
  const MANA_SYMBOLS = ["W", "U", "B", "R", "G", "C"];
  const MANA_LABELS = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green", C: "Colorless" };

  // -----------------------------------------------------------------
  // State
  // -----------------------------------------------------------------
  let autoMode = true;
  let manaPool = { W: 0, U: 0, B: 0, R: 0, G: 0, C: 0 };

  // -----------------------------------------------------------------
  // DOM references
  // -----------------------------------------------------------------
  const autoToggle = document.getElementById("autoModeToggle");
  const manaPoolDisplay = document.getElementById("manaPoolDisplay");

  // -----------------------------------------------------------------
  // Auto-mode toggle
  // -----------------------------------------------------------------
  if (autoToggle) {
    autoToggle.checked = autoMode;
    // Update the label to be more descriptive.
    const label = autoToggle.nextElementSibling;
    if (label) label.title = "Auto-tap mana sources and auto-resolve ETB triggers when enabled.";
    autoToggle.addEventListener("change", () => {
      autoMode = autoToggle.checked;
      oh.showMessage(
        autoMode
          ? "Automation ON — click lands/mana sources to tap them; ETB effects resolve automatically."
          : "Automation OFF — all actions are manual.",
        "info"
      );
    });
  }

  // -----------------------------------------------------------------
  // 1. Auto-tap for mana
  // -----------------------------------------------------------------
  const boardArea = document.getElementById("boardArea");
  if (boardArea) {
    boardArea.addEventListener(
      "click",
      (event) => {
        if (!autoMode) return;
        const cardEl = event.target.closest(".hand-card");
        if (!cardEl) return;
        if (event.button !== 0) return;

        const card = findBoardCardByElement(cardEl);
        if (!card) return;
        if (card.boardZone === "graveyard" || card.boardZone === "command") return;

        const manaProduced = detectManaProduction(card);
        if (!manaProduced || !manaProduced.length) return;

        event.stopPropagation();
        event.preventDefault();

        if (card.tapped) {
          oh.showMessage(`${card.name} is already tapped.`, "warning");
          return;
        }

        // "Any color" — show a color picker before tapping.
        if (manaProduced.length === 5 && manaProduced.includes("W") && manaProduced.includes("G")) {
          showColorPicker(card, (chosen) => {
            card.tapped = true;
            oh.renderBoard();
            manaPool[chosen] = (manaPool[chosen] || 0) + 1;
            updateManaDisplay();
            oh.showMessage(`Tapped ${card.name} for {${chosen}}.`, "info");
          });
          return;
        }

        card.tapped = true;
        oh.renderBoard();
        manaProduced.forEach((sym) => {
          manaPool[sym] = (manaPool[sym] || 0) + 1;
        });
        updateManaDisplay();
        const manaText = manaProduced.map((s) => `{${s}}`).join("");
        oh.showMessage(`Tapped ${card.name} for ${manaText}.`, "info");
      },
      { capture: true }
    );
  }

  // -----------------------------------------------------------------
  // Color picker for "any color" mana sources
  // -----------------------------------------------------------------
  function showColorPicker(card, onPick) {
    let picker = document.getElementById("manaColorPicker");
    if (!picker) {
      picker = document.createElement("div");
      picker.id = "manaColorPicker";
      picker.setAttribute("role", "dialog");
      picker.setAttribute("aria-label", "Choose mana color");
      picker.style.cssText = `
        position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
        z-index: 2100; background: rgba(15,23,42,0.97);
        border: 1px solid rgba(148,163,184,0.35); border-radius: 0.85rem;
        padding: 1rem 1.25rem; min-width: 260px;
        box-shadow: 0 1.5rem 3rem rgba(2,6,23,0.65); backdrop-filter: blur(16px);
      `;
      document.body.appendChild(picker);
    }
    picker.innerHTML = `
      <div style="font-weight:700;font-size:0.88rem;color:#f1f5f9;margin-bottom:0.65rem;">
        Tap ${escapeHtml(card.name)} — choose a color:
      </div>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;justify-content:center;">
        ${["W","U","B","R","G","C"].map((s) => `
          <button type="button" data-color="${s}" class="mana-pick-btn"
            style="width:2.4rem;height:2.4rem;border-radius:999px;border:2px solid rgba(255,255,255,0.25);
                   font-weight:700;font-size:0.85rem;cursor:pointer;
                   background:${manaBackground(s)};color:${manaForeground(s)};"
            title="${MANA_LABELS[s]}">{${s}}</button>
        `).join("")}
      </div>
      <div style="margin-top:0.65rem;text-align:right;">
        <button type="button" id="manaPickerCancel" style="background:transparent;border:1px solid rgba(148,163,184,0.3);border-radius:0.4rem;padding:0.25rem 0.65rem;font-size:0.8rem;color:rgba(148,163,184,0.8);cursor:pointer;">Cancel</button>
      </div>
    `;
    picker.style.display = "block";
    picker.querySelectorAll(".mana-pick-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        picker.style.display = "none";
        onPick(btn.getAttribute("data-color"));
      });
    });
    picker.querySelector("#manaPickerCancel").addEventListener("click", () => {
      picker.style.display = "none";
    });
  }

  function manaBackground(sym) {
    return { W: "#f9fafb", U: "#3b82f6", B: "#312e81", R: "#ef4444", G: "#16a34a", C: "#94a3b8" }[sym] || "#475569";
  }
  function manaForeground(sym) {
    return { W: "#1e293b", U: "#fff", B: "#e0e7ff", R: "#fff", G: "#fff", C: "#1e293b" }[sym] || "#fff";
  }

  // -----------------------------------------------------------------
  // 2. ETB trigger detection (wraps moveCardToBoard)
  // -----------------------------------------------------------------
  const _origMoveCardToBoard = oh.moveCardToBoard.bind(oh);
  oh.moveCardToBoard = function (card, preferredZone, beforeId, stackParentId) {
    try {
      _origMoveCardToBoard(card, preferredZone, beforeId, stackParentId);
    } catch (err) {
      console.error("[triggers] moveCardToBoard error:", err);
      return;
    }
    if (!autoMode || !card) return;
    const zone = card.boardZone || preferredZone;
    const isBattlefield = zone === "creatures" || zone === "permanents" || zone === "lands";
    if (!isBattlefield) return;
    const etbEffects = detectEtbTriggers(card);
    etbEffects.forEach((effect) => {
      if (effect.autoResolvable) {
        autoResolveEtb(card, effect);
      } else {
        announceManualTrigger(card, effect);
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
    const label = document.createElement("span");
    label.style.cssText = "font-size:0.7rem;color:rgba(148,163,184,0.7);margin-right:0.2rem;";
    label.textContent = "Mana:";
    manaPoolDisplay.innerHTML = "";
    manaPoolDisplay.appendChild(label);
    MANA_SYMBOLS.filter((s) => manaPool[s] > 0).forEach((s) => {
      const pip = document.createElement("span");
      pip.className = `mana-pip mana-${s.toLowerCase()}`;
      pip.title = `${manaPool[s]} ${MANA_LABELS[s]}`;
      pip.textContent = manaPool[s] > 1 ? `${manaPool[s]}{${s}}` : `{${s}}`;
      manaPoolDisplay.appendChild(pip);
    });
  }

  // Clear mana pool when untap all fires.
  const untapAllBtn = document.getElementById("untapAllBtn");
  if (untapAllBtn) {
    untapAllBtn.addEventListener("click", () => {
      MANA_SYMBOLS.forEach((s) => { manaPool[s] = 0; });
      updateManaDisplay();
    });
  }

  // -----------------------------------------------------------------
  // 4. Manual trigger announcer
  //
  // Non-auto-resolvable triggers (discard a card, scry, search, generic
  // triggered abilities) are surfaced as a toast through oh.showMessage
  // so the user gets a reminder without an extra panel hidden behind
  // the bottom action bar.
  // -----------------------------------------------------------------
  function announceManualTrigger(card, effect) {
    const message = `${card.name} — ${effect.description}`;
    oh.showMessage(message, "warning");
  }

  // -----------------------------------------------------------------
  // 5. Oracle text parsers
  // -----------------------------------------------------------------

  function detectManaProduction(card) {
    const text = (card.oracle_text || "").toLowerCase();
    if (!text || !text.includes("add")) return [];

    const produced = [];

    // "any color" mana — return sentinel array of all 5.
    if (/add\s+(?:one mana of any color|mana of any color)/i.test(text)) {
      return ["W", "U", "B", "R", "G"];
    }

    // Colorless.
    if (/add\s+\{c\}/i.test(text)) produced.push("C");

    // Specific symbols from "add {X}" patterns.
    const re = /add\s+(?:\{([wubrgc])(?:\/[wubrgc])?\})+/gi;
    let match;
    while ((match = re.exec(text)) !== null) {
      const sym = match[1].toUpperCase();
      if (MANA_SYMBOLS.includes(sym) && !produced.includes(sym)) produced.push(sym);
    }

    // Basic land subtypes always produce their color.
    const typeLine = (card.type_line || "").toLowerCase();
    const subtypeMap = { plains: "W", island: "U", swamp: "B", mountain: "R", forest: "G" };
    Object.entries(subtypeMap).forEach(([subtype, sym]) => {
      if (typeLine.includes(subtype) && !produced.includes(sym)) produced.push(sym);
    });

    return produced;
  }

  function detectEtbTriggers(card) {
    const text = card.oracle_text || "";
    if (!text) return [];
    const effects = [];

    const etbPatterns = [
      /when\s+(?:this|~|[\w\s,]+?)\s+enters(?:\s+the\s+battlefield)?[^.]*?[,.]/gi,
      /as\s+(?:this|~|[\w\s,]+?)\s+enters(?:\s+the\s+battlefield)?[^.]*?[,.]/gi,
    ];

    for (const pattern of etbPatterns) {
      let match;
      while ((match = pattern.exec(text)) !== null) {
        const sentence = match[0];
        const sl = sentence.toLowerCase();

        const drawMatch = sl.match(/draw\s+(a|an|one|two|three|four|five|\d+)\s+card/);
        if (drawMatch) {
          const count = parseWordCount(drawMatch[1]);
          effects.push({ kind: "draw", count, description: `Draw ${count} card${count === 1 ? "" : "s"} (ETB)`, autoResolvable: count > 0 });
        }

        const discardMatch = sl.match(/discard\s+(a|an|one|two|three|four|five|\d+)\s+card/);
        if (discardMatch) {
          const count = parseWordCount(discardMatch[1]);
          effects.push({ kind: "discard", count, description: `Discard ${count} card${count === 1 ? "" : "s"} (ETB)`, autoResolvable: false });
        }

        const tokenMatch = sl.match(/create\s+(a|an|one|two|three|four|five|\d+)\s+(?:\S+\s+)*token/);
        if (tokenMatch) {
          const count = parseWordCount(tokenMatch[1]);
          effects.push({ kind: "tokens", count, description: `Create ${count} token${count === 1 ? "" : "s"} (ETB)`, autoResolvable: count > 0 });
        }

        const scryMatch = sl.match(/scry\s+(\d+)/);
        if (scryMatch) {
          const count = parseInt(scryMatch[1], 10);
          effects.push({ kind: "scry", count, description: `Scry ${count} (ETB)`, autoResolvable: false });
        }

        if (sl.includes("search your library")) {
          const isBasicLand = sl.includes("basic land");
          const toBattlefield = sl.includes("battlefield");
          const tapped = sl.includes("tapped");
          effects.push({
            kind: "search",
            criteria: { kind: isBasicLand ? "basic_land" : "land" },
            destination: toBattlefield ? "battlefield" : "hand",
            tapped,
            description: `Search library for ${isBasicLand ? "basic land" : "land"} → ${toBattlefield ? "battlefield" + (tapped ? " (tapped)" : "") : "hand"} (ETB)`,
            autoResolvable: false,
          });
        }

        if (sl.match(/put\s+(?:a|an|one|two|three|\d+)\s+\+1\/\+1\s+counter/)) {
          effects.push({ kind: "counter", description: "Put +1/+1 counter(s) on a creature (ETB) — place manually", autoResolvable: false });
        }
      }
    }

    // Recurring triggered abilities — queue as reminders.
    const triggeredPatterns = [
      { re: /whenever\s+(?:this|~|[\w\s,]+?)\s+attacks[^.]*\./gi, label: "Attacks trigger" },
      { re: /at\s+the\s+beginning\s+of\s+(?:your\s+)?upkeep[^.]*\./gi, label: "Upkeep trigger" },
      { re: /at\s+the\s+beginning\s+of\s+(?:your\s+)?end\s+step[^.]*\./gi, label: "End step trigger" },
      { re: /whenever\s+you\s+(?:draw|cast|play)[^.]*\./gi, label: "Cast/draw trigger" },
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

  async function autoResolveEtb(card, effect) {
    if (effect.kind === "draw" && effect.count > 0) {
      oh.showMessage(`${card.name}: drawing ${effect.count} card${effect.count === 1 ? "" : "s"}…`, "info");
      if (oh.drawCards) {
        await oh.drawCards(effect.count);
      } else {
        for (let i = 0; i < effect.count; i++) {
          if (oh.drawBtn && !oh.drawBtn.disabled) oh.drawBtn.click();
          await sleep(120);
        }
      }
    } else if (effect.kind === "discard" && effect.count > 0) {
      // Open the discard modal via the resolve modal with discard pre-selected.
      oh.showMessage(`${card.name}: discard ${effect.count} card${effect.count === 1 ? "" : "s"} — choose from your hand.`, "info");
      const resolveEffectType = document.getElementById("resolveEffectType");
      const resolveDiscardCount = document.getElementById("resolveDiscardCount");
      const resolveApplyBtn = document.getElementById("resolveApplyBtn");
      const resolveModal = document.getElementById("resolveModal");
      if (resolveEffectType && resolveDiscardCount && resolveApplyBtn && resolveModal) {
        resolveEffectType.value = "discard";
        resolveDiscardCount.value = String(effect.count);
        // Trigger the field visibility update.
        resolveEffectType.dispatchEvent(new Event("change"));
        const instance = window.bootstrap && bootstrap.Modal
          ? bootstrap.Modal.getOrCreateInstance(resolveModal)
          : null;
        if (instance) instance.show();
      }
    } else if (effect.kind === "tokens" && effect.count > 0) {
      oh.showMessage(`${card.name}: creating ${effect.count} token${effect.count === 1 ? "" : "s"} — pick from the token picker.`, "info");
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
      oh.showMessage(`${card.name}: ${effect.description} — use Fetch Card to search.`, "info");
      const fetchBtn = document.getElementById("fetchCardBtn");
      if (fetchBtn) fetchBtn.click();
    } else if (effect.kind === "triggered") {
      oh.showMessage(`Reminder — ${card.name}: ${effect.description}`, "info");
    }
    // "counter" kind: no action, just the queue entry serves as a reminder.
  }

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
    // Primary: use data-cardId attribute set by the core renderer.
    const uid = cardEl.dataset.cardId;
    if (uid) {
      for (const zone of Object.keys(oh.boardState)) {
        const found = (oh.boardState[zone] || []).find((c) => c.__uid === uid);
        if (found) return found;
      }
    }
    // Fallback: DOM index within zone container.
    for (const zone of Object.keys(oh.boardState)) {
      const container = document.getElementById(`board${zone.charAt(0).toUpperCase() + zone.slice(1)}`);
      if (!container) continue;
      const allEls = Array.from(container.querySelectorAll(".hand-card"));
      const idx = allEls.indexOf(cardEl);
      if (idx >= 0 && idx < (oh.boardState[zone] || []).length) {
        return oh.boardState[zone][idx];
      }
    }
    return null;
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
  // Styles
  // -----------------------------------------------------------------
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
  `;
  document.head.appendChild(style);

  // -----------------------------------------------------------------
  // Initialize
  // -----------------------------------------------------------------
  updateManaDisplay();
})();
