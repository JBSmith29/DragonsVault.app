(function () {
  const METRICS_FORM_SELECTOR = "form[data-game-metrics-form]";

  const initForm = (form) => {
    if (!form || form.dataset.gameMetricsReady === "true") {
      return;
    }
    form.dataset.gameMetricsReady = "true";

    const rangeWrapper = form.querySelector('[data-game-select="range"]');
    const podWrapper = form.querySelector('[data-game-select="pod"]');
    const yearWrapper = form.querySelector('[data-game-select="year"]');
    const playerWrapper = form.querySelector('[data-game-select="player"]');
    const deckWrapper = form.querySelector('[data-game-select="deck"]');
    const yearField = form.querySelector("#yearField");
    const customField = form.querySelector("#customField");

    const submitForm = () => {
      const formData = new FormData(form);
      const params = new URLSearchParams();
      for (const [key, value] of formData.entries()) {
        if (value && value.toString().trim()) {
          params.set(key, value.toString().trim());
        }
      }
      const action = form.getAttribute("action") || window.location.pathname;
      const targetUrl = params.toString()
        ? `${action}?${params.toString()}`
        : action;
      window.location.href = targetUrl;
    };

    const setRangeValue = (value) => {
      if (!rangeWrapper) {
        return;
      }
      const input = rangeWrapper.querySelector("[data-game-select-input]");
      const labelEl = rangeWrapper.querySelector("[data-game-select-label]");
      const options = Array.from(
        rangeWrapper.querySelectorAll("[data-game-select-option]"),
      );
      if (!input || !labelEl) {
        return;
      }
      const match = options.find(
        (opt) => (opt.dataset.value || "") === value,
      );
      options.forEach((btn) => btn.classList.toggle("active", btn === match));
      if (match) {
        labelEl.textContent = match.dataset.label || match.textContent.trim();
      }
      input.value = value;
    };

    const bindSelect = (wrapper, onChange) => {
      if (!wrapper || wrapper.dataset.gameSelectReady === "true") {
        return;
      }
      const input = wrapper.querySelector("[data-game-select-input]");
      const labelEl = wrapper.querySelector("[data-game-select-label]");
      const options = Array.from(
        wrapper.querySelectorAll("[data-game-select-option]"),
      );
      if (!input || !labelEl || !options.length) {
        return;
      }

      const setActive = (item, fromUser = false) => {
        const value = item.dataset.value || "";
        const label = item.dataset.label || item.textContent.trim();
        options.forEach((btn) => btn.classList.toggle("active", btn === item));
        labelEl.textContent = label;
        input.value = value;
        if (onChange) {
          onChange(value, fromUser);
        }
      };

      options.forEach((btn) => {
        btn.addEventListener("click", () => setActive(btn, true));
        if ((btn.dataset.value || "") === input.value) {
          setActive(btn, false);
        }
      });

      if (onChange) {
        onChange(input.value || "", false);
      }
      wrapper.dataset.gameSelectReady = "true";
    };

    const bindSearch = (wrapper, optionSelector) => {
      if (!wrapper || wrapper.dataset.gameSelectSearchReady === "true") {
        return;
      }
      const searchInput = wrapper.querySelector("[data-dv-select-search]");
      if (!searchInput) {
        return;
      }
      const menu = wrapper.querySelector(".dv-select-menu");
      if (!menu) {
        return;
      }
      const updateFilter = () => {
        const q = searchInput.value.trim().toLowerCase();
        const targets = Array.from(menu.querySelectorAll(optionSelector));
        targets.forEach((btn) => {
          const label = (btn.dataset.label || btn.textContent || "").toLowerCase();
          const match = !q || label.includes(q);
          const li = btn.closest("li");
          if (li) {
            li.style.display = match ? "" : "none";
          } else {
            btn.style.display = match ? "" : "none";
          }
        });
      };
      searchInput.addEventListener("input", updateFilter);
      updateFilter();
      wrapper.addEventListener("shown.bs.dropdown", () => {
        searchInput.value = "";
        searchInput.dispatchEvent(new Event("input"));
        setTimeout(() => searchInput.focus(), 10);
      });
      wrapper.dataset.gameSelectSearchReady = "true";
    };

    const updateVisibility = (value, fromUser) => {
      if (!yearField || !customField) {
        return;
      }
      yearField.style.display = value === "year" ? "" : "none";
      customField.style.display = value === "custom" ? "" : "none";
      if (!fromUser) {
        return;
      }
      if (value === "custom") {
        return;
      }
      if (value === "year") {
        const yearInput = yearField.querySelector("[data-game-select-input]");
        if (yearInput && yearInput.value) {
          submitForm();
        }
        return;
      }
      submitForm();
    };

    bindSelect(podWrapper, (_value, fromUser) => {
      if (!fromUser) {
        return;
      }
      submitForm();
    });
    bindSelect(rangeWrapper, updateVisibility);
    bindSelect(yearWrapper, (_value, fromUser) => {
      if (!fromUser) {
        return;
      }
      if (rangeWrapper) {
        setRangeValue("year");
        updateVisibility("year", false);
      }
      submitForm();
    });
    bindSelect(playerWrapper);
    bindSelect(deckWrapper);
    bindSearch(playerWrapper, "[data-game-select-option]");
    bindSearch(deckWrapper, "[data-game-select-option]");
  };

  const scan = (scope) => {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll(METRICS_FORM_SELECTOR).forEach(initForm);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => scan(document));
  } else {
    scan(document);
  }

  document.addEventListener("htmx:afterSwap", (event) => {
    scan(event.target || document);
  });
})();
