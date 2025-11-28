(function () {
  const MOBILE_MAX = 768;
  const body = document.body;
  const overlay = document.getElementById("mobileNavOverlay");
  const toggle = document.getElementById("mobileNavToggle");
  const closeBtn = overlay ? overlay.querySelector("[data-mobile-close]") : null;
  const links = overlay ? Array.from(overlay.querySelectorAll("[data-mobile-nav-link]")) : [];
  const sidebarToggle = document.getElementById("sidebarMobileToggle");

  const isMobile = () => window.innerWidth < MOBILE_MAX;

  const openOverlay = () => {
    if (!overlay || !isMobile()) return;
    overlay.classList.remove("d-none");
    body.classList.add("mobile-nav-open");
    try {
      overlay.querySelector("[data-mobile-nav-link]")?.focus({ preventScroll: true });
    } catch (_) {
      /* noop */
    }
  };

  const closeOverlay = () => {
    if (!overlay) return;
    overlay.classList.add("d-none");
    body.classList.remove("mobile-nav-open");
    try {
      toggle?.focus({ preventScroll: true });
    } catch (_) {
      /* noop */
    }
  };

  const handleToggleClick = (evt) => {
    if (!isMobile()) return;
    evt.preventDefault();
    evt.stopImmediatePropagation();
    if (overlay && overlay.classList.contains("d-none")) {
      openOverlay();
    } else {
      closeOverlay();
    }
  };

  if (toggle) {
    toggle.addEventListener("click", handleToggleClick, { capture: true });
  }

  if (sidebarToggle) {
    sidebarToggle.addEventListener("click", handleToggleClick, { capture: true });
  }

  closeBtn?.addEventListener("click", (evt) => {
    evt.preventDefault();
    closeOverlay();
  });

  links.forEach((link) => {
    link.addEventListener("click", () => closeOverlay());
  });

  document.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape" && !overlay?.classList.contains("d-none")) {
      closeOverlay();
    }
  });

  window.addEventListener("resize", () => {
    if (!isMobile()) {
      closeOverlay();
    }
  });
})();
