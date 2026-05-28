/*
 * opening-hand-enhancements.js
 *
 * Supplemental module that adds quality-of-life improvements to the Opening
 * Hand simulator without modifying the core 5000-line inline script. It hooks
 * into the public API exposed on window.__openingHand.
 *
 * Features:
 *   1. "Next Turn" button — untaps all, draws a card, increments turn counter,
 *      resets land-played flag. Guarded against empty state.
 *   2. Auto-play lands — single-click a land in hand plays it directly to the
 *      lands zone without a context menu.
 *   3. Undo stack — stores the last 10 state tokens so users can revert.
 *   4. Keyboard shortcuts — D=draw, U=untap, N=next turn, M=mulligan, Z=undo,
 *      ?=show shortcut help.
 *   5. Turn counter display — shows "Turn N" in the status bar.
 *   6. LocalStorage persistence — saves board state so page refresh doesn't
 *      lose everything.
 *   7. Hand size + lands-played indicators in the status bar.
 *   8. Reset confirmation — prevents accidental wipes.
 *   9. Double-click guard on Next Turn.
 *  10. Keyboard shortcut help panel (?).
 */
(function () {
  "use strict";

  const oh = window.__openingHand;
  if (!oh) return;

  const STORAGE_KEY_PREFIX = "dv_opening_hand_";
  const MAX_UNDO = 10;

  // -----------------------------------------------------------------
  // State
  // -----------------------------------------------------------------
  let turnNumber = 0;
  let undoStack = [];
  let nextTurnBusy = false;

  // -----------------------------------------------------------------
  // DOM references
  // -----------------------------------------------------------------
  const nextTurnBtn = document.getElementById("nextTurnBtn");
  const undoBtn = document.getElementById("undoBtn");
  const resetBtn = document.getElementById("resetBtn");
  const handGrid = document.getElementById("handGrid");
  const turnBadge = document.getElementById("turnCounter");
  const handSizeBadge = document.getElementById("handSizeBadge");
  const landsPlayedBadge = document.getElementById("landsPlayedBadge");

  // -----------------------------------------------------------------
  // 1. Next Turn button — guarded against empty state + double-click
  // -----------------------------------------------------------------
  if (nextTurnBtn) {
    nextTurnBtn.addEventListener("click", async () => {
      if (nextTurnBusy) return;
      // Guard: don't fire before a hand is dealt.
      if (!oh.stateInput || !oh.stateInput.value) {
        oh.showMessage("Shuffle a deck first.", "warning");
        return;
      }
      nextTurnBusy = true;
      nextTurnBtn.disabled = true;
      try {
        await doNextTurn();
      } finally {
        nextTurnBusy = false;
        nextTurnBtn.disabled = false;
      }
    });
  }

  async function doNextTurn() {
    pushUndo();

    // Untap all.
    const untapAllBtn = document.getElementById("untapAllBtn");
    if (untapAllBtn) {
      untapAllBtn.click();
    } else if (oh.untapAllBoardCards) {
      oh.untapAllBoardCards();
    }
    oh.landsPlayedThisTurn = 0;

    // Draw exactly one card via the awaitable drawCards helper.
    // We record hand size before/after to confirm only one card was added.
    const handBefore = (oh.handCards || []).length;
    let drewCount = 0;
    if (typeof oh.drawCards === "function") {
      try {
        drewCount = await oh.drawCards(1);
      } catch (_) {
        drewCount = 0;
      }
    }
    const handAfter = (oh.handCards || []).length;
    const actuallyDrew = handAfter - handBefore;

    turnNumber += 1;
    updateTurnDisplay();
    updateStatusBadges();
    if (actuallyDrew > 0) {
      oh.showMessage(`Turn ${turnNumber} — untapped and drew a card.`, "info");
    } else {
      oh.showMessage(`Turn ${turnNumber} — untapped (deck empty, no draw).`, "warning");
    }
    persistState();
  }

  // -----------------------------------------------------------------
  // 2. Auto-play lands (single-click in hand)
  // -----------------------------------------------------------------
  if (handGrid) {
    handGrid.addEventListener(
      "click",
      (event) => {
        const cardEl = event.target.closest(".hand-card");
        if (!cardEl) return;
        if (event.button !== 0) return;
        const allCards = Array.from(handGrid.querySelectorAll(".hand-card"));
        const idx = allCards.indexOf(cardEl);
        if (idx < 0 || idx >= oh.handCards.length) return;
        const card = oh.handCards[idx];
        if (!card || !card.is_land) return;

        event.stopPropagation();
        event.preventDefault();
        pushUndo();
        oh.handCards.splice(idx, 1);
        oh.moveCardToBoard(card, "lands");
        oh.landsPlayedThisTurn += 1;
        oh.renderHand();
        updateStatusBadges();
        persistState();
      },
      { capture: true }
    );
  }

  // -----------------------------------------------------------------
  // 3. Undo stack
  // -----------------------------------------------------------------
  if (undoBtn) {
    undoBtn.addEventListener("click", () => popUndo());
  }

  function pushUndo() {
    const snapshot = {
      stateToken: oh.stateInput ? oh.stateInput.value : "",
      handCards: JSON.parse(JSON.stringify(oh.handCards || [])),
      boardState: JSON.parse(JSON.stringify(oh.boardState || {})),
      turn: turnNumber,
      landsPlayed: oh.landsPlayedThisTurn,
    };
    undoStack.push(snapshot);
    if (undoStack.length > MAX_UNDO) undoStack.shift();
    if (undoBtn) undoBtn.disabled = false;
  }

  function popUndo() {
    if (!undoStack.length) return;
    const snapshot = undoStack.pop();
    if (oh.stateInput) oh.stateInput.value = snapshot.stateToken;
    oh.handCards = snapshot.handCards;
    const zones = Object.keys(oh.boardState);
    zones.forEach((zone) => {
      oh.boardState[zone] = snapshot.boardState[zone] || [];
    });
    turnNumber = snapshot.turn;
    oh.landsPlayedThisTurn = snapshot.landsPlayed;
    oh.renderHand();
    oh.renderBoard();
    updateTurnDisplay();
    updateStatusBadges();
    oh.showMessage("Undid last action.", "info");
    if (undoBtn) undoBtn.disabled = undoStack.length === 0;
    persistState();
  }

  // -----------------------------------------------------------------
  // 4. Keyboard shortcuts
  // -----------------------------------------------------------------
  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    const key = event.key.toLowerCase();
    switch (key) {
      case "d":
        event.preventDefault();
        if (oh.drawBtn && !oh.drawBtn.disabled) oh.drawBtn.click();
        break;
      case "u":
        event.preventDefault();
        document.getElementById("untapAllBtn")?.click();
        break;
      case "n":
        event.preventDefault();
        if (nextTurnBtn && !nextTurnBtn.disabled && !nextTurnBusy) nextTurnBtn.click();
        break;
      case "m":
        event.preventDefault();
        if (oh.mulliganBtn && !oh.mulliganBtn.disabled) oh.mulliganBtn.click();
        break;
      case "z":
        event.preventDefault();
        if (undoBtn && !undoBtn.disabled) undoBtn.click();
        break;
      case "?":
        event.preventDefault();
        toggleShortcutHelp();
        break;
      case "escape": {
        const openModal = document.querySelector(".modal.show");
        if (openModal && window.bootstrap) {
          bootstrap.Modal.getInstance(openModal)?.hide();
        }
        hideShortcutHelp();
        break;
      }
    }
  });

  // -----------------------------------------------------------------
  // 5. Turn counter display
  // -----------------------------------------------------------------
  function updateTurnDisplay() {
    if (!turnBadge) return;
    if (turnNumber > 0) {
      turnBadge.textContent = `Turn ${turnNumber}`;
      turnBadge.hidden = false;
    } else {
      turnBadge.hidden = true;
    }
  }

  // -----------------------------------------------------------------
  // 7. Hand size + lands-played status badges
  // -----------------------------------------------------------------
  function updateStatusBadges() {
    if (handSizeBadge) {
      const count = (oh.handCards || []).length;
      handSizeBadge.textContent = `Hand: ${count}`;
      handSizeBadge.hidden = false;
    }
    if (landsPlayedBadge) {
      const played = oh.landsPlayedThisTurn || 0;
      landsPlayedBadge.textContent = played > 0 ? `Land played` : `Land: 0`;
      landsPlayedBadge.className = `badge ${played > 0 ? "text-bg-success" : "text-bg-secondary"}`;
      landsPlayedBadge.hidden = false;
    }
  }

  // Observe hand changes to keep badge current.
  setInterval(() => {
    if (oh.stateInput && oh.stateInput.value) updateStatusBadges();
  }, 800);

  // -----------------------------------------------------------------
  // 8. Reset confirmation
  // -----------------------------------------------------------------
  if (resetBtn) {
    // Intercept the reset button before the core handler fires.
    resetBtn.addEventListener(
      "click",
      (event) => {
        const hasState = oh.stateInput && oh.stateInput.value;
        const hasCards = (oh.handCards || []).length > 0 ||
          Object.values(oh.boardState || {}).some((z) => z.length > 0);
        if (!hasState && !hasCards) return; // nothing to lose, let it through
        if (!confirm("Reset the simulator? This will clear your hand and board.")) {
          event.stopImmediatePropagation();
          event.preventDefault();
        } else {
          // Clear undo stack and turn counter on confirmed reset.
          undoStack = [];
          turnNumber = 0;
          if (undoBtn) undoBtn.disabled = true;
          updateTurnDisplay();
          updateStatusBadges();
          try { localStorage.removeItem(storageKey()); } catch (_) {}
        }
      },
      { capture: true }
    );
  }

  // -----------------------------------------------------------------
  // 10. Keyboard shortcut help panel
  // -----------------------------------------------------------------
  const SHORTCUTS = [
    { key: "N", desc: "Next Turn (untap + draw)" },
    { key: "D", desc: "Draw 1 card" },
    { key: "U", desc: "Untap all permanents" },
    { key: "M", desc: "Mulligan" },
    { key: "Z", desc: "Undo last action" },
    { key: "?", desc: "Show / hide this help" },
    { key: "Esc", desc: "Close modal / help" },
  ];

  let shortcutPanel = null;

  function buildShortcutPanel() {
    if (shortcutPanel) return shortcutPanel;
    const panel = document.createElement("div");
    panel.id = "shortcutHelpPanel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Keyboard shortcuts");
    panel.style.cssText = `
      position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
      z-index: 2000; background: rgba(15,23,42,0.97);
      border: 1px solid rgba(148,163,184,0.35); border-radius: 0.85rem;
      padding: 1.25rem 1.5rem; min-width: 280px; max-width: 360px;
      box-shadow: 0 1.5rem 3rem rgba(2,6,23,0.65); backdrop-filter: blur(16px);
      display: none;
    `;
    panel.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.75rem;">
        <span style="font-weight:700;font-size:0.9rem;color:#f1f5f9;">Keyboard Shortcuts</span>
        <button type="button" id="shortcutHelpClose" style="background:transparent;border:0;color:rgba(148,163,184,0.7);font-size:1.1rem;line-height:1;padding:0.1rem 0.3rem;border-radius:0.3rem;cursor:pointer;" aria-label="Close">✕</button>
      </div>
      <table style="width:100%;border-collapse:collapse;">
        ${SHORTCUTS.map((s) => `
          <tr>
            <td style="padding:0.3rem 0.5rem 0.3rem 0;white-space:nowrap;">
              <kbd style="background:rgba(255,255,255,0.1);border:1px solid rgba(148,163,184,0.3);border-radius:0.3rem;padding:0.15rem 0.45rem;font-size:0.8rem;font-family:monospace;color:#e2e8f0;">${escapeHtml(s.key)}</kbd>
            </td>
            <td style="padding:0.3rem 0;font-size:0.82rem;color:rgba(203,213,225,0.9);">${escapeHtml(s.desc)}</td>
          </tr>
        `).join("")}
      </table>
      <div style="margin-top:0.75rem;font-size:0.72rem;color:rgba(148,163,184,0.6);">Press <kbd style="background:rgba(255,255,255,0.1);border:1px solid rgba(148,163,184,0.3);border-radius:0.3rem;padding:0.1rem 0.35rem;font-family:monospace;">?</kbd> or Esc to close.</div>
    `;
    document.body.appendChild(panel);
    panel.querySelector("#shortcutHelpClose").addEventListener("click", hideShortcutHelp);
    shortcutPanel = panel;
    return panel;
  }

  function toggleShortcutHelp() {
    const panel = buildShortcutPanel();
    if (panel.style.display === "none" || !panel.style.display) {
      panel.style.display = "block";
      panel.querySelector("#shortcutHelpClose").focus();
    } else {
      panel.style.display = "none";
    }
  }

  function hideShortcutHelp() {
    if (shortcutPanel) shortcutPanel.style.display = "none";
  }

  // -----------------------------------------------------------------
  // 6. LocalStorage persistence
  // -----------------------------------------------------------------
  function storageKey() {
    try {
      const deckId = oh.currentDeckId ? oh.currentDeckId() : "";
      return STORAGE_KEY_PREFIX + (deckId || "custom");
    } catch (_) {
      return STORAGE_KEY_PREFIX + "custom";
    }
  }

  function persistState() {
    try {
      const payload = {
        stateToken: oh.stateInput ? oh.stateInput.value : "",
        handCards: oh.handCards || [],
        boardState: oh.boardState || {},
        turn: turnNumber,
        landsPlayed: oh.landsPlayedThisTurn,
        savedAt: Date.now(),
      };
      localStorage.setItem(storageKey(), JSON.stringify(payload));
    } catch (_err) {}
  }

  function restoreState() {
    try {
      const raw = localStorage.getItem(storageKey());
      if (!raw) return false;
      const payload = JSON.parse(raw);
      if (Date.now() - (payload.savedAt || 0) > 6 * 60 * 60 * 1000) {
        localStorage.removeItem(storageKey());
        return false;
      }
      if (payload.stateToken && oh.stateInput) {
        oh.stateInput.value = payload.stateToken;
      }
      if (Array.isArray(payload.handCards) && payload.handCards.length) {
        oh.handCards = payload.handCards;
        oh.renderHand();
      }
      if (payload.boardState) {
        Object.keys(oh.boardState).forEach((zone) => {
          oh.boardState[zone] = payload.boardState[zone] || [];
        });
        oh.renderBoard();
      }
      turnNumber = payload.turn || 0;
      oh.landsPlayedThisTurn = payload.landsPlayed || 0;
      updateTurnDisplay();
      updateStatusBadges();
      return true;
    } catch (_err) {
      return false;
    }
  }

  // Restore on load.
  if (oh.stateInput && !oh.stateInput.value) {
    const restored = restoreState();
    if (restored) {
      oh.showMessage("Restored previous session. Press Reset to start fresh.", "info");
      oh.updateActionVisibility();
    }
  }

  // Persist periodically.
  setInterval(() => {
    if (oh.stateInput && oh.stateInput.value) persistState();
  }, 5000);

  // -----------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------
  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  // -----------------------------------------------------------------
  // Initialize
  // -----------------------------------------------------------------
  updateTurnDisplay();
  updateStatusBadges();
})();
