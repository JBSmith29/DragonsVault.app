/*
 * opening-hand-life-counters.js
 *
 * Supplemental module for the Opening Hand simulator. Adds:
 *
 *   1. Life Tracker — primary life total with +/- buttons, optional
 *      commander damage matrix for multiplayer pods. Persists per-deck.
 *   2. +1/+1 Counter Tracker — increment/decrement counters on creatures
 *      via a small badge overlaid on the card; integrates with the
 *      context menu.
 *
 * Auto-tap mana on cast and the mana-pool tracker were removed at user
 * request.
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
    // Idempotent — if the tracker already exists, skip and clean up dupes.
    const existing = document.querySelectorAll("#lifeTrackerWrap, .life-tracker");
    if (existing.length >= 1) {
      existing.forEach((el, idx) => { if (idx > 0) el.remove(); });
      return;
    }

    // Centred in the bottom action bar (between "Select New Deck" and
    // "Scry / Surveil"); fall back to the board status HUD, then the hand bar.
    const host =
      document.getElementById("actionLifeHost") ||
      document.getElementById("boardStatusPanel") ||
      document.querySelector(".hand-status");
    if (!host) return;

    const wrap = document.createElement("div");
    wrap.id = "lifeTrackerWrap";
    wrap.className = "life-tracker";
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", "Life total");
    wrap.innerHTML = `
      <button type="button" class="life-btn life-minus" data-life-delta="-1" aria-label="Decrease life" title="Subtract 1 (right-click: −5)">−</button>
      <span class="life-display" id="lifeDisplay" title="Click to set life total">${lifeTotal}</span>
      <button type="button" class="life-btn life-plus" data-life-delta="1" aria-label="Increase life" title="Add 1 (right-click: +5)">+</button>
      <button type="button" class="life-btn life-pod" id="lifePodBtn" aria-label="Manage opponents (commander damage)" title="Multiplayer pod &amp; commander damage">
        <i class="bi bi-people-fill" aria-hidden="true"></i>
      </button>
    `;
    host.appendChild(wrap);

    wrap.querySelectorAll("[data-life-delta]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const delta = parseInt(btn.dataset.lifeDelta, 10) || 0;
        adjustLife(delta);
      });
    });

    wrap.querySelector(".life-minus").addEventListener("contextmenu", (e) => {
      e.preventDefault();
      adjustLife(-5);
    });
    wrap.querySelector(".life-plus").addEventListener("contextmenu", (e) => {
      e.preventDefault();
      adjustLife(5);
    });

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
  // 2. +1/+1 and −1/−1 COUNTER TRACKER
  //
  // Each card slot in `counters` is { plus: int, minus: int }. Both
  // badges render simultaneously when present so the player can see
  // them stacking before deciding when to apply state-based actions.
  // =================================================================

  function loadCounters() {
    const raw = loadJson(deckScopedKey(COUNTERS_STORAGE_KEY), {}) || {};
    // Backfill: older versions stored just { plus: N }; coerce any missing
    // minus key to 0 so reads are uniform.
    Object.keys(raw).forEach((uid) => {
      const entry = raw[uid] || {};
      raw[uid] = {
        plus: Math.max(0, parseInt(entry.plus, 10) || 0),
        minus: Math.max(0, parseInt(entry.minus, 10) || 0),
      };
    });
    counters = raw;
  }

  function persistCounters() {
    saveJson(deckScopedKey(COUNTERS_STORAGE_KEY), counters);
  }

  function getCounters(cardUid) {
    if (!cardUid || !counters[cardUid]) return { plus: 0, minus: 0 };
    return {
      plus: counters[cardUid].plus || 0,
      minus: counters[cardUid].minus || 0,
    };
  }

  function setCounter(cardUid, kind, value) {
    if (!cardUid || (kind !== "plus" && kind !== "minus")) return;
    const safe = Math.max(0, parseInt(value, 10) || 0);
    const current = getCounters(cardUid);
    const next = { ...current, [kind]: safe };
    if (next.plus === 0 && next.minus === 0) {
      delete counters[cardUid];
    } else {
      counters[cardUid] = next;
    }
    persistCounters();
    updateCounterBadgesForCard(cardUid);
  }

  function clearCounters(cardUid) {
    if (!cardUid) return;
    delete counters[cardUid];
    persistCounters();
    updateCounterBadgesForCard(cardUid);
  }

  function adjustCounter(cardUid, kind, delta) {
    const current = getCounters(cardUid);
    setCounter(cardUid, kind, (current[kind] || 0) + delta);
  }

  function updateCounterBadgesForCard(cardUid) {
    const cardEls = document.querySelectorAll(`.hand-card[data-card-id="${cardUid}"]`);
    cardEls.forEach(applyCounterBadgeToElement);
  }

  function ensureBadge(cardEl, kind) {
    const klass = kind === "plus" ? "plus-counter-badge" : "minus-counter-badge";
    let badge = cardEl.querySelector("." + klass);
    if (badge) return badge;
    badge = document.createElement("button");
    badge.type = "button";
    badge.className = `badge ${kind === "plus" ? "text-bg-success" : "text-bg-danger"} ${klass}`;
    const top = kind === "plus" ? "0.45rem" : "2.05rem";
    badge.style.cssText = [
      "position: absolute",
      `top: ${top}`,
      "right: 0.45rem",
      "z-index: 7",
      "padding: 0.25rem 0.5rem",
      "font-size: 0.7rem",
      "font-weight: 700",
      "letter-spacing: 0.02em",
      "border: 1px solid rgba(255,255,255,0.45)",
      "border-radius: 999px",
      "cursor: pointer",
      "box-shadow: 0 0.25rem 0.65rem rgba(2, 6, 23, 0.45)",
      "line-height: 1",
      "min-width: 2.4rem",
    ].join(";");
    badge.setAttribute(
      "aria-label",
      kind === "plus" ? "Adjust +1/+1 counters" : "Adjust −1/−1 counters"
    );
    badge.title =
      kind === "plus"
        ? "Click +1, right-click −1, shift+right-click clear"
        : "Click +1, right-click −1, shift+right-click clear (−1/−1 counters)";
    badge.dataset.kind = kind;
    badge.addEventListener("click", (event) => {
      const uid = cardEl.dataset.cardId;
      if (!uid) return;
      event.preventDefault();
      event.stopPropagation();
      adjustCounter(uid, kind, 1);
    });
    badge.addEventListener("contextmenu", (event) => {
      const uid = cardEl.dataset.cardId;
      if (!uid) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.shiftKey) {
        setCounter(uid, kind, 0);
      } else {
        adjustCounter(uid, kind, -1);
      }
    });
    cardEl.appendChild(badge);
    return badge;
  }

  function applyCounterBadgeToElement(cardEl) {
    if (!cardEl) return;
    const uid = cardEl.dataset.cardId;
    if (!uid) return;

    const isOnBoard = cardEl.dataset.source === "board";
    const { plus, minus } = getCounters(uid);

    // Always show counters that have been put on a card while it is on
    // the board (any zone). Hand / library views never show counters —
    // counters only exist for permanents.
    const showPlus = isOnBoard && plus > 0;
    const showMinus = isOnBoard && minus > 0;

    let plusBadge = cardEl.querySelector(".plus-counter-badge");
    if (!showPlus && plusBadge) plusBadge.remove();
    if (showPlus) {
      plusBadge = ensureBadge(cardEl, "plus");
      plusBadge.textContent = `+${plus}/+${plus}`;
    }

    let minusBadge = cardEl.querySelector(".minus-counter-badge");
    if (!showMinus && minusBadge) minusBadge.remove();
    if (showMinus) {
      minusBadge = ensureBadge(cardEl, "minus");
      minusBadge.textContent = `−${minus}/−${minus}`;
    }
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
  // context menu is open.
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

  function watchContextMenu() {
    const observer = new MutationObserver(() => {
      const menu = document.getElementById("cardContextMenu");
      if (!menu || menu.hidden) return;
      if (menu.querySelector("[data-counter-injected]")) return;

      const activeCard = detectActiveContextCard(menu);
      if (!activeCard) return;
      // Counters live on permanents — show on creatures, other permanents,
      // and lands (animated lands etc.). Skip graveyard / command zone.
      const zone = activeCard.boardZone;
      if (zone === "graveyard" || zone === "command") return;

      const uid = activeCard.__uid;
      const { plus, minus } = getCounters(uid);

      const sep = document.createElement("div");
      sep.dataset.counterInjected = "1";
      sep.style.cssText = "border-top:1px solid rgba(148,163,184,0.18);margin:0.25rem 0;";
      menu.appendChild(sep);

      function makeItem(label, onClick) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "card-context-item";
        btn.dataset.counterInjected = "1";
        btn.textContent = label;
        btn.addEventListener("click", () => {
          onClick();
          document.getElementById("cardContextMenu").hidden = true;
        });
        menu.appendChild(btn);
        return btn;
      }

      makeItem(
        `Add +1/+1 counter${plus ? ` (${plus})` : ""}`,
        () => adjustCounter(uid, "plus", 1)
      );
      if (plus > 0) {
        makeItem("Remove +1/+1 counter", () => adjustCounter(uid, "plus", -1));
      }

      makeItem(
        `Add −1/−1 counter${minus ? ` (${minus})` : ""}`,
        () => adjustCounter(uid, "minus", 1)
      );
      if (minus > 0) {
        makeItem("Remove −1/−1 counter", () => adjustCounter(uid, "minus", -1));
      }

      if (plus > 0 || minus > 0) {
        makeItem("Clear all counters", () => clearCounters(uid));
      }
    });
    observer.observe(document.body, { attributes: true, subtree: true, attributeFilter: ["hidden"] });
  }

  function detectActiveContextCard(menu) {
    if (_lastInteractedCardEl && _lastInteractedCardEl.isConnected) {
      const card = findCardByUid(_lastInteractedCardEl.dataset.cardId);
      if (card) return card;
    }
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
  // STYLES
  // =================================================================
  const style = document.createElement("style");
  style.textContent = `
    /* Big "− 40 +" life counter, transparent so it blends into the action bar. */
    .life-tracker {
      display: inline-flex;
      align-items: center;
      flex-wrap: nowrap;
      gap: 0.35rem;
      padding: 0;
      background: transparent;
      border: 0;
      box-shadow: none;
      line-height: 1;
    }
    .life-tracker .life-btn {
      background: transparent;
      border: 0;
      color: #94a3b8;
      font-weight: 700;
      width: 2rem;
      height: 2rem;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      padding: 0;
      line-height: 1;
      font-size: 1.9rem;
      transition: color 0.15s ease, transform 0.1s ease;
    }
    .life-tracker .life-minus:hover { color: #f87171; }
    .life-tracker .life-plus:hover { color: #4ade80; }
    .life-tracker .life-btn:active { transform: scale(0.88); }
    .life-tracker .life-btn:focus-visible {
      outline: 2px solid rgba(96, 165, 250, 0.7);
      outline-offset: 1px;
      border-radius: 0.4rem;
    }
    .life-tracker .life-display {
      min-width: 2ch;
      text-align: center;
      font-weight: 800;
      cursor: pointer;
      padding: 0 0.1rem;
      font-size: 2.4rem;
      color: #f8fafc;
      font-variant-numeric: tabular-nums;
      letter-spacing: -0.01em;
      line-height: 1;
      transition: color 0.15s ease;
    }
    .life-tracker .life-display:hover {
      color: #ffffff;
    }
    .life-tracker .life-display.life-low {
      color: #fbbf24;
    }
    .life-tracker .life-display.life-dead {
      color: #f87171;
      text-decoration: line-through;
    }
    .life-tracker .life-pod {
      position: relative;
      margin-left: 0.4rem;
      width: 1.7rem;
      height: 1.7rem;
      font-size: 0.95rem;
      color: #64748b;
      align-self: center;
    }
    .life-tracker .life-pod:hover { color: #cbd5e1; }
    .life-tracker .life-pod-badge {
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

    .plus-counter-badge.plus-counter-badge,
    .minus-counter-badge.minus-counter-badge {
      position: absolute;
      right: 0.45rem;
      z-index: 7;
      padding: 0.25rem 0.5rem;
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.02em;
      border: 1px solid rgba(255, 255, 255, 0.45);
      border-radius: 999px;
      cursor: pointer;
      box-shadow: 0 0.25rem 0.65rem rgba(2, 6, 23, 0.45);
      line-height: 1;
      min-width: 2.4rem;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .plus-counter-badge.plus-counter-badge { top: 0.45rem; }
    .minus-counter-badge.minus-counter-badge { top: 2.05rem; }
    .plus-counter-badge.plus-counter-badge:hover,
    .minus-counter-badge.minus-counter-badge:hover {
      transform: scale(1.06);
      box-shadow: 0 0.4rem 0.9rem rgba(2, 6, 23, 0.55);
    }
    .plus-counter-badge:focus-visible,
    .minus-counter-badge:focus-visible {
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

  window.addEventListener("beforeunload", () => {
    persistLife();
    persistCounters();
  });

  window.__openingHandLifeCounters = {
    getLifeTotal: () => lifeTotal,
    setLifeTotal: (v) => { lifeTotal = v; renderLife(); persistLife(); },
    getOpponents: () => opponents,
    getCounters,
    setCounter,
    clearCounters,
    adjustCounter,
  };
})();
