/*
 * archidekt-deck-picker.js
 *
 * On the advanced game-log form, each seat's deck field gets a "Pull from
 * Archidekt" button. It pulls that player's Commander decks live (pre-filling
 * the username saved on the pod player), lets you pick one, imports it into a
 * local deck, and points the seat at the imported folder — so the logged game
 * gets the deck's commander, cards, and bracket like any other deck.
 */
(function () {
  "use strict";

  function deckSelect(seat) {
    return document.querySelector('.seat-deck-select[data-seat="' + seat + '"]');
  }

  function usernameForSeat(seat) {
    var roster = document.querySelector('input[name="seat_' + seat + '_roster_id"]');
    var id = roster && roster.value ? String(roster.value) : "";
    var map = window.__rosterArchidekt || {};
    return (id && map[id]) || "";
  }

  function notify(message, kind) {
    if (typeof window.showToast === "function") {
      window.showToast(message, kind || "info");
    } else if (kind === "danger") {
      window.alert(message);
    }
  }

  function csrfHeaders(extra) {
    var headers = Object.assign({}, extra || {});
    if (window.csrfHeader) Object.assign(headers, window.csrfHeader);
    return headers;
  }

  function pointSeatAtFolder(seat, folderId, label) {
    var container = deckSelect(seat);
    if (!container) return;
    var hidden = container.querySelector('input[name="seat_' + seat + '_deck_ref"]');
    var labelEl = container.querySelector("[data-game-select-label]");
    if (hidden) {
      hidden.value = "folder:" + folderId;
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (labelEl) labelEl.textContent = label;
    // Keep the dv-select menu consistent: add + activate a matching option.
    var menu = container.querySelector(".dv-select-menu");
    if (menu) {
      menu.querySelectorAll("[data-game-select-option]").forEach(function (opt) {
        opt.classList.remove("active");
      });
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "dropdown-item active";
      btn.setAttribute("data-game-select-option", "");
      btn.setAttribute("data-value", "folder:" + folderId);
      btn.setAttribute("data-label", label);
      btn.textContent = label;
      li.appendChild(btn);
      menu.appendChild(li);
    }
  }

  function importDeck(seat, menuEl, deck) {
    if (menuEl) menuEl.textContent = "Importing " + deck.name + "…";
    fetch("/api/games/archidekt/import", {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      credentials: "same-origin",
      body: JSON.stringify({ deck_id: deck.id }),
    })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (data) {
        if (menuEl) { menuEl.hidden = true; menuEl.textContent = ""; }
        if (data && data.success) {
          var label = data.name + (data.bracket != null ? " (Bracket " + data.bracket + ")" : "");
          pointSeatAtFolder(seat, data.folder_id, label);
          notify("Imported " + data.name + (data.refreshed ? " (refreshed)" : "") + ".", "success");
        } else {
          notify((data && data.message) || "Couldn't import that deck.", "danger");
        }
      })
      .catch(function () {
        if (menuEl) { menuEl.hidden = true; menuEl.textContent = ""; }
        notify("Couldn't import that deck.", "danger");
      });
  }

  function renderDecks(seat, menuEl, decks, username) {
    if (!menuEl) return;
    menuEl.textContent = "";
    if (!decks.length) {
      var empty = document.createElement("div");
      empty.className = "text-muted small p-2";
      empty.textContent = 'No Commander decks found for "' + username + '".';
      menuEl.appendChild(empty);
      menuEl.hidden = false;
      return;
    }
    var list = document.createElement("div");
    list.className = "list-group shadow-sm";
    list.style.maxHeight = "16rem";
    list.style.overflowY = "auto";
    decks.forEach(function (deck) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center py-1";
      var name = document.createElement("span");
      name.className = "text-truncate";
      name.textContent = deck.name;
      item.appendChild(name);
      if (deck.bracket != null) {
        var badge = document.createElement("span");
        badge.className = "badge text-bg-secondary ms-2 flex-shrink-0";
        badge.textContent = "B" + deck.bracket;
        item.appendChild(badge);
      }
      item.addEventListener("click", function () { importDeck(seat, menuEl, deck); });
      list.appendChild(item);
    });
    menuEl.appendChild(list);
    menuEl.hidden = false;
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest(".archidekt-pull-btn");
    if (!btn) return;
    event.preventDefault();
    var seat = btn.getAttribute("data-archidekt-seat");
    var menuEl = document.querySelector('[data-archidekt-menu="' + seat + '"]');

    var username = window.prompt("Archidekt username (or profile URL):", usernameForSeat(seat) || "");
    if (username === null) return;
    username = username.trim();
    if (!username) return;

    if (menuEl) { menuEl.hidden = false; menuEl.textContent = "Loading decks…"; }
    fetch("/api/games/archidekt/decks?username=" + encodeURIComponent(username), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (data) {
        if (data && data.success) {
          renderDecks(seat, menuEl, data.decks || [], data.username || username);
        } else {
          if (menuEl) menuEl.hidden = true;
          notify((data && data.message) || "Couldn't load decks.", "danger");
        }
      })
      .catch(function () {
        if (menuEl) menuEl.hidden = true;
        notify("Couldn't load decks.", "danger");
      });
  });
})();
