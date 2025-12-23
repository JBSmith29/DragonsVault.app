(function () {
  const READY_EVENT = "dv-select:ready";
  const CHANGE_EVENT = "dv-select:change";

  function dispatch(wrapper, type, detail) {
    wrapper.dispatchEvent(
      new CustomEvent(type, {
        detail,
        bubbles: false,
      }),
    );
  }

  function setActive(wrapper, item, { trigger } = { trigger: false }) {
    if (!item) {
      return;
    }
    const labelEl = wrapper.querySelector("[data-dv-select-label]");
    const hiddenInput = wrapper.querySelector("[data-dv-select-input]");
    const items = Array.from(wrapper.querySelectorAll("[data-dv-select-option]"));
    const value = item.dataset.value ?? "";
    const label = item.dataset.label ?? item.textContent.trim();

    items.forEach((btn) => {
      btn.classList.toggle("active", btn === item);
    });

    if (labelEl) {
      labelEl.textContent = label;
    }

    if (hiddenInput && hiddenInput.value !== value) {
      hiddenInput.value = value;
      const inputEvent = new Event("input", { bubbles: true });
      hiddenInput.dispatchEvent(inputEvent);
      const changeEvent = new Event("change", { bubbles: true });
      hiddenInput.dispatchEvent(changeEvent);
    }

    wrapper.dataset.dvSelectValue = value;

    if (trigger) {
      dispatch(wrapper, CHANGE_EVENT, { value, label });
      if (wrapper.dataset.autoSubmit === "true") {
        const form = hiddenInput?.form ?? wrapper.closest("form");
        if (form) {
          form.submit();
        }
      }
    }
  }

  function initialise(wrapper) {
    if (wrapper.dataset.dvSelectReady === "true") {
      return;
    }

    const items = Array.from(wrapper.querySelectorAll("[data-dv-select-option]"));
    if (!items.length) {
      return;
    }

    const hiddenInput = wrapper.querySelector("[data-dv-select-input]");
    const currentValue = hiddenInput?.value ?? wrapper.dataset.dvSelectValue ?? "";
    let activeItem = items.find((item) => (item.dataset.value ?? "") === currentValue);

    if (!activeItem) {
      activeItem = items.find((item) => item.classList.contains("active")) || items[0];
      if (hiddenInput) {
        hiddenInput.value = activeItem?.dataset.value ?? "";
      }
    }

    if (activeItem) {
      setActive(wrapper, activeItem, { trigger: false });
    }

    items.forEach((item) => {
      item.addEventListener("click", (event) => {
        event.preventDefault();
        const isActive = item.classList.contains("active");
        if (isActive && wrapper.dataset.allowReselect !== "true") {
          return;
        }
        setActive(wrapper, item, { trigger: true });
      });
    });

    const searchInput = wrapper.querySelector("[data-dv-select-search]");
    if (searchInput) {
      const searchTargets = items.map((btn) => ({
        btn,
        label: (btn.dataset.label || btn.textContent || "").toLowerCase(),
      }));
      const categoryItems = Array.from(wrapper.querySelectorAll("[data-dv-select-category]"));
      searchInput.addEventListener("input", () => {
        const q = searchInput.value.trim().toLowerCase();
        const showCategories = !q;
        categoryItems.forEach((node) => {
          node.style.display = showCategories ? "" : "none";
        });
        searchTargets.forEach(({ btn, label }) => {
          const match = !q || label.includes(q);
          if (btn.closest("li")) {
            btn.closest("li").style.display = match ? "" : "none";
          } else {
            btn.style.display = match ? "" : "none";
          }
        });
      });
      wrapper.addEventListener("shown.bs.dropdown", () => {
        searchInput.value = "";
        searchInput.dispatchEvent(new Event("input"));
        setTimeout(() => searchInput.focus(), 10);
      });
    }

    wrapper.dataset.dvSelectReady = "true";

    const readyItem = activeItem || items[0];
    if (readyItem) {
      dispatch(wrapper, READY_EVENT, {
        value: readyItem.dataset.value ?? "",
        label: readyItem.dataset.label ?? readyItem.textContent.trim(),
      });
    }
  }

  function scan(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-dv-select]").forEach((wrapper) => initialise(wrapper));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => scan(document));
  } else {
    scan(document);
  }

  document.addEventListener("htmx:afterSwap", (event) => {
    scan(event.target || document);
  });
})();
