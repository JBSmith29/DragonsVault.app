(() => {
  const RIPPLE_SELECTOR = "[data-ripple]";
  const CONFIRM_SELECTOR = "[data-confirm]";

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

  function applySubmitFeedback(form, submitter) {
    if (!form) return;
    if (!form.hasAttribute("data-ux-submit") && !form.hasAttribute("data-job-trigger")) {
      if (!submitter || !submitter.hasAttribute("data-ux-submit")) return;
    }
    const btn = submitter || form.querySelector('button[type="submit"], input[type="submit"]');
    if (!btn || btn.disabled) return;
    if (btn.dataset.uxFeedbackApplied === "1") return;
    const label = btn.dataset.progressLabel || "Working...";
    btn.dataset.uxFeedbackApplied = "1";
    if (!btn.dataset.uxOriginalLabel) {
      btn.dataset.uxOriginalLabel = btn.tagName === "INPUT" ? btn.value : btn.innerHTML;
    }
    btn.disabled = true;
    if (btn.tagName === "INPUT") {
      btn.value = label;
    } else {
      btn.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${label}`;
    }
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

  document.addEventListener("submit", (evt) => {
    const form = evt.target;
    if (!(form instanceof HTMLFormElement)) return;
    const submitter = evt.submitter instanceof HTMLElement ? evt.submitter : null;
    let confirmMsg = (submitter && submitter.getAttribute("data-confirm"))
      || form.getAttribute("data-confirm");
    if (submitter && submitter.dataset.confirmed === "1") {
      submitter.dataset.confirmed = "";
      confirmMsg = null;
    }
    if (confirmMsg) {
      const ok = window.confirm(confirmMsg);
      if (!ok) {
        evt.preventDefault();
        return;
      }
    }
    applySubmitFeedback(form, submitter);
  });

  document.addEventListener("click", (evt) => {
    const target = evt.target instanceof Element ? evt.target.closest(CONFIRM_SELECTOR) : null;
    if (!target) return;
    const confirmMsg = target.getAttribute("data-confirm");
    if (!confirmMsg) return;
    if (target.closest("form")) {
      const ok = window.confirm(confirmMsg);
      if (!ok) {
        evt.preventDefault();
        evt.stopImmediatePropagation();
        return;
      }
      target.dataset.confirmed = "1";
      return;
    }
    const ok = window.confirm(confirmMsg);
    if (!ok) {
      evt.preventDefault();
      evt.stopImmediatePropagation();
    }
  });
})();
