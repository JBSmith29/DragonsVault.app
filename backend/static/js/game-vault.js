/* Game Vault — self-contained front-end.
 * No framework, no shared app JS. Talks only to /game-vault/api/*.
 */
(function () {
  "use strict";

  const CFG = window.GAME_VAULT || {};
  const API = CFG.apiBase || "/game-vault/api";
  const WIN_CONDITIONS = CFG.winConditions || [];
  const SOURCES = CFG.knownSources || ["archidekt", "moxfield", "mtggoldfish"];

  const state = { players: [], games: [], stats: {} };

  const WIN_LABELS = {
    combat: "Combat damage", combo: "Combo", commander_damage: "Commander damage",
    mill: "Mill", alt_win: "Alternate win", other: "Other",
  };
  const SOURCE_LABELS = { archidekt: "Archidekt", moxfield: "Moxfield", mtggoldfish: "MTGGoldfish", manual: "Manual" };

  /* ---------------------------------------------------------------- utils */
  function h(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }
  const $ = (sel, root) => (root || document).querySelector(sel);
  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
  const initials = (name) => (name || "?").trim().split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  async function api(method, path, body) {
    const opts = {
      method,
      headers: { "Accept": "application/json" },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      if (CFG.csrfToken) opts.headers["X-CSRFToken"] = CFG.csrfToken;
      opts.body = JSON.stringify(body);
    } else if (method !== "GET" && CFG.csrfToken) {
      opts.headers["X-CSRFToken"] = CFG.csrfToken;
    }
    let resp;
    try {
      resp = await fetch(API + path, opts);
    } catch (e) {
      throw new Error("Network error — check your connection.");
    }
    let data = null;
    try { data = await resp.json(); } catch (e) { /* non-json */ }
    if (!resp.ok) {
      throw new Error((data && data.error) || `Request failed (${resp.status}).`);
    }
    return data || {};
  }

  /* --------------------------------------------------------------- toasts */
  function toast(message, kind) {
    const host = $("#gvToasts");
    const icon = kind === "err" ? "bi-exclamation-octagon" : kind === "ok" ? "bi-check-circle" : "bi-info-circle";
    const node = h("div", { class: `gv-toast ${kind || "info"}` },
      h("i", { class: `bi ${icon}` }), h("span", { text: message }));
    host.append(node);
    setTimeout(() => { node.style.opacity = "0"; node.style.transition = "opacity .3s"; setTimeout(() => node.remove(), 300); }, 3600);
  }

  /* --------------------------------------------------------------- modals */
  const overlay = () => $("#gvModalOverlay");
  function openModal(title, bodyNode, footButtons) {
    $("#gvModalTitle").textContent = title;
    const body = $("#gvModalBody"); clear(body); body.append(bodyNode);
    const foot = $("#gvModalFoot"); clear(foot);
    (footButtons || []).forEach((b) => foot.append(b));
    overlay().classList.add("open");
    document.body.style.overflow = "hidden";
    const focusable = body.querySelector("input, select, textarea, button");
    if (focusable) setTimeout(() => focusable.focus(), 60);
  }
  function closeModal() {
    overlay().classList.remove("open");
    document.body.style.overflow = "";
  }
  function btn(label, kind, onClick, opts) {
    const b = h("button", { type: "button", class: `gv-btn ${kind || ""}`.trim() }, label);
    if (opts && opts.icon) b.prepend(h("i", { class: `bi ${opts.icon}` }));
    b.addEventListener("click", onClick);
    return b;
  }
  function field(labelText, control, hint) {
    return h("div", { class: "gv-field" },
      h("label", { text: labelText }), control, hint ? h("div", { class: "gv-hint", text: hint }) : null);
  }

  /* -------------------------------------------------------------- widgets */
  function pips(colors) {
    const wrap = h("span", { class: "gv-pips" });
    (colors || []).forEach((c) => wrap.append(h("span", { class: `gv-pip ${c}`, title: c })));
    return wrap;
  }
  function sourceBadge(source) {
    return h("span", { class: `gv-badge src-${source || "manual"}` }, SOURCE_LABELS[source] || source || "Manual");
  }

  /* ------------------------------------------------------------ dashboard */
  function renderStats() {
    const s = state.stats || {};
    const host = $("#gvStats"); clear(host);
    const tiles = [
      { n: s.total_games || 0, l: "Games logged", i: "bi-dice-5", c: "var(--gv-blue)" },
      { n: s.total_players || 0, l: "Players", i: "bi-people", c: "var(--gv-purple)" },
      { n: s.total_decks || 0, l: "Decks tracked", i: "bi-collection", c: "var(--gv-green)" },
    ];
    tiles.forEach((t) => host.append(
      h("div", { class: "gv-stat" },
        h("div", { class: "gv-stat-num", text: String(t.n) }),
        h("div", { class: "gv-stat-label" }, h("i", { class: `bi ${t.i}`, style: `color:${t.c};margin-right:.4rem` }), t.l))
    ));
  }

  function board(title, icon, rows) {
    const panel = h("div", { class: "gv-panel gv-board" }, h("h3", {}, h("i", { class: `bi ${icon}` }), title));
    if (!rows || !rows.length) {
      panel.append(h("div", { class: "gv-empty", text: "No data yet." }));
      return panel;
    }
    rows.slice(0, 6).forEach((r) => {
      panel.append(h("div", { class: "gv-rank" },
        h("div", { class: "gv-rank-name" }, r.label,
          h("div", { class: "gv-rank-meta", text: `${r.wins}W · ${r.games} games` })),
        h("div", { class: "gv-winbar" }, h("span", { style: `width:${Math.round(r.win_rate)}%` })),
        h("div", { class: "gv-winpct", text: `${r.win_rate}%` })));
    });
    return panel;
  }
  function renderBoards() {
    const s = state.stats || {};
    const host = $("#gvBoards"); clear(host);
    host.append(board("Top players", "bi-trophy", s.players));
    host.append(board("Top decks", "bi-layers", s.decks));
    host.append(board("Top commanders", "bi-person-badge", s.commanders));
    host.append(board("Win rate by turn order", "bi-sort-numeric-down", s.turn_order));
  }

  /* ---------------------------------------------------------------- games */
  function gameCard(game, opts) {
    const card = h("div", { class: "gv-panel gv-game" });
    const top = h("div", { class: "gv-game-top" },
      h("span", { class: "gv-game-date" }, h("i", { class: "bi bi-calendar3", style: "margin-right:.4rem;color:var(--gv-muted)" }), game.played_at_label || "—"));
    const meta = h("div", { class: "gv-game-meta" });
    if (game.turns) meta.append(h("span", { text: `${game.turns} turns` }));
    if (game.win_condition) meta.append(h("span", { text: WIN_LABELS[game.win_condition] || game.win_condition }));
    if (game.infinite_win) meta.append(h("span", { class: "gv-badge gv-inf" }, h("i", { class: "bi bi-infinity" }), " Infinite"));
    top.append(meta);
    if (opts && opts.actions) {
      const acts = h("div", { style: "margin-left:auto; display:flex; gap:.3rem" },
        h("button", { class: "gv-btn gv-btn-ghost gv-btn-sm gv-btn-icon", title: "Edit game",
          onclick: () => openLogGame(game) }, h("i", { class: "bi bi-pencil" })),
        h("button", { class: "gv-btn gv-btn-danger gv-btn-sm gv-btn-icon", title: "Delete game",
          onclick: () => confirmDelete("game", game) }, h("i", { class: "bi bi-trash" })));
      top.append(acts);
    }
    card.append(top);

    const seats = h("div", { class: "gv-seats" });
    (game.participants || []).forEach((p) => {
      const seat = h("div", { class: `gv-seat ${p.is_winner ? "winner" : ""}` });
      if (p.turn_order) seat.append(h("span", { class: "gv-turn-chip", title: `${ordinal(p.turn_order)} to play` }, `T${p.turn_order}`));
      if (p.is_winner) seat.append(h("i", { class: "bi bi-trophy-fill gv-crown", title: "Winner" }));
      // Show the commander (the stable identity); fall back to the deck name.
      const identity = p.commander_name || p.deck_name;
      seat.append(h("div", { style: "min-width:0" },
        h("div", { class: "gv-seat-player", text: p.player_name || "Unknown" }),
        identity ? h("div", { class: "gv-seat-deck", text: identity }) : null));
      seats.append(seat);
    });
    card.append(seats);
    if (game.notes) card.append(h("div", { class: "gv-game-meta", style: "margin-top:.6rem" }, h("i", { class: "bi bi-chat-left-text", style: "margin-right:.4rem" }), game.notes));
    return card;
  }

  function renderGames() {
    const recent = $("#gvRecentGames"); clear(recent);
    const all = $("#gvAllGames"); clear(all);
    if (!state.games.length) {
      recent.append(blank("bi-dice-5", "No games logged yet.", "Log game", () => openLogGame()));
      all.append(blank("bi-dice-5", "No games logged yet.", "Log game", () => openLogGame()));
      return;
    }
    state.games.slice(0, 6).forEach((g) => recent.append(gameCard(g, { actions: true })));
    state.games.forEach((g) => all.append(gameCard(g, { actions: true })));
  }

  /* -------------------------------------------------------------- players */
  function deckRow(deck) {
    const row = h("div", { class: "gv-deck" });
    row.append(deck.commander_image
      ? h("img", { class: "gv-deck-art", src: deck.commander_image, alt: "", loading: "lazy" })
      : h("div", { class: "gv-deck-art" }));
    const sub = h("div", { class: "gv-deck-sub" }, sourceBadge(deck.source));
    if (deck.colors && deck.colors.length) sub.append(pips(deck.colors));
    if (deck.bracket) sub.append(h("span", { class: "gv-badge gv-bracket", text: `Bracket ${deck.bracket}` }));
    if (deck.commander_name) sub.append(h("span", { text: deck.commander_name }));
    if (deck.sync_status === "error") sub.append(h("span", { style: "color:var(--gv-red)", title: deck.sync_error || "Sync failed" }, h("i", { class: "bi bi-exclamation-triangle" })));
    row.append(h("div", { class: "gv-deck-main" },
      h("div", { class: "gv-deck-name", text: deck.name }), sub));

    const actions = h("div", { class: "gv-deck-actions" });
    if (deck.url) actions.append(h("a", { class: "gv-btn gv-btn-ghost gv-btn-sm gv-btn-icon", href: deck.url, target: "_blank", rel: "noopener", title: "Open source" }, h("i", { class: "bi bi-box-arrow-up-right" })));
    if (deck.source !== "manual") actions.append(h("button", { class: "gv-btn gv-btn-ghost gv-btn-sm gv-btn-icon", title: "Re-sync from source", onclick: (e) => syncDeck(deck, e.currentTarget) }, h("i", { class: "bi bi-arrow-repeat" })));
    actions.append(h("button", { class: "gv-btn gv-btn-danger gv-btn-sm gv-btn-icon", title: "Remove deck", onclick: () => confirmDelete("deck", deck) }, h("i", { class: "bi bi-trash" })));
    row.append(actions);
    return row;
  }

  function playerCard(player) {
    const card = h("div", { class: "gv-panel gv-player-card" });
    const avatar = h("div", { class: "gv-avatar", text: initials(player.name) });
    if (player.color) avatar.style.background = player.color;
    const top = h("div", { class: "gv-player-top" }, avatar,
      h("div", {}, h("div", { class: "gv-player-name", text: player.name }),
        player.note ? h("div", { class: "gv-player-note", text: player.note }) : null),
      h("div", { class: "gv-player-actions" },
        h("button", { class: "gv-btn gv-btn-ghost gv-btn-sm gv-btn-icon", title: "Edit player", onclick: () => openPlayerModal(player) }, h("i", { class: "bi bi-pencil" })),
        h("button", { class: "gv-btn gv-btn-danger gv-btn-sm gv-btn-icon", title: "Delete player", onclick: () => confirmDelete("player", player) }, h("i", { class: "bi bi-trash" }))));
    card.append(top);

    const list = h("div", { class: "gv-deck-list" });
    if (player.decks && player.decks.length) player.decks.forEach((d) => list.append(deckRow(d)));
    else list.append(h("div", { class: "gv-empty", text: "No decks yet — import one below." }));
    card.append(list);

    card.append(h("button", { class: "gv-btn gv-btn-primary gv-btn-sm", style: "align-self:flex-start", onclick: () => openImportModal(player) },
      h("i", { class: "bi bi-cloud-download" }), " Import deck"));
    return card;
  }

  function renderPlayers() {
    const host = $("#gvPlayers"); clear(host);
    if (!state.players.length) {
      host.append(blank("bi-people", "No players yet.", "Add player", () => openPlayerModal()));
      return;
    }
    state.players.forEach((p) => host.append(playerCard(p)));
  }

  function blank(icon, msg, cta, onCta) {
    const node = h("div", { class: "gv-panel gv-blank" }, h("i", { class: `bi ${icon}` }), h("div", { text: msg }));
    if (cta) node.append(h("button", { class: "gv-btn gv-btn-primary gv-btn-sm", style: "margin-top:1rem", onclick: onCta }, cta));
    return node;
  }

  /* ---------------------------------------------------- player modal */
  function openPlayerModal(player) {
    const isEdit = !!player;
    const name = h("input", { class: "gv-input", type: "text", maxlength: "120", placeholder: "e.g. Alex", value: (player && player.name) || "" });
    const note = h("input", { class: "gv-input", type: "text", maxlength: "255", placeholder: "Optional — playstyle, pod, etc.", value: (player && player.note) || "" });
    const color = h("input", { class: "gv-input", type: "color", value: (player && player.color) || "#8b5cf6", style: "height:42px;padding:.2rem" });
    const body = h("div", {},
      field("Player name", name),
      field("Note", note),
      field("Accent colour", color));
    const save = btn(isEdit ? "Save" : "Add player", "gv-btn-primary", async () => {
      const payload = { name: name.value.trim(), note: note.value.trim(), color: color.value };
      if (!payload.name) { toast("Enter a player name.", "err"); return; }
      save.disabled = true;
      try {
        if (isEdit) await api("PATCH", `/players/${player.id}`, payload);
        else await api("POST", "/players", payload);
        toast(isEdit ? "Player updated." : "Player added.", "ok");
        closeModal(); await reload();
      } catch (e) { toast(e.message, "err"); save.disabled = false; }
    }, { icon: isEdit ? "bi-check-lg" : "bi-person-plus" });
    openModal(isEdit ? "Edit player" : "Add player", body, [btn("Cancel", "gv-btn-ghost", closeModal), save]);
  }

  /* ---------------------------------------------------- import modal */
  function openImportModal(player) {
    // Mode toggle: paste a link, or look up by username (Archidekt/Moxfield).
    const seg = h("div", { class: "gv-seg" },
      h("button", { type: "button", class: "active", dataset: { mode: "url" }, text: "Paste a link" }),
      h("button", { type: "button", dataset: { mode: "user" }, text: "By username" }));
    const paneUrl = buildUrlPane(player);
    const paneUser = buildUserPane(player);
    paneUser.wrap.hidden = true;
    seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      seg.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const url = b.dataset.mode === "url";
      paneUrl.wrap.hidden = !url; paneUser.wrap.hidden = url;
    }));
    const body = h("div", {}, seg, paneUrl.wrap, paneUser.wrap);
    openModal(`Import deck for ${player.name}`, body, [btn("Done", "gv-btn-ghost", async () => { closeModal(); await reload(); })]);
  }

  function buildUrlPane(player) {
    const input = h("input", { class: "gv-input", type: "url", placeholder: "https://archidekt.com/decks/… · moxfield.com/decks/… · mtggoldfish.com/deck/…" });
    const go = btn("Import", "gv-btn-primary", async () => {
      const url = input.value.trim();
      if (!url) { toast("Paste a deck link.", "err"); return; }
      go.disabled = true; go.textContent = "Importing…";
      try {
        const { deck } = await api("POST", `/players/${player.id}/decks`, { url });
        toast(`Imported “${deck.name}”.`, "ok");
        input.value = ""; await reload(); patchOpenImport(player);
      } catch (e) { toast(e.message, "err"); }
      go.disabled = false; go.textContent = "Import";
    }, { icon: "bi-cloud-download" });
    const wrap = h("div", { style: "margin-top:1rem" },
      field("Deck link", input, "Works with Archidekt and MTGGoldfish. Moxfield blocks automated access, so its links can’t be imported. The deck must be public."),
      h("div", { style: "margin-top:.75rem" }, go));
    return { wrap };
  }

  function buildUserPane(player) {
    // Only Archidekt supports server-side username listing; Moxfield is behind
    // Cloudflare bot protection and can't be queried.
    const source = h("select", { class: "gv-select" },
      h("option", { value: "archidekt", text: "Archidekt" }));
    const username = h("input", { class: "gv-input", type: "text", placeholder: "their username" });
    const results = h("div", { class: "gv-picklist", style: "margin-top:.75rem" });
    const search = btn("Find decks", "gv-btn-primary", async () => {
      const u = username.value.trim();
      if (!u) { toast("Enter a username.", "err"); return; }
      clear(results); results.append(h("div", { class: "gv-loading" }, h("span", { class: "gv-spin" }), "Searching…"));
      try {
        const { decks } = await api("GET", `/source-decks?source=${encodeURIComponent(source.value)}&username=${encodeURIComponent(u)}`);
        clear(results);
        if (!decks.length) { results.append(h("div", { class: "gv-empty", text: "No decks found." })); return; }
        decks.forEach((d) => results.append(pickRow(player, d)));
      } catch (e) { clear(results); results.append(h("div", { class: "gv-empty", text: e.message })); }
    }, { icon: "bi-search" });
    const row = h("div", { class: "gv-row" }, field("Site", source), field("Username", username));
    const wrap = h("div", { style: "margin-top:1rem" }, row,
      h("div", { class: "gv-hint", style: "margin-top:.4rem" }, "Only Archidekt supports username lookup. Moxfield blocks automated access (Cloudflare)."),
      h("div", { style: "margin-top:.5rem" }, search), results);
    return { wrap };
  }

  function pickRow(player, d) {
    const meta = h("div", { class: "gv-deck-sub" });
    if (d.colors && d.colors.length) meta.append(pips(d.colors));
    if (d.bracket) meta.append(h("span", { class: "gv-badge gv-bracket", text: `B${d.bracket}` }));
    if (d.card_count) meta.append(h("span", { text: `${d.card_count} cards` }));
    const add = h("button", { class: "gv-btn gv-btn-primary gv-btn-sm gv-btn-icon", title: "Add this deck" }, h("i", { class: "bi bi-plus-lg" }));
    const row = h("div", { class: "gv-pick" },
      h("div", { style: "flex:1;min-width:0" }, h("div", { class: "gv-pick-name", text: d.name }), meta), add);
    add.addEventListener("click", async () => {
      add.disabled = true; add.innerHTML = '<span class="gv-spin"></span>';
      try {
        await api("POST", `/players/${player.id}/decks`, { source: d.source, source_id: d.source_id });
        toast(`Added “${d.name}”.`, "ok");
        row.style.opacity = "0.5"; add.innerHTML = '<i class="bi bi-check-lg"></i>';
        await reload();
      } catch (e) { toast(e.message, "err"); add.disabled = false; add.innerHTML = '<i class="bi bi-plus-lg"></i>'; }
    });
    return row;
  }

  // Refresh the player object referenced by an open import modal (best-effort).
  function patchOpenImport(player) {
    const fresh = state.players.find((p) => p.id === player.id);
    if (fresh) Object.assign(player, fresh);
  }

  async function syncDeck(deck, button) {
    if (button) { button.disabled = true; button.innerHTML = '<span class="gv-spin"></span>'; }
    try {
      const { deck: updated } = await api("POST", `/decks/${deck.id}/sync`);
      toast(`Synced “${updated.name}”.`, "ok");
      await reload();
    } catch (e) { toast(e.message, "err"); if (button) { button.disabled = false; button.innerHTML = '<i class="bi bi-arrow-repeat"></i>'; } }
  }

  /* ---------------------------------------------------- log game modal */
  function openLogGame(existing) {
    const isEdit = !!existing;
    if (!isEdit && state.players.length < 2) {
      toast("Add at least two players first.", "err");
      switchTab("players");
      return;
    }

    // Ordered seat state — index = turn order (0 = plays first). Selections are
    // held here so reorder / add / remove never lose data. snapDeck preserves a
    // logged deck name that isn't (yet) mapped to one of the player's decks.
    const seats = [];
    if (isEdit && (existing.participants || []).length) {
      existing.participants.slice()
        .sort((a, b) => (a.turn_order || 0) - (b.turn_order || 0))
        .forEach((p) => {
          let playerId = p.player_id ? String(p.player_id) : "";
          if (!playerId && p.player_name) {
            const m = state.players.find((x) => x.name.toLowerCase() === p.player_name.toLowerCase());
            if (m) playerId = String(m.id);
          }
          seats.push({
            playerId,
            deckId: p.deck_id ? String(p.deck_id) : "",
            winner: !!p.is_winner,
            snapDeck: (!p.deck_id && p.deck_name) ? { name: p.deck_name, commander: p.commander_name || "" } : null,
          });
        });
    } else {
      const startN = Math.min(4, Math.max(2, state.players.length));
      for (let i = 0; i < startN; i++) {
        const p = state.players[i];
        seats.push({ playerId: p ? String(p.id) : "", deckId: "", winner: false, snapDeck: null });
      }
    }

    const seatsWrap = h("div", { class: "gv-log-seats" });

    function fillDecks(deckSel, playerId, selected, placeholder) {
      clear(deckSel);
      deckSel.append(h("option", { value: "", text: placeholder || "— deck (optional) —" }));
      const p = state.players.find((x) => String(x.id) === String(playerId));
      (p && p.decks || []).forEach((d) => deckSel.append(h("option", { value: String(d.id), text: d.name })));
      deckSel.value = selected || "";
    }

    function render() {
      clear(seatsWrap);
      seats.forEach((seat, i) => seatsWrap.append(seatRow(seat, i)));
    }

    function seatRow(seat, i) {
      const isFirst = i === 0;
      const isLast = i === seats.length - 1;
      const badge = h("span", {
        class: `gv-seat-badge ${isFirst ? "first" : ""} ${isLast ? "last" : ""}`.trim(),
        title: isFirst ? "Plays first" : isLast ? "Plays last" : `Turn ${i + 1}`,
      }, ordinal(i + 1));

      const playerSel = h("select", { class: "gv-select" }, h("option", { value: "", text: "— player —" }));
      state.players.forEach((p) => playerSel.append(h("option", { value: String(p.id), text: p.name })));
      playerSel.value = seat.playerId || "";

      const deckSel = h("select", { class: "gv-select", title: seat.snapDeck ? `Logged as “${seat.snapDeck.name}” — pick a deck to map it` : "" });
      fillDecks(deckSel, seat.playerId, seat.deckId, seat.snapDeck ? `keep logged: ${seat.snapDeck.name}` : undefined);

      playerSel.addEventListener("change", () => {
        seat.playerId = playerSel.value;
        seat.deckId = "";
        seat.snapDeck = null;
        fillDecks(deckSel, seat.playerId, "");
      });
      deckSel.addEventListener("change", () => { seat.deckId = deckSel.value; });

      const win = h("input", { type: "checkbox" });
      win.checked = !!seat.winner;
      win.addEventListener("change", () => {
        seats.forEach((s) => { s.winner = false; });
        seat.winner = win.checked;
        render(); // reflect single-winner across rows
      });

      const up = h("button", { class: "gv-btn gv-btn-ghost gv-btn-sm gv-btn-icon", type: "button", title: "Move earlier",
        disabled: isFirst, onclick: () => { if (i > 0) { [seats[i - 1], seats[i]] = [seats[i], seats[i - 1]]; render(); } } }, h("i", { class: "bi bi-arrow-up" }));
      const down = h("button", { class: "gv-btn gv-btn-ghost gv-btn-sm gv-btn-icon", type: "button", title: "Move later",
        disabled: isLast, onclick: () => { if (i < seats.length - 1) { [seats[i + 1], seats[i]] = [seats[i], seats[i + 1]]; render(); } } }, h("i", { class: "bi bi-arrow-down" }));
      const remove = h("button", { class: "gv-btn gv-btn-danger gv-btn-sm gv-btn-icon", type: "button", title: "Remove seat",
        disabled: seats.length <= 2, onclick: () => { if (seats.length > 2) { seats.splice(i, 1); render(); } } }, h("i", { class: "bi bi-x-lg" }));

      return h("div", { class: "gv-log-seat" },
        badge, playerSel, deckSel,
        h("label", { class: "gv-win" }, win, " win"),
        h("div", { class: "gv-seat-ctrls" }, up, down, remove));
    }
    render();

    const addSeat = btn("Add player", "gv-btn-ghost gv-btn-sm", () => {
      if (seats.length < 8) { seats.push({ playerId: "", deckId: "", winner: false }); render(); }
    }, { icon: "bi-plus-lg" });

    const playedAt = h("input", { class: "gv-input", type: "date",
      value: (isEdit && existing.played_at_label) ? existing.played_at_label : new Date().toISOString().slice(0, 10) });
    const turns = h("input", { class: "gv-input", type: "number", min: "0", max: "100", placeholder: "—",
      value: (isEdit && existing.turns) ? String(existing.turns) : "" });
    const winCond = h("select", { class: "gv-select" }, h("option", { value: "", text: "— how it ended —" }));
    WIN_CONDITIONS.forEach((w) => winCond.append(h("option", { value: w, text: WIN_LABELS[w] || w })));
    if (isEdit && existing.win_condition) winCond.value = existing.win_condition;
    const infinite = h("input", { type: "checkbox" });
    if (isEdit && existing.infinite_win) infinite.checked = true;
    const infiniteRow = h("label", { class: "gv-win", style: "margin-top:.3rem" }, infinite,
      h("span", {}, " ", h("i", { class: "bi bi-infinity" }), " Infinite win ", h("span", { class: "gv-hint", style: "display:inline" }, "(won with an infinite combo)")));
    const notes = h("textarea", { class: "gv-textarea", maxlength: "2000", placeholder: "Optional notes…" },
      (isEdit && existing.notes) ? existing.notes : null);

    const body = h("div", {},
      h("div", { class: "gv-row" }, field("Date", playedAt), field("Turns (optional)", turns)),
      field("Win condition", winCond),
      infiniteRow,
      h("div", { class: "gv-field" },
        h("label", {}, "Seats — top plays first, bottom plays last"),
        seatsWrap,
        h("div", { style: "margin-top:.5rem" }, addSeat)),
      field("Notes", notes));

    const save = btn("Save game", "gv-btn-primary", async () => {
      if (seats.some((s) => !s.playerId)) {
        toast("Give every seat a player, or remove the empty one.", "err");
        return;
      }
      const ids = seats.map((s) => s.playerId);
      if (new Set(ids).size !== ids.length) {
        toast("Each player can only take one seat.", "err");
        return;
      }
      const participants = seats.map((s, idx) => {
        const seatData = {
          player_id: Number(s.playerId),
          is_winner: !!s.winner,
          turn_order: idx + 1, // explicit: 1 = first to play … N = last
        };
        if (s.deckId) {
          seatData.deck_id = Number(s.deckId);
        } else {
          seatData.deck_id = null;
          if (s.snapDeck) { // preserve a logged-but-unmapped deck
            seatData.deck_name = s.snapDeck.name;
            seatData.commander_name = s.snapDeck.commander;
          }
        }
        return seatData;
      });
      const payload = {
        played_at: playedAt.value,
        turns: turns.value || null,
        win_condition: winCond.value || null,
        infinite_win: infinite.checked,
        notes: notes.value.trim() || null,
        participants,
      };
      save.disabled = true;
      try {
        if (isEdit) await api("PATCH", `/games/${existing.id}`, payload);
        else await api("POST", "/games", payload);
        toast(isEdit ? "Game updated." : "Game logged.", "ok");
        closeModal(); await reload();
      } catch (e) { toast(e.message, "err"); save.disabled = false; }
    }, { icon: "bi-check-lg" });

    openModal(isEdit ? "Edit game" : "Log a game", body,
      [btn("Cancel", "gv-btn-ghost", closeModal), save]);
  }

  /* ------------------------------------------------- deck mapping modal */
  async function openDeckMap() {
    const body = h("div", {}, h("div", { class: "gv-loading" }, h("span", { class: "gv-spin" }), "Loading…"));
    openModal("Map game decks", body, [btn("Close", "gv-btn-ghost", closeModal)]);
    let data;
    try { data = await api("GET", "/deck-map"); }
    catch (e) { clear(body); body.append(h("div", { class: "gv-empty", text: e.message })); return; }
    clear(body);
    if (!data.players || !data.players.length) {
      body.append(h("div", { class: "gv-blank" },
        h("i", { class: "bi bi-diagram-3" }),
        h("div", { text: "No game decks to map. Log or import some games first." })));
      return;
    }
    body.append(h("div", { class: "gv-hint", style: "margin-bottom:.6rem" },
      "Point each commander from your game history at one of that player’s current decks. ✓ = same commander (a confident match, pre-selected). Leave the rest as-is — unmapped games keep their commander."));

    const rows = [];
    data.players.forEach((pl) => {
      body.append(h("div", { style: "font-weight:700; margin:.8rem 0 .35rem; display:flex; align-items:center; gap:.4rem" },
        h("div", { class: "gv-avatar", style: "width:26px;height:26px;font-size:.75rem;border-radius:8px", text: initials(pl.name) }), pl.name));
      if (!pl.decks.length) {
        body.append(h("div", { class: "gv-hint", style: "margin-bottom:.3rem" },
          "No decks imported for this player yet — import their decks first, then map."));
      }
      (pl.game_commanders || []).forEach((gc) => {
        const sel = h("select", { class: "gv-select" }, h("option", { value: "", text: "— leave as-is —" }));
        pl.decks.forEach((d) => sel.append(h("option", { value: String(d.id),
          text: d.commander_name && d.commander_name !== d.name ? `${d.name} · ${d.commander_name}` : d.name })));
        const original = gc.mapped_deck_id ? String(gc.mapped_deck_id) : "";
        // Pre-select an already-mapped deck, else the confident commander match.
        sel.value = original || (gc.suggested_deck_id ? String(gc.suggested_deck_id) : "");
        if (!pl.decks.length) sel.disabled = true;
        rows.push({ playerId: pl.id, commanderName: gc.commander_name, select: sel, original });
        const nameCell = h("div", { class: "gv-map-name", title: gc.commander_name }, gc.commander_name,
          h("span", { class: "gv-map-count", text: ` ${gc.count}×` }));
        if (gc.suggested_deck_id && !gc.mapped_deck_id) nameCell.append(h("span", { class: "gv-map-match", title: "Same commander as a current deck" }, " ✓"));
        body.append(h("div", { class: "gv-map-row" }, nameCell, sel));
      });
    });

    const save = btn("Save mappings", "gv-btn-primary", async () => {
      const mappings = rows
        .filter((r) => r.select.value !== r.original)
        .map((r) => ({ player_id: r.playerId, commander_name: r.commanderName,
          deck_id: r.select.value ? Number(r.select.value) : null }));
      if (!mappings.length) { toast("No mapping changes to save.", "info"); return; }
      save.disabled = true;
      try {
        const { result } = await api("POST", "/deck-map", { mappings });
        toast(`Mapped ${result.decks_mapped} deck name(s) across ${result.seats_updated} seats.`, "ok");
        closeModal(); await reload();
      } catch (e) { toast(e.message, "err"); save.disabled = false; }
    }, { icon: "bi-check-lg" });
    const foot = $("#gvModalFoot"); clear(foot);
    foot.append(btn("Cancel", "gv-btn-ghost", closeModal), save);
  }

  /* --------------------------------------------------------- delete flow */
  function confirmDelete(kind, obj) {
    const labels = {
      player: [`Delete ${obj.name}?`, "This also removes their imported decks. Games already logged keep their history.", `/players/${obj.id}`],
      deck: [`Remove “${obj.name}”?`, "The deck will be removed from this player.", `/decks/${obj.id}`],
      game: [`Delete this game?`, "This removes the logged game permanently.", `/games/${obj.id}`],
    }[kind];
    const body = h("div", {}, h("p", { text: labels[1], style: "margin:0;color:var(--gv-muted)" }));
    const del = btn("Delete", "gv-btn-danger", async () => {
      del.disabled = true;
      try { await api("DELETE", labels[2]); toast("Deleted.", "ok"); closeModal(); await reload(); }
      catch (e) { toast(e.message, "err"); del.disabled = false; }
    }, { icon: "bi-trash" });
    openModal(labels[0], body, [btn("Cancel", "gv-btn-ghost", closeModal), del]);
  }

  /* ----------------------------------------------------------- rendering */
  function renderAll() { renderStats(); renderBoards(); renderGames(); renderPlayers(); }

  async function reload() {
    try {
      const data = await api("GET", "/state");
      state.players = data.players || [];
      state.games = data.games || [];
      state.stats = data.stats || {};
      renderAll();
    } catch (e) { toast(e.message, "err"); }
  }

  /* --------------------------------------------------------------- tabs */
  function switchTab(name) {
    document.querySelectorAll(".gv-tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    document.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== name; });
  }

  /* --------------------------------------------------------------- utils */
  function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }
  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  /* ---------------------------------------------------------------- init */
  function init() {
    document.querySelectorAll(".gv-tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
    document.querySelectorAll("[data-action]").forEach((el) => el.addEventListener("click", () => {
      const a = el.dataset.action;
      if (a === "add-player") openPlayerModal();
      else if (a === "log-game") openLogGame();
      else if (a === "map-decks") openDeckMap();
    }));
    overlay().addEventListener("click", (e) => { if (e.target === overlay() || e.target.hasAttribute("data-close")) closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && overlay().classList.contains("open")) closeModal(); });
    reload();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
