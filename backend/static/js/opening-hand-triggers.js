/*
 * opening-hand-triggers.js
 *
 * ETB trigger automation for the Opening Hand simulator.
 *
 * When the Auto toggle is on and a card enters a battlefield zone:
 *   - "Draw N" effects auto-fire via oh.drawCards()
 *   - "Create N tokens" opens the token picker
 *   - "Scry N" opens the scry modal with the count pre-filled
 *   - "Search your library" opens the fetch modal
 *   - Discard / +1/+1 counter / generic triggered abilities surface as
 *     a warning toast for the user to resolve manually
 *
 * Mana auto-tap and the mana-pool tracker were removed at user request.
 */
(function () {
  "use strict";

  const oh = window.__openingHand;
  if (!oh) return;

  // -----------------------------------------------------------------
  // State
  // -----------------------------------------------------------------
  let autoMode = true;

  // -----------------------------------------------------------------
  // DOM references
  // -----------------------------------------------------------------
  const autoToggle = document.getElementById("autoModeToggle");

  // -----------------------------------------------------------------
  // Auto-mode toggle
  // -----------------------------------------------------------------
  if (autoToggle) {
    autoToggle.checked = autoMode;
    const label = autoToggle.nextElementSibling;
    if (label) label.title = "Auto-resolve ETB triggers when enabled (draw, tokens, scry, search prompts).";
    autoToggle.addEventListener("change", () => {
      autoMode = autoToggle.checked;
      oh.showMessage(
        autoMode
          ? "Automation ON — ETB triggers resolve automatically."
          : "Automation OFF — all actions are manual.",
        "info"
      );
    });
  }

  // -----------------------------------------------------------------
  // Wrap moveCardToBoard to detect ETB effects
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

  function announceManualTrigger(card, effect) {
    const message = `${card.name} — ${effect.description}`;
    oh.showMessage(message, "warning");
  }

  // -----------------------------------------------------------------
  // Oracle text parser — ETB triggers
  // -----------------------------------------------------------------
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

    // Recurring triggered abilities — surface as reminders, not auto-resolved.
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
    // "discard" / "counter" kinds intentionally not auto-resolved — they
    // come through announceManualTrigger as warning toasts instead.
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

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
})();
