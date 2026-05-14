/*
 * opening-hand-life-counters.js
 *
 * Supplemental module that adds three high-impact gameplay features to the
 * Opening Hand simulator:
 *
 *   1. Life Tracker — primary life total with +/- buttons, optional commander
 *      damage matrix for multiplayer pods. Persists per-deck.
 *   2. +1/+1 Counter Tracker — increment/decrement counters on creatures via
 *      a small badge overlaid on the card; integrates with the ETB +1/+1
 *      counter trigger from triggers.js.
 *   3. Auto-Tap Mana on Cast — when a non-land card leaves the hand for the
 *      battlefield with the auto toggle on, automatically taps untapped
 *      lands matching the mana cost and deducts from the mana pool. Warns
 *      if mana is insufficient (does not block — Magic players get the
 *      final say).
 *
 * Loads after enhancements + triggers, so it can extend their wrappers.
 */
(function () {
  "use strict";

  const oh = window.__openingHand;
  if (!oh) return;

  // -----------------------------------------------------------------
  // Constants
  // -----------------------------------------------------------------
  const LIFE_STORAGE_KEY = "dv_opening_hand_life";
  const COUNTERS_STORAGE_KEY = "dv_opening_hand_counters";
  const STARTING_LIFE = 40; // Commander default; adjustable.
  const MANA_SYMBOLS = ["W", "U", "B", "R", "G", "C"];

  // -----------------------------------------------------------------
  // State
  // -----------------------------------------------------------------
  let lifeTotal = STARTING_LIFE;
  let opponents = []; // { id, name, life, commanderDamage }
  let counters = {}; // { [cardUid]: { plus: int } }

  // -----------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------
  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function deckScopedKey(base) {
    let id = "custom";
    try {
      if (typeof oh.currentDeckId === "function") {
        id = oh.currentDeckId() || "custom";
      }
    } catch (_) { /* ignore */ }
    return `${base}_${id}`;
  }

  function loadJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return fallback;
      const parsed = JSON.parse(raw);
      return parsed == null ? fallback : parsed;
    } catch (_) {
      return fallback;
    }
  }

  function saveJson(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_) { /* quota / private mode */ }
  }

  // =================================================================
  // 1. LIFE TRACKER
  // =================================================================

  function buildLifeTracker() {
    // Idempotent — if the tracker already exists, skip.
    // Also remove any duplicates left by stale cached scripts.
    const existing = document.querySelectorAll("#lifeTrackerWrap, .life-tracker");
    if (existing.length >= 1) {
      // Remove all but the first; if any exist we've been initialized.
      existing.forEach((el, idx) => { if (idx > 0) el.remove(); });
      return;
    }

    const statusBar = document.querySelector(".hand-status");
    if (!statusBar) return;

    const wrap = document.createElement("span");
    wrap.id = "lifeTrackerWrap";
    wrap.className = "badge text-bg-secondary life-tracker";
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", "Life total");
    wrap.innerHTML = `
      <i class="bi bi-heart-fill life-icon" aria-hidden="true"></i>
      <button type="button" class="life-btn life-minus" data-life-delta="-1" aria-label="Decrease life" title="Subtract 1 (right-click: −5)">−</button>
      <span class="life-display" id="lifeDisplay" title="Click to edit life total">${lifeTotal}</span>
      <button type="button" class="life-btn life-plus" data-life-delta="1" aria-label="Increase life" title="Add 1 (right-click: +5)">+</button>
      <button type="button" class="life-btn life-pod" id="lifePodBtn" aria-label="Manage opponents (commander damage)" title="Multiplayer pod &amp; commander damage">
        <i class="bi bi-people-fill" aria-hidden="true"></i>
      </button>
    `;
    statusBar.appendChild(wrap);

    wrap.querySelectorAll("[data-life-delta]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const delta = parseInt(btn.dataset.lifeDelta, 10) || 0;
        adjustLife(delta);
      });
    });

    // Right-click on +/- adjusts by 5 for fast changes.
    wrap.querySelector(".life-minus").addEventListener("contextmenu", (e) => {
      e.preventDefault();
      adjustLife(-5);
    });
    wrap.querySelector(".life-plus").addEventListener("contextmenu", (e) => {
      e.preventDefault();
      adjustLife(5);
    });

    // Click on display to edit directly.
    wrap.querySelector("#lifeDisplay").addEventListener("click", () => {
      const next = prompt("Set life total:", String(lifeTotal));
      if (next === null) return;
      const num = parseInt(next, 10);
      if (!isNaN(num)) {
        lifeTotal = num;
        renderLife();
        persistLife();
      }
    });

    wrap.querySelector("#lifePodBtn").addEventListener("click", openPodModal);
  }

  function adjustLife(delta) {
    lifeTotal += delta;
    renderLife();
    persistLife();
    if (lifeTotal <= 0) {
      oh.showMessage(`You're at ${lifeTotal} life.`, "warning");
    }
  }

  function renderLife() {
    const display = document.getElementById("lifeDisplay");
    if (!display) return;
    display.textContent = String(lifeTotal);
    display.classList.toggle("life-low", lifeTotal <= 5);
    display.classList.toggle("life-dead", lifeTotal <= 0);

    // Update pod button badge if there are opponents.
    const podBtn = document.getElementById("lifePodBtn");
    if (podBtn) {
      const count = opponents.length;
      let badge = podBtn.querySelector(".life-pod-badge");
      if (count > 0) {
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "life-pod-badge";
          podBtn.appendChild(badge);
        }
        badge.textContent = String(count);
      } else if (badge) {
        badge.remove();
      }
    }
  }

  function persistLife() {
    saveJson(deckScopedKey(LIFE_STORAGE_KEY), {
      lifeTotal,
      opponents,
      savedAt: Date.now(),
    });
  }

  function restoreLife() {
    const data = loadJson(deckScopedKey(LIFE_STORAGE_KEY), null);
    if (!data) {
      lifeTotal = STARTING_LIFE;
      opponents = [];
      return;
    }
    if (Date.now() - (data.savedAt || 0) > 6 * 60 * 60 * 1000) {
      lifeTotal = STARTING_LIFE;
      opponents = [];
      return;
    }
    lifeTotal = typeof data.lifeTotal === "number" ? data.lifeTotal : STARTING_LIFE;
    opponents = Array.isArray(data.opponents) ? data.opponents : [];
  }

  // -----------------------------------------------------------------
  // Pod / commander damage modal
  // -----------------------------------------------------------------
  function openPodModal() {
    let modal = document.getElementById("podModal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "podModal";
      modal.setAttribute("role", "dialog");
      modal.setAttribute("aria-label", "Pod and commander damage");
      modal.style.cssText = `
        position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
        z-index: 2050; background: rgba(15,23,42,0.97);
        border: 1px solid rgba(148,163,184,0.35); border-radius: 0.85rem;
        padding: 1.1rem 1.25rem; min-width: 340px; max-width: 480px;
        box-shadow: 0 1.5rem 3rem rgba(2,6,23,0.65); backdrop-filter: blur(16px);
      `;
      document.body.appendChild(modal);
    }
    renderPodModal(modal);
    modal.style.display = "block";
  }

  function renderPodModal(modal) {
    modal.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.85rem;">
        <span style="font-weight:700;font-size:0.95rem;color:#f1f5f9;">Pod &amp; Commander Damage</span>
        <button type="button" id="podClose" style="background:transparent;border:0;color:rgba(148,163,184,0.7);font-size:1.1rem;line-height:1;padding:0.1rem 0.35rem;border-radius:0.3rem;cursor:pointer;" aria-label="Close">✕</button>
      </div>
      <div id="podOpponents" style="display:flex;flex-direction:column;gap:0.55rem;max-height:60vh;overflow-y:auto;">
        ${opponents.length ? opponents.map(renderOpponentRow).join("") : '<div style="color:rgba(148,163,184,0.7);font-size:0.85rem;text-align:center;padding:0.5rem 0;">No opponents added yet.</div>'}
      </div>
      <div style="margin-top:0.85rem;display:flex;gap:0.5rem;flex-wrap:wrap;">
        <button type="button" id="podAddOpponent" class="btn btn-sm btn-outline-light">
          <i class="bi bi-person-plus me-1"></i>Add opponent
        </button>
        ${opponents.length ? '<button type="button" id="podClearOpponents" class="btn btn-sm btn-outline-danger">Remove all</button>' : ""}
        <button type="button" id="podResetLives" class="btn btn-sm btn-outline-secondary">Reset life totals</button>
      </div>
      <div style="margin-top:0.65rem;font-size:0.72rem;color:rgba(148,163,184,0.6);">
        Track each opponent's life and the commander damage you've taken from them. 21+ commander damage from a single opponent is lethal.
      </div>
    `;

    modal.querySelector("#podClose").addEventListener("click", () => {
      modal.style.display = "none";
    });
    modal.querySelector("#podAddOpponent").addEventListener("click", () => {
      const name = prompt("Opponent name:", `Opponent ${opponents.length + 1}`);
      if (!name) return;
      opponents.push({
        id: `opp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        name: name.slice(0, 40),
        life: STARTING_LIFE,
        commanderDamage: 0,
      });
      persistLife();
      renderLife();
      renderPodModal(modal);
    });
    const clearBtn = modal.querySelector("#podClearOpponents");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        if (!confirm("Remove all opponents?")) return;
        opponents = [];
        persistLife();
        renderLife();
        renderPodModal(modal);
      });
    }
    modal.querySelector("#podResetLives").addEventListener("click", () => {
      lifeTotal = STARTING_LIFE;
      opponents.forEach((opp) => {
        opp.life = STARTING_LIFE;
        opp.commanderDamage = 0;
      });
      persistLife();
      renderLife();
      renderPodModal(modal);
      oh.showMessage("Life totals reset.", "info");
    });

    modal.querySelectorAll("[data-opp-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const oppId = btn.dataset.oppId;
        const action = btn.dataset.oppAction;
        const opp = opponents.find((o) => o.id === oppId);
        if (!opp) return;
        if (action === "life-down") opp.life -= 1;
        else if (action === "life-up") opp.life += 1;
        else if (action === "cmd-down") opp.commanderDamage = Math.max(0, opp.commanderDamage - 1);
        else if (action === "cmd-up") opp.commanderDamage += 1;
        else if (action === "rename") {
          const next = prompt("Rename opponent:", opp.name);
          if (next) opp.name = next.slice(0, 40);
        } else if (action === "remove") {
          opponents = opponents.filter((o) => o.id !== oppId);
        }
        persistLife();
        renderLife();
        renderPodModal(modal);
      });
    });
  }

  function renderOpponentRow(opp) {
    const lethal = opp.commanderDamage >= 21;
    return `
      <div style="border:1px solid rgba(148,163,184,0.2);border-radius:0.6rem;padding:0.55rem 0.7rem;background:rgba(30,41,59,0.4);">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:0.5rem;margin-bottom:0.4rem;">
          <span style="font-weight:600;color:#f1f5f9;font-size:0.88rem;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(opp.name)}</span>
          <button type="button" class="pod-icon-btn" data-opp-action="rename" data-opp-id="${opp.id}" title="Rename"><i class="bi bi-pencil"></i></button>
          <button type="button" class="pod-icon-btn pod-icon-danger" data-opp-action="remove" data-opp-id="${opp.id}" title="Remove"><i class="bi bi-x-lg"></i></button>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem;font-size:0.78rem;">
          <div>
            <div style="color:rgba(148,163,184,0.7);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.15rem;">Life</div>
            <div style="display:flex;align-items:center;gap:0.3rem;">
              <button type="button" class="pod-step-btn" data-opp-action="life-down" data-opp-id="${opp.id}">−</button>
              <span style="min-width:2rem;text-align:center;font-weight:700;color:${opp.life <= 5 ? "#fda4af" : "#f1f5f9"};">${opp.life}</span>
              <button type="button" class="pod-step-btn" data-opp-action="life-up" data-opp-id="${opp.id}">+</button>
            </div>
          </div>
          <div>
            <div style="color:rgba(148,163,184,0.7);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.15rem;">Cmd dmg from them</div>
            <div style="display:flex;align-items:center;gap:0.3rem;">
              <button type="button" class="pod-step-btn" data-opp-action="cmd-down" data-opp-id="${opp.id}">−</button>
              <span style="min-width:2rem;text-align:center;font-weight:700;color:${lethal ? "#fda4af" : "#f1f5f9"};">${opp.commanderDamage}${lethal ? " ☠" : ""}</span>
              <button type="button" class="pod-step-btn" data-opp-action="cmd-up" data-opp-id="${opp.id}">+</button>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // =================================================================
  // 2. +1/+1 COUNTER TRACKER
  // =================================================================

  function loadCounters() {
    counters = loadJson(deckScopedKey(COUNTERS_STORAGE_KEY), {}) || {};
  }

  function persistCounters() {
    saveJson(deckScopedKey(COUNTERS_STORAGE_KEY), counters);
  }

  function getCounter(cardUid) {
    if (!cardUid || !counters[cardUid]) return 0;
    return counters[cardUid].plus || 0;
  }

  function setCounter(cardUid, value) {
    if (!cardUid) return;
    if (!value || value <= 0) {
      delete counters[cardUid];
    } else {
      counters[cardUid] = { plus: value };
    }
    persistCounters();
    updateCounterBadgesForCard(cardUid);
  }

  function adjustCounter(cardUid, delta) {
    setCounter(cardUid, Math.max(0, getCounter(cardUid) + delta));
  }

  function updateCounterBadgesForCard(cardUid) {
    const cardEls = document.querySelectorAll(`.hand-card[data-card-id="${cardUid}"]`);
    cardEls.forEach(applyCounterBadgeToElement);
  }

  function applyCounterBadgeToElement(cardEl) {
    if (!cardEl) return;
    const uid = cardEl.dataset.cardId;
    if (!uid) return;
    let badge = cardEl.querySelector(".plus-counter-badge");
    const value = getCounter(uid);

    // Only show on creatures on the board.
    const isOnBoard = cardEl.dataset.source === "board";
    const card = findCardByUid(uid);
    const isCreature = !!(card && card.is_creature);

    if (!isOnBoard || !isCreature || value <= 0) {
      if (badge) badge.remove();
      return;
    }

    if (!badge) {
      badge = document.createElement("button");
      badge.type = "button";
      badge.className = "plus-counter-badge";
      badge.setAttribute("aria-label", "Adjust +1/+1 counters");
      badge.title = "Click +/- to adjust. Right-click to clear.";
      badge.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        adjustCounter(uid, 1);
      });
      badge.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (event.shiftKey) {
          setCounter(uid, 0);
        } else {
          adjustCounter(uid, -1);
        }
      });
      cardEl.appendChild(badge);
    }
    badge.textContent = `+${value}/+${value}`;
  }

  function findCardByUid(uid) {
    if (!uid) return null;
    for (const zone of Object.keys(oh.boardState)) {
      const found = (oh.boardState[zone] || []).find((c) => c.__uid === uid);
      if (found) return found;
    }
    return null;
  }

  function refreshAllCounterBadges() {
    document.querySelectorAll('#boardArea .hand-card[data-source="board"]').forEach(applyCounterBadgeToElement);
  }

  // Track the most recently interacted card so we know which one's
  // context menu is open. Right-click and action button click both
  // bubble through the board area where we can capture them.
  let _lastInteractedCardEl = null;
  document.addEventListener("contextmenu", (event) => {
    const cardEl = event.target.closest && event.target.closest('.hand-card[data-source="board"]');
    if (cardEl) _lastInteractedCardEl = cardEl;
  }, true);
  document.addEventListener("click", (event) => {
    const actionBtn = event.target.closest && event.target.closest('.card-action-btn');
    if (!actionBtn) return;
    const cardEl = actionBtn.closest('.hand-card[data-source="board"]');
    if (cardEl) _lastInteractedCardEl = cardEl;
  }, true);

  // Add counter controls to context menu via listener on the board.
  // We can't modify ensureContextMenu, but we can append items after the
  // menu opens by observing it.
  function watchContextMenu() {
    const observer = new MutationObserver(() => {
      const menu = document.getElementById("cardContextMenu");
      if (!menu || menu.hidden) return;
      if (menu.querySelector("[data-counter-injected]")) return;

      const activeCard = detectActiveContextCard(menu);
      if (!activeCard || !activeCard.is_creature) return;
      const zone = activeCard.boardZone;
      if (zone !== "creatures" && zone !== "permanents") return;

      const sep = document.createElement("div");
      sep.dataset.counterInjected = "1";
      sep.style.cssText = "border-top:1px solid rgba(148,163,184,0.18);margin:0.25rem 0;";
      menu.appendChild(sep);

      const uid = activeCard.__uid;
      const current = getCounter(uid);

      const addBtn = document.createElement("button");
      addBtn.type = "button";
      addBtn.className = "card-context-item";
      addBtn.dataset.counterInjected = "1";
      addBtn.textContent = `Add +1/+1 counter${current ? ` (${current})` : ""}`;
      addBtn.addEventListener("click", () => {
        adjustCounter(uid, 1);
        document.getElementById("cardContextMenu").hidden = true;
      });
      menu.appendChild(addBtn);

      if (current > 0) {
        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "card-context-item";
        removeBtn.dataset.counterInjected = "1";
        removeBtn.textContent = "Remove +1/+1 counter";
        removeBtn.addEventListener("click", () => {
          adjustCounter(uid, -1);
          document.getElementById("cardContextMenu").hidden = true;
        });
        menu.appendChild(removeBtn);

        const clearBtn = document.createElement("button");
        clearBtn.type = "button";
        clearBtn.className = "card-context-item";
        clearBtn.dataset.counterInjected = "1";
        clearBtn.textContent = "Clear all counters";
        clearBtn.addEventListener("click", () => {
          setCounter(uid, 0);
          document.getElementById("cardContextMenu").hidden = true;
        });
        menu.appendChild(clearBtn);
      }
    });
    observer.observe(document.body, { attributes: true, subtree: true, attributeFilter: ["hidden"] });
  }

  function detectActiveContextCard(menu) {
    // First, prefer the most recently right-clicked / action-clicked card.
    if (_lastInteractedCardEl && _lastInteractedCardEl.isConnected) {
      const card = findCardByUid(_lastInteractedCardEl.dataset.cardId);
      if (card) return card;
    }
    // Fallback: geometric search at the menu position.
    const x = parseFloat(menu.style.left) || 0;
    const y = parseFloat(menu.style.top) || 0;
    const cards = document.querySelectorAll('#boardArea .hand-card[data-source="board"]');
    let best = null;
    let bestDist = Infinity;
    cards.forEach((el) => {
      const r = el.getBoundingClientRect();
      const dx = Math.max(r.left - x, x - r.right, 0);
      const dy = Math.max(r.top - y, y - r.bottom, 0);
      const d = Math.hypot(dx, dy);
      if (d < bestDist) { bestDist = d; best = el; }
    });
    if (!best || bestDist > 220) return null;
    return findCardByUid(best.dataset.cardId);
  }

  // Wrap renderBoard to refresh counter badges after each render.
  if (typeof oh.renderBoard === "function") {
    const origRender = oh.renderBoard.bind(oh);
    oh.renderBoard = function () {
      origRender();
      refreshAllCounterBadges();
    };
  }

  // =================================================================
  // 3. AUTO-TAP MANA WHEN CASTING
  // =================================================================

  // Wrap moveCardToBoard (already wrapped by triggers.js).
  // To detect "card came from hand", we snapshot hand UIDs at the start
  // of every move and check before the core wrapper splices/renders.
  // The hand splice happens in playCardFromHand BEFORE moveCardToBoard
  // runs, so we need to snapshot before that. Hooking renderHand catches
  // it: renderHand fires after the splice, so by that point the card is
  // gone from `handCards` — we want the *previous* frame's hand. We
  // therefore snapshot inside renderHand AFTER it runs (capturing the
  // post-splice state), and check membership in the snapshot taken
  // BEFORE this renderHand. Sequence:
  //   1. user clicks hand card
  //   2. handler splices card out of handCards
  //   3. handler calls renderHand() → our wrapper updates snapshot to
  //      post-splice state (card NOT in snapshot)
  //   4. handler calls playCardFromHand → moveCardToBoard
  //   5. our move wrapper checks: was card in PREVIOUS snapshot? Need
  //      to keep two snapshots.
  let _prevSnapshot = new Set();
  let _curSnapshot = new Set();

  function snapshotHandIds() {
    _prevSnapshot = _curSnapshot;
    _curSnapshot = new Set((oh.handCards || []).map((c) => c.__uid));
  }

  if (typeof oh.renderHand === "function") {
    const origRenderHand = oh.renderHand.bind(oh);
    oh.renderHand = function () {
      origRenderHand();
      snapshotHandIds();
    };
  }

  const _movePrev = oh.moveCardToBoard.bind(oh);
  oh.moveCardToBoard = function (card, preferredZone, beforeId, stackParentId) {
    const wasInHand = card && (_prevSnapshot.has(card.__uid) || _curSnapshot.has(card.__uid));
    const result = _movePrev(card, preferredZone, beforeId, stackParentId);
    try {
      if (wasInHand && card && !card.is_land) {
        const zone = card.boardZone || preferredZone;
        const isBattlefield = zone === "creatures" || zone === "permanents";
        if (isBattlefield) {
          autoTapForCost(card);
        }
      }
    } catch (err) {
      console.error("[life-counters] auto-tap error:", err);
    }
    return result;
  };

  // Initial snapshots.
  snapshotHandIds();
  snapshotHandIds();

  // -----------------------------------------------------------------
  // Mana cost parsing
  // -----------------------------------------------------------------
  // Parses strings like "{2}{W}{U}" into { generic: 2, W: 1, U: 1 }
  function parseManaCost(costStr) {
    const result = { generic: 0, W: 0, U: 0, B: 0, R: 0, G: 0, C: 0 };
    if (!costStr || typeof costStr !== "string") return result;
    const tokens = costStr.match(/\{[^}]+\}/g) || [];
    for (const tok of tokens) {
      const inner = tok.slice(1, -1).toUpperCase();
      // Pure numeric — generic mana.
      if (/^\d+$/.test(inner)) {
        result.generic += parseInt(inner, 10);
        continue;
      }
      // X / Y / Z — generic placeholder, skip (player picks).
      if (inner === "X" || inner === "Y" || inner === "Z") continue;
      // Hybrid like W/U or 2/W — pick the first colored option.
      if (inner.includes("/")) {
        const parts = inner.split("/");
        // 2/W means "2 generic OR W"; prefer W to ease auto-tap.
        const colored = parts.find((p) => MANA_SYMBOLS.includes(p));
        if (colored) {
          result[colored] += 1;
        } else {
          result.generic += 1;
        }
        continue;
      }
      // Phyrexian {W/P} — assume player pays mana, treat as W.
      if (inner.endsWith("/P")) {
        const sym = inner[0];
        if (MANA_SYMBOLS.includes(sym)) result[sym] += 1;
        continue;
      }
      // Single colored or colorless symbol.
      if (MANA_SYMBOLS.includes(inner)) {
        result[inner] += 1;
        continue;
      }
      // Snow {S} — treat as generic.
      if (inner === "S") {
        result.generic += 1;
      }
    }
    return result;
  }

  function autoTapForCost(card) {
    const autoToggle = document.getElementById("autoModeToggle");
    if (autoToggle && !autoToggle.checked) return;

    const cost = parseManaCost(card.mana_cost || "");
    const totalCost = cost.generic + MANA_SYMBOLS.reduce((s, c) => s + cost[c], 0);
    if (totalCost === 0) return; // free spells / no cost

    // First, see how much is already in the mana pool from prior taps.
    const pool = getManaPool();
    const consumed = consumeFromPool(pool, cost);
    let stillNeed = consumed.remaining;
    let stillNeedTotal = stillNeed.generic + MANA_SYMBOLS.reduce((s, c) => s + stillNeed[c], 0);

    // If the pool covered everything, just deduct and we're done.
    if (stillNeedTotal === 0) {
      writeManaPool(consumed.pool);
      oh.showMessage(`Cast ${card.name}: spent mana from pool.`, "info");
      return;
    }

    // Otherwise, look for untapped lands and try to tap enough to cover.
    const lands = (oh.boardState && oh.boardState.lands) || [];
    const tappedNow = [];
    const colorsLackingMessages = [];

    // Try colored requirements first — find a land that produces that color.
    for (const sym of MANA_SYMBOLS) {
      while (stillNeed[sym] > 0) {
        const land = findUntappedLandFor(lands, sym, tappedNow);
        if (!land) {
          colorsLackingMessages.push(`{${sym}}`);
          stillNeed[sym] -= 1; // skip, mark as missing
          continue;
        }
        land.tapped = true;
        tappedNow.push({ land, color: sym });
        // Add to consumed pool (positive) then remove (zero out for this color).
        stillNeed[sym] -= 1;
      }
    }

    // Generic requirement — any untapped land works.
    while (stillNeed.generic > 0) {
      const land = findAnyUntappedLand(lands, tappedNow);
      if (!land) {
        colorsLackingMessages.push(`{${stillNeed.generic} generic}`);
        break;
      }
      land.tapped = true;
      const produced = primaryColorOf(land) || "C";
      tappedNow.push({ land, color: produced });
      stillNeed.generic -= 1;
    }

    // Re-render board to show taps.
    if (tappedNow.length) {
      if (typeof oh.renderBoard === "function") oh.renderBoard();
    }

    // Update mana pool (pool may have been partially consumed).
    writeManaPool(consumed.pool);

    if (colorsLackingMessages.length) {
      oh.showMessage(
        `Cast ${card.name} — short ${colorsLackingMessages.join(", ")}. Tapped ${tappedNow.length} land(s).`,
        "warning"
      );
    } else if (tappedNow.length) {
      oh.showMessage(
        `Cast ${card.name}: auto-tapped ${tappedNow.length} land${tappedNow.length === 1 ? "" : "s"}.`,
        "info"
      );
    }
  }

  // Returns the mana pool as a {W,U,B,R,G,C,generic} object by reading
  // the trigger panel's pool display chips.
  function getManaPool() {
    const display = document.getElementById("manaPoolDisplay");
    const pool = { W: 0, U: 0, B: 0, R: 0, G: 0, C: 0, generic: 0 };
    if (!display || display.hidden) return pool;
    display.querySelectorAll(".mana-pip").forEach((pip) => {
      const txt = (pip.textContent || "").trim();
      // Match "3{W}" or "{W}".
      const m = txt.match(/^(\d+)?\{([WUBRGC])\}$/);
      if (!m) return;
      const count = m[1] ? parseInt(m[1], 10) : 1;
      const sym = m[2];
      if (pool[sym] !== undefined) pool[sym] += count;
    });
    return pool;
  }

  function writeManaPool(pool) {
    // The triggers.js module owns the live `manaPool` object. We can't
    // reach into its closure, but we *can* signal a clear+repopulate via
    // a CustomEvent that triggers.js will need to listen for. As a
    // pragmatic substitute, we simulate untap-then-add: dispatch
    // synthetic events on the untapAllBtn (which clears the pool), then
    // re-tap virtual sources. That's noisy. Simpler: mutate the chip DOM
    // directly to reflect the new total, accepting that triggers.js
    // internal state will desync until the next untap.
    const display = document.getElementById("manaPoolDisplay");
    if (!display) return;
    display.innerHTML = "";
    const total = MANA_SYMBOLS.reduce((s, sym) => s + (pool[sym] || 0), 0);
    if (total === 0) {
      display.hidden = true;
      return;
    }
    display.hidden = false;
    const label = document.createElement("span");
    label.style.cssText = "font-size:0.7rem;color:rgba(148,163,184,0.7);margin-right:0.2rem;";
    label.textContent = "Mana:";
    display.appendChild(label);
    MANA_SYMBOLS.filter((s) => (pool[s] || 0) > 0).forEach((s) => {
      const pip = document.createElement("span");
      pip.className = `mana-pip mana-${s.toLowerCase()}`;
      pip.textContent = pool[s] > 1 ? `${pool[s]}{${s}}` : `{${s}}`;
      display.appendChild(pip);
    });
  }

  // Spend from pool first; returns { pool: remaining-pool, remaining: cost-still-owed }
  function consumeFromPool(pool, cost) {
    const newPool = { ...pool };
    const remaining = { ...cost };
    // Pay colored requirements with matching pool mana.
    for (const sym of MANA_SYMBOLS) {
      while (remaining[sym] > 0 && (newPool[sym] || 0) > 0) {
        newPool[sym] -= 1;
        remaining[sym] -= 1;
      }
    }
    // Pay generic with any pool mana (prefer colorless first).
    const genericOrder = ["C", "W", "U", "B", "R", "G"];
    for (const sym of genericOrder) {
      while (remaining.generic > 0 && (newPool[sym] || 0) > 0) {
        newPool[sym] -= 1;
        remaining.generic -= 1;
      }
    }
    return { pool: newPool, remaining };
  }

  function findUntappedLandFor(lands, sym, alreadyTapped) {
    const skipUids = new Set(alreadyTapped.map((e) => e.land.__uid));
    return lands.find((land) => {
      if (land.tapped || skipUids.has(land.__uid)) return false;
      const produced = landColors(land);
      return produced.includes(sym);
    });
  }

  function findAnyUntappedLand(lands, alreadyTapped) {
    const skipUids = new Set(alreadyTapped.map((e) => e.land.__uid));
    return lands.find((land) => !land.tapped && !skipUids.has(land.__uid));
  }

  function primaryColorOf(land) {
    const colors = landColors(land);
    return colors[0] || null;
  }

  function landColors(land) {
    if (!land) return [];
    const text = (land.oracle_text || "").toLowerCase();
    const typeLine = (land.type_line || "").toLowerCase();
    const colors = [];

    if (/add\s+(?:one mana of any color|mana of any color)/i.test(text)) {
      return ["W", "U", "B", "R", "G"];
    }

    if (/add\s+\{c\}/i.test(text)) colors.push("C");
    const re = /add\s+(?:\{([wubrgc])(?:\/[wubrgc])?\})+/gi;
    let match;
    while ((match = re.exec(text)) !== null) {
      const sym = match[1].toUpperCase();
      if (MANA_SYMBOLS.includes(sym) && !colors.includes(sym)) colors.push(sym);
    }
    const subtypeMap = { plains: "W", island: "U", swamp: "B", mountain: "R", forest: "G" };
    Object.entries(subtypeMap).forEach(([sub, sym]) => {
      if (typeLine.includes(sub) && !colors.includes(sym)) colors.push(sym);
    });

    return colors;
  }

  // =================================================================
  // STYLES
  // =================================================================
  const style = document.createElement("style");
  style.textContent = `
    .hand-status .life-tracker {
      display: inline-flex;
      align-items: center;
      gap: 0.2rem;
      padding: 0.15rem 0.45rem;
      font-size: 0.72rem;
      line-height: 1;
      vertical-align: middle;
    }
    .hand-status .life-tracker .life-icon {
      color: #fca5a5;
      font-size: 0.78rem;
      margin-right: 0.05rem;
    }
    .hand-status .life-tracker .life-btn {
      background: transparent;
      border: 0;
      color: inherit;
      opacity: 0.85;
      font-weight: 600;
      width: 1.15rem;
      height: 1.15rem;
      border-radius: 0.25rem;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      padding: 0;
      line-height: 1;
      font-size: 0.85rem;
      transition: background 0.15s ease, opacity 0.15s ease;
    }
    .hand-status .life-tracker .life-btn:hover {
      background: rgba(255, 255, 255, 0.12);
      opacity: 1;
    }
    .hand-status .life-tracker .life-btn:focus-visible {
      outline: 2px solid rgba(96, 165, 250, 0.7);
      outline-offset: 1px;
    }
    .hand-status .life-tracker .life-display {
      min-width: 1.4rem;
      text-align: center;
      font-weight: 700;
      cursor: pointer;
      padding: 0 0.15rem;
      border-radius: 0.25rem;
      font-size: 0.75rem;
      letter-spacing: 0.02em;
    }
    .hand-status .life-tracker .life-display:hover {
      background: rgba(255, 255, 255, 0.12);
    }
    .hand-status .life-tracker .life-display.life-low {
      color: #fca5a5;
    }
    .hand-status .life-tracker .life-display.life-dead {
      color: #f87171;
      text-decoration: line-through;
    }
    .hand-status .life-tracker .life-pod {
      position: relative;
      margin-left: 0.1rem;
      font-size: 0.75rem;
    }
    .hand-status .life-tracker .life-pod-badge {
      position: absolute;
      top: -3px;
      right: -3px;
      background: #f59e0b;
      color: #1e293b;
      font-size: 0.55rem;
      font-weight: 700;
      min-width: 0.85rem;
      height: 0.85rem;
      padding: 0 0.15rem;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      pointer-events: none;
      line-height: 1;
    }

    .pod-icon-btn {
      background: transparent;
      border: 0;
      color: rgba(148, 163, 184, 0.7);
      cursor: pointer;
      padding: 0.2rem 0.35rem;
      border-radius: 0.3rem;
      font-size: 0.8rem;
      line-height: 1;
    }
    .pod-icon-btn:hover {
      color: #e2e8f0;
      background: rgba(148, 163, 184, 0.15);
    }
    .pod-icon-danger:hover {
      color: #fda4af;
      background: rgba(244, 63, 94, 0.15);
    }
    .pod-step-btn {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid rgba(148, 163, 184, 0.25);
      color: #e2e8f0;
      width: 1.5rem;
      height: 1.5rem;
      border-radius: 0.35rem;
      cursor: pointer;
      font-size: 0.85rem;
      font-weight: 700;
      line-height: 1;
      padding: 0;
    }
    .pod-step-btn:hover {
      background: rgba(30, 41, 59, 0.9);
    }

    .plus-counter-badge {
      position: absolute;
      top: 0.35rem;
      left: 0.35rem;
      background: rgba(34, 197, 94, 0.95);
      color: #052e16;
      border: 2px solid #f0fdf4;
      border-radius: 999px;
      font-size: 0.7rem;
      font-weight: 800;
      padding: 0.15rem 0.45rem;
      z-index: 5;
      cursor: pointer;
      box-shadow: 0 0.25rem 0.65rem rgba(2, 6, 23, 0.4);
      line-height: 1;
      letter-spacing: 0.02em;
    }
    .plus-counter-badge:hover {
      transform: scale(1.08);
    }
    .plus-counter-badge:focus-visible {
      outline: 2px solid rgba(96, 165, 250, 0.7);
      outline-offset: 2px;
    }
  `;
  document.head.appendChild(style);

  // =================================================================
  // INITIALIZE
  // =================================================================
  restoreLife();
  loadCounters();
  buildLifeTracker();
  renderLife();
  watchContextMenu();
  refreshAllCounterBadges();

  // Persist on page unload too.
  window.addEventListener("beforeunload", () => {
    persistLife();
    persistCounters();
  });

  // Expose minimal API for tests / debugging.
  window.__openingHandLifeCounters = {
    getLifeTotal: () => lifeTotal,
    setLifeTotal: (v) => { lifeTotal = v; renderLife(); persistLife(); },
    getOpponents: () => opponents,
    getCounter,
    setCounter,
    parseManaCost,
    autoTapForCost,
  };
})();
