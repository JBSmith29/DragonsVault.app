/*
 * card-autocomplete.js
 *
 * EDHREC-style "as you type" card-name suggestions. Attaches to any
 *   <input data-card-autocomplete>
 * anywhere in the app, debounces keystrokes, queries /api/cards/autocomplete,
 * and shows a keyboard-navigable dropdown of matching card names.
 *
 * Selecting a suggestion fills the input and, if it lives in a form, submits it
 * (pick-and-go) — unless the input is marked data-card-autocomplete="nosubmit",
 * in which case it just fills the value and fires input/change events so any
 * existing live-filter logic reacts.
 */
(function () {
  "use strict";

  var ENDPOINT = "/api/cards/autocomplete";
  var MIN_LEN = 2;
  var LIMIT = 10;
  var DEBOUNCE_MS = 140;

  function attach(input) {
    if (!input || input.__cardAcBound) return;
    input.__cardAcBound = true;
    input.setAttribute("autocomplete", "off");

    var menu = null;
    var items = [];
    var active = -1;
    var lastQuery = "";
    var timer = null;
    var seq = 0;

    function ensureMenu() {
      if (menu) return menu;
      menu = document.createElement("div");
      menu.className = "card-ac-menu";
      menu.setAttribute("role", "listbox");
      document.body.appendChild(menu);
      return menu;
    }

    function closeMenu() {
      if (menu) {
        menu.remove();
        menu = null;
      }
      items = [];
      active = -1;
    }

    function position() {
      if (!menu) return;
      var r = input.getBoundingClientRect();
      menu.style.left = r.left + window.scrollX + "px";
      menu.style.top = r.bottom + window.scrollY + 2 + "px";
      menu.style.width = r.width + "px";
    }

    function setActive(index) {
      active = index;
      if (!menu) return;
      var children = menu.children;
      for (var i = 0; i < children.length; i++) {
        if (i === index) {
          children[i].classList.add("active");
          children[i].scrollIntoView({ block: "nearest" });
        } else {
          children[i].classList.remove("active");
        }
      }
    }

    function choose(index) {
      var name = items[index];
      if (name == null) return;
      input.value = name;
      closeMenu();
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      var form = input.form;
      if (form && input.dataset.cardAutocomplete !== "nosubmit") {
        if (typeof form.requestSubmit === "function") form.requestSubmit();
        else form.submit();
      }
    }

    function render(list) {
      items = Array.isArray(list) ? list : [];
      active = -1;
      if (!items.length) {
        closeMenu();
        return;
      }
      var box = ensureMenu();
      box.textContent = "";
      items.forEach(function (name, i) {
        var el = document.createElement("div");
        el.className = "card-ac-item";
        el.setAttribute("role", "option");
        el.textContent = name;
        el.addEventListener("mousedown", function (event) {
          event.preventDefault(); // keep focus; fire before blur
          choose(i);
        });
        el.addEventListener("mouseenter", function () {
          setActive(i);
        });
        box.appendChild(el);
      });
      position();
    }

    function runQuery(query) {
      var mine = ++seq;
      fetch(ENDPOINT + "?q=" + encodeURIComponent(query) + "&limit=" + LIMIT, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(function (resp) {
          return resp.ok ? resp.json() : null;
        })
        .then(function (data) {
          if (mine !== seq) return; // a newer keystroke superseded this
          render(data && data.ok ? data.suggestions : []);
        })
        .catch(function () {
          /* network hiccup — leave the field usable, no dropdown */
        });
    }

    input.addEventListener("input", function () {
      var query = input.value.trim();
      if (query === lastQuery) return;
      lastQuery = query;
      if (timer) clearTimeout(timer);
      if (query.length < MIN_LEN) {
        closeMenu();
        return;
      }
      timer = setTimeout(function () {
        runQuery(query);
      }, DEBOUNCE_MS);
    });

    input.addEventListener("keydown", function (event) {
      if (!menu || !items.length) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((active + 1) % items.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((active - 1 + items.length) % items.length);
      } else if (event.key === "Enter") {
        if (active >= 0) {
          event.preventDefault();
          choose(active);
        } else {
          closeMenu();
        }
      } else if (event.key === "Escape") {
        closeMenu();
      }
    });

    input.addEventListener("blur", function () {
      // Delay so a click on a suggestion (mousedown) registers first.
      setTimeout(closeMenu, 120);
    });

    window.addEventListener("scroll", function () { if (menu) position(); }, true);
    window.addEventListener("resize", function () { if (menu) position(); });
  }

  function init() {
    var inputs = document.querySelectorAll("input[data-card-autocomplete]");
    for (var i = 0; i < inputs.length; i++) attach(inputs[i]);
  }

  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
})();
