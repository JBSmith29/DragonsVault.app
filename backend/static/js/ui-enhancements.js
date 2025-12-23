(() => {
  const RIPPLE_SELECTOR = "[data-ripple]";

  function createRipple(evt) {
    const target = evt.currentTarget;
    if (!target) return;

    const rect = target.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const ripple = document.createElement("span");
    ripple.className = "ripple";
    ripple.style.width = `${size}px`;
    ripple.style.height = `${size}px`;

    let x = evt.clientX - rect.left - size / 2;
    let y = evt.clientY - rect.top - size / 2;
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      x = rect.width / 2 - size / 2;
      y = rect.height / 2 - size / 2;
    }

    ripple.style.left = `${x}px`;
    ripple.style.top = `${y}px`;

    const previous = target.querySelector(".ripple");
    if (previous) previous.remove();

    target.appendChild(ripple);
    ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
  }

  function bindRipple(el) {
    if (!el || el.dataset.rippleBound) return;
    el.addEventListener("click", createRipple);
    el.dataset.rippleBound = "1";
  }

  function init(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll(RIPPLE_SELECTOR).forEach(bindRipple);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => init(document));
  } else {
    init(document);
  }

  document.addEventListener("htmx:afterSwap", (evt) => {
    if (!evt || !evt.target) return;
    init(evt.target);
  });
})();
