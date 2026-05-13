/*
 * opening-hand-enhancements.js
 *
 * Supplemental module that adds quality-of-life improvements to the Opening
 * Hand simulator without modifying the core 5000-line inline script. It hooks
 * into the public API exposed on window.__openingHand.
 *
 * Features:
 *   1. "Next Turn" button — untaps all, draws a card, increments turn counter,
 *      resets land-played flag.
 *   2. Auto-play lands — single-click a land in hand plays it directly to the
 *      lands zone without a context menu.
 *   3. Undo stack — stores the last 10 state tokens so users can revert.
 *   4. Keyboard shortcuts — D=draw, U=untap, N=next turn, M=mulligan, Z=undo.
 *   5. Turn counter display — shows "Turn N" in the status bar.
 *   6. LocalStorage persistence — saves board state so page refresh doesn't
 *      lose everything.
 *   7. One-click start — auto-submits the landing form when a deck is selected.
 *   8. Inline effect shortcuts — simple draw/token effects resolve without modal.
 */
(function () {
  "use strict";

  // Wait for the core module to initialize.
  const oh = window.__openingHand;
  if (!oh) return;

  const STORAGE_KEY_PREFIX = "dv_opening_hand_";
  const MAX_UNDO = 10;

  // -----------------------------------------------------------------
  // State
  // -----------------------------------------------------------------
  let turnNumber = 0;
  let undoStack = [];

  // -----------------------------------------------------------------
  // 1. Next Turn button
  // -----------------------------------------------------------------
  const nextTurnBtn = document.getElementById("nextTurnBtn");
  if (nextTurnBtn) {
    nextTurnBtn.addEventListener("click", async () => {
      await doNextTurn();
    });
  }

  async function doNextTurn() {
    // Save undo point before the turn.
    pushUndo();

    // Untap all.
    if (oh.untapAllBoardCards) {
      oh.untapAllBoardCards();
    }
    oh.landsPlayedThisTurn = 0;

    // Draw a card by clicking the draw button (delegates to the core script's
    // draw handler which manages state token updates correctly).
    if (oh.drawBtn && !oh.drawBtn.disabled) {
      oh.drawBtn.click();
    }

    // Increment turn.
    turnNumber += 1;
    updateTurnDisplay();
    oh.showMessage(`Turn ${turnNumber} — untapped, drew a card.`, "info");
    persistState();
  }

  // -----------------------------------------------------------------
  // 2. Auto-play lands (intercept single-click on hand cards)
  // -----------------------------------------------------------------
  // We use event delegation on the hand grid. The core script already
  // handles clicks, but we can intercept lands before the context menu
  // fires by listening in the capture phase.
  const handGrid = document.getElementById("handGrid");
  if (handGrid) {
    handGrid.addEventListener(
      "click",
      (event) => {
        const cardEl = event.target.closest(".hand-card");
        if (!cardEl) return;
        // Only intercept left-click (not right-click context menu).
        if (event.button !== 0) return;
        // Find the card data from the hand array by matching the element index.
        const allCards = Array.from(handGrid.querySelectorAll(".hand-card"));
        const idx = allCards.indexOf(cardEl);
        if (idx < 0 || idx >= oh.handCards.length) return;
        const card = oh.handCards[idx];
        if (!card || !card.is_land) return;

        // Auto-play the land: remove from hand, add to lands zone.
        event.stopPropagation();
        event.preventDefault();
        pushUndo();
        oh.handCards.splice(idx, 1);
        oh.moveCardToBoard(card, "lands");
        oh.landsPlayedThisTurn += 1;
        oh.renderHand();
        persistState();
      },
      { capture: true }
    );
  }

  // -----------------------------------------------------------------
  // 3. Undo stack
  // -----------------------------------------------------------------
  const undoBtn = document.getElementById("undoBtn");
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
    // Restore board state.
    const zones = Object.keys(oh.boardState);
    zones.forEach((zone) => {
      oh.boardState[zone] = snapshot.boardState[zone] || [];
    });
    turnNumber = snapshot.turn;
    oh.landsPlayedThisTurn = snapshot.landsPlayed;
    oh.renderHand();
    oh.renderBoard();
    updateTurnDisplay();
    oh.showMessage("Undid last action.", "info");
    if (undoBtn) undoBtn.disabled = undoStack.length === 0;
    persistState();
  }

  // -----------------------------------------------------------------
  // 4. Keyboard shortcuts
  // -----------------------------------------------------------------
  document.addEventListener("keydown", (event) => {
    // Don't fire when typing in inputs/textareas.
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
        if (nextTurnBtn && !nextTurnBtn.disabled) nextTurnBtn.click();
        break;
      case "m":
        event.preventDefault();
        if (oh.mulliganBtn && !oh.mulliganBtn.disabled) oh.mulliganBtn.click();
        break;
      case "z":
        event.preventDefault();
        if (undoBtn && !undoBtn.disabled) undoBtn.click();
        break;
      case "escape":
        // Close any open modal.
        const openModal = document.querySelector(".modal.show");
        if (openModal && window.bootstrap) {
          bootstrap.Modal.getInstance(openModal)?.hide();
        }
        break;
    }
  });

  // -----------------------------------------------------------------
  // 5. Turn counter display
  // -----------------------------------------------------------------
  const turnBadge = document.getElementById("turnCounter");

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
  // 6. LocalStorage persistence
  // -----------------------------------------------------------------
  function storageKey() {
    const deckId = oh.currentDeckId ? oh.currentDeckId() : "";
    return STORAGE_KEY_PREFIX + (deckId || "custom");
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
    } catch (_err) {
      // Storage full or unavailable — silently ignore.
    }
  }

  function restoreState() {
    try {
      const raw = localStorage.getItem(storageKey());
      if (!raw) return false;
      const payload = JSON.parse(raw);
      // Only restore if saved within the last 6 hours (matches token TTL).
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
      return true;
    } catch (_err) {
      return false;
    }
  }

  // Attempt restore on load (only if we have a state token already set).
  if (oh.stateInput && !oh.stateInput.value) {
    const restored = restoreState();
    if (restored) {
      oh.showMessage("Restored previous session from local storage.", "info");
      oh.updateActionVisibility();
    }
  }

  // Persist after every draw/shuffle/play action by observing state changes.
  // We hook into the draw button and shuffle button via MutationObserver on
  // the state input.
  if (oh.stateInput) {
    const stateObserver = new MutationObserver(() => persistState());
    stateObserver.observe(oh.stateInput, { attributes: true, attributeFilter: ["value"] });
    // Also persist on value change (programmatic).
    oh.stateInput.addEventListener("change", () => persistState());
  }

  // Persist periodically (every 5 seconds if state exists).
  setInterval(() => {
    if (oh.stateInput && oh.stateInput.value) persistState();
  }, 5000);

  // -----------------------------------------------------------------
  // 7. One-click start (landing page auto-submit)
  // -----------------------------------------------------------------
  const landingForm = document.getElementById("openingHandStartForm");
  const landingDeckSelect = document.querySelector('[data-dv-select="opening-deck"]');
  if (landingForm && landingDeckSelect) {
    landingDeckSelect.addEventListener("dv-select:change", (event) => {
      const value = event.detail && event.detail.value;
      if (value) {
        // Small delay so the hidden input updates before submit.
        setTimeout(() => landingForm.submit(), 50);
      }
    });
  }

  // -----------------------------------------------------------------
  // 8. Inline effect shortcuts
  // -----------------------------------------------------------------
  // Override the core's "resolve" flow for simple effects. When a card
  // with a simple draw-N or create-token effect is played, we can
  // resolve it immediately without opening the modal.
  // This is done by intercepting the resolveModal show event.
  const resolveModalEl = document.getElementById("resolveModal");
  if (resolveModalEl) {
    resolveModalEl.addEventListener("show.bs.modal", (event) => {
      // Check if the effect is simple enough to auto-resolve.
      const effectType = document.getElementById("resolveEffectType");
      const drawCount = document.getElementById("resolveDrawCount");
      if (!effectType) return;

      const type = effectType.value;
      // Auto-resolve simple "draw 1" without modal.
      if (type === "draw" && drawCount && parseInt(drawCount.value || "0", 10) === 1) {
        event.preventDefault();
        // Trigger a draw via the draw button.
        if (oh.drawBtn && !oh.drawBtn.disabled) {
          oh.drawBtn.click();
        }
      }
    });
  }

  // -----------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------

  // Initialize turn display.
  updateTurnDisplay();
})();
