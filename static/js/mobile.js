(function () {
  const MOBILE_MAX = 768;
  const body = document.body;
  const overlay = document.getElementById("mobileNavOverlay");
  const toggle = document.getElementById("mobileNavToggle");
  const closeBtn = overlay ? overlay.querySelector("[data-mobile-close]") : null;
  const links = overlay ? Array.from(overlay.querySelectorAll("[data-mobile-nav-link]")) : [];
  const sidebarToggle = document.getElementById("sidebarMobileToggle");
  const DRAWER = overlay; // treat overlay as drawer surface
  const mq = window.matchMedia("(max-width: 768px)");

  const isMobile = () => window.innerWidth < MOBILE_MAX;

  const openOverlay = () => {
    if (!overlay || !isMobile()) return;
    overlay.classList.remove("d-none", "closed");
    overlay.classList.add("open");
    body.classList.add("mobile-nav-open");
    body.style.overflow = "hidden";
    try {
      overlay.querySelector("[data-mobile-nav-link]")?.focus({ preventScroll: true });
    } catch (_) {
      /* noop */
    }
  };

  const closeOverlay = () => {
    if (!overlay) return;
    overlay.classList.add("closed");
    overlay.classList.remove("open");
    setTimeout(() => overlay.classList.add("d-none"), 150);
    body.classList.remove("mobile-nav-open");
    body.style.overflow = "";
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

  // Mobile import wizard: step control + smooth scroll
  const importModal = document.getElementById("mobileImportModal");
  const nextBtn = importModal?.querySelector("[data-mobile-next]");
  const prevBtn = importModal?.querySelector("[data-mobile-prev]");
  let currentStep = 1;

  const showStep = (step) => {
    if (!importModal) return;
    currentStep = Math.min(Math.max(step, 1), 4);
    importModal.querySelectorAll(".mobile-step").forEach((el) => {
      el.classList.toggle("d-none", Number(el.dataset.step) !== currentStep);
    });
    prevBtn?.classList.toggle("disabled", currentStep === 1);
    if (currentStep === 4) {
      nextBtn.textContent = "Done";
    } else {
      nextBtn.textContent = "Next";
    }
    importModal.scrollTo({ top: 0, behavior: "smooth" });
  };

  nextBtn?.addEventListener("click", () => {
    if (currentStep < 4) {
      showStep(currentStep + 1);
    } else {
      const modal = bootstrap.Modal.getInstance(importModal);
      modal?.hide();
    }
  });
  prevBtn?.addEventListener("click", () => {
    showStep(currentStep - 1);
  });

  importModal?.addEventListener("shown.bs.modal", () => {
    showStep(1);
  });

  // Swipe gestures for mobile drawer
  let touchStartX = 0;
  let touchCurrentX = 0;
  let tracking = false;
  const EDGE_ZONE = 20;

  const onTouchStart = (evt) => {
    if (!mq.matches) return;
    const touch = evt.touches[0];
    touchStartX = touch.clientX;
    touchCurrentX = touchStartX;
    const overlayVisible = overlay && !overlay.classList.contains("d-none");
    // Start tracking if overlay is open, or touch starts on edge
    if (overlayVisible || touchStartX <= EDGE_ZONE) {
      tracking = true;
    }
  };

  const onTouchMove = (evt) => {
    if (!tracking || !mq.matches) return;
    touchCurrentX = evt.touches[0].clientX;
  };

  const onTouchEnd = () => {
    if (!tracking || !mq.matches) return;
    const deltaX = touchCurrentX - touchStartX;
    const overlayVisible = overlay && !overlay.classList.contains("d-none");
    const SWIPE_THRESHOLD = 60;
    if (!overlayVisible && touchStartX <= EDGE_ZONE && deltaX > SWIPE_THRESHOLD) {
      openOverlay();
    } else if (overlayVisible && deltaX < -SWIPE_THRESHOLD) {
      closeOverlay();
    }
    tracking = false;
    touchStartX = 0;
    touchCurrentX = 0;
  };

  document.addEventListener("touchstart", onTouchStart, { passive: true });
  document.addEventListener("touchmove", onTouchMove, { passive: true });
  document.addEventListener("touchend", onTouchEnd, { passive: true });

  // Mobile modals: open/close helpers with back button support
  const modalStack = [];
  const openMobileModal = (id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const inst = bootstrap.Modal.getOrCreateInstance(el, { backdrop: true, focus: true });
    inst.show();
    modalStack.push(inst);
    document.body.style.overflow = "hidden";
  };
  const closeTopModal = () => {
    const inst = modalStack.pop();
    inst?.hide();
    if (!modalStack.length) {
      document.body.style.overflow = "";
    }
  };
  document.addEventListener("click", (evt) => {
    const target = evt.target.closest("[data-mobile-modal]");
    if (target) {
      evt.preventDefault();
      openMobileModal(target.dataset.mobileModal);
    }
  });
  window.addEventListener("popstate", () => {
    if (modalStack.length) {
      closeTopModal();
    }
  });

  // Ensure all mobile-visible modals have a close button
  document.addEventListener("shown.bs.modal", (evt) => {
    if (!isMobile()) return;
    const modalEl = evt.target;
    const content = modalEl.querySelector(".modal-content") || modalEl;
    if (content.dataset.mobileCloseInjected === "1") return;
    if (!content.querySelector(".mobile-modal-close")) {
      const closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "btn-close btn-close-white mobile-modal-close";
      closeBtn.setAttribute("aria-label", "Close");
      closeBtn.setAttribute("data-bs-dismiss", "modal");
      closeBtn.addEventListener("click", () => {
        const inst = bootstrap.Modal.getInstance(modalEl);
        inst?.hide();
      });
      content.appendChild(closeBtn);
    }
    content.dataset.mobileCloseInjected = "1";
  });

  // Cleanup orphaned backdrops on load
  if (isMobile()) {
    document.querySelectorAll(".modal-backdrop").forEach((b) => b.remove());
    document.body.classList.remove("modal-open");
  }

  document.addEventListener("hidden.bs.modal", () => {
    if (!isMobile()) return;
    document.querySelectorAll(".modal-backdrop").forEach((b) => b.remove());
    document.body.classList.remove("modal-open");
  });

  // Swipe-to-close support for mobile full modals
  let swipeStartY = 0;
  let swipeTarget = null;
  document.addEventListener("touchstart", (e) => {
    if (!isMobile()) return;
    const target = e.target.closest(".mobile-full-modal");
    if (!target) return;
    swipeTarget = target;
    swipeStartY = e.touches[0].clientY;
  }, { passive: true });

  document.addEventListener("touchmove", (e) => {
    if (!isMobile() || !swipeTarget) return;
    const diff = e.touches[0].clientY - swipeStartY;
    if (diff > 50) {
      swipeTarget.classList.add("swipe-down");
    }
  }, { passive: true });

  document.addEventListener("touchend", () => {
    if (!isMobile() || !swipeTarget) return;
    if (swipeTarget.classList.contains("swipe-down")) {
      (swipeTarget.querySelector(".mobile-modal-close") || swipeTarget.querySelector(".btn-close"))?.click();
    }
    swipeTarget.classList.remove("swipe-down");
    swipeTarget = null;
  });

  // Hide drawer when modal shows (avoid z-index conflicts)
  document.addEventListener("shown.bs.modal", () => {
    document.getElementById("sidebarMenu")?.classList.remove("mobile-active");
  });

  // Resync mobile modal colors on theme change
  document.addEventListener("theme-change", () => {
    if (!isMobile()) return;
    const bodyStyle = getComputedStyle(document.body);
    document.querySelectorAll(".mobile-full-modal").forEach((modal) => {
      modal.style.backgroundColor = bodyStyle.backgroundColor;
      modal.style.color = bodyStyle.color;
    });
  });

  // Card row accordion for mobile tables (folder detail & card listings)
  const initMobileRows = () => {
    if (!isMobile()) return;
    const rows = Array.from(document.querySelectorAll("#folderCardsTable tbody tr.deck-row, .cards-table tbody tr.card-row"));
    rows.forEach((row) => {
      if (row.dataset.mobileReady === "1") return;
      row.dataset.mobileReady = "1";

      const name = row.getAttribute("data-name") || row.querySelector("a")?.textContent || "Card";
      const setCode = row.getAttribute("data-set") || row.querySelector("[data-set]")?.dataset?.set || "";
      const rarity = row.getAttribute("data-rarity") || row.getAttribute("data-rar") || "";
      const qty = row.getAttribute("data-qty") || row.getAttribute("data-quantity") || row.querySelector(".col-qty")?.textContent?.trim() || "";
      const cmc = row.getAttribute("data-cmc") || row.getAttribute("data-cmc-bucket") || "";
      const manaCost = row.getAttribute("data-mana") || "";
      const imgSmall = row.getAttribute("data-img-small") || row.getAttribute("data-img") || row.querySelector("img")?.getAttribute("src") || "";
      const imgLarge = row.getAttribute("data-img-large") || row.getAttribute("data-img-normal") || row.getAttribute("data-img") || "";
      const oracle = row.getAttribute("data-oracle") || "";
      const type = row.getAttribute("data-type") || "";

      const cardWrap = document.createElement("div");
      cardWrap.className = "mobile-row-card";

      const media = document.createElement("div");
      media.className = "mobile-row-media";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = imgSmall || imgLarge;
      if (imgLarge) img.dataset.large = imgLarge;
      media.appendChild(img);

      const body = document.createElement("div");
      body.className = "mobile-row-body";

      const head = document.createElement("div");
      head.className = "mobile-row-headline";
      head.textContent = name;

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "btn btn-outline-secondary btn-sm mobile-toggle";
      toggle.textContent = "Details";

      const metaWrap = document.createElement("div");
      metaWrap.className = "mobile-row-meta";
      if (setCode) {
        const setBadge = document.createElement("span");
        setBadge.className = "badge text-bg-secondary";
        setBadge.textContent = setCode.toUpperCase();
        metaWrap.appendChild(setBadge);
      }
      if (rarity) {
        const rarBadge = document.createElement("span");
        rarBadge.className = "badge text-bg-dark";
        rarBadge.textContent = rarity;
        metaWrap.appendChild(rarBadge);
      }
      if (cmc) {
        const cmcBadge = document.createElement("span");
        cmcBadge.className = "badge text-bg-info text-dark";
        cmcBadge.textContent = `CMC ${cmc}`;
        metaWrap.appendChild(cmcBadge);
      }
      if (qty) {
        const qtyBadge = document.createElement("span");
        qtyBadge.className = "badge text-bg-primary";
        qtyBadge.textContent = `Qty ${qty}`;
        metaWrap.appendChild(qtyBadge);
      }
      if (manaCost) {
        const mana = document.createElement("span");
        mana.className = "badge text-bg-secondary";
        mana.innerHTML = manaCost;
        metaWrap.appendChild(mana);
      }

      const detail = document.createElement("div");
      detail.className = "mobile-detail";
      detail.innerHTML = `
        ${type ? `<div class="mb-1"><strong>Type:</strong> ${type}</div>` : ""}
        ${oracle ? `<div class="mb-1"><strong>Text:</strong> ${oracle}</div>` : ""}
      `;

      body.appendChild(head);
      body.appendChild(metaWrap);
      body.appendChild(toggle);
      body.appendChild(detail);

      cardWrap.appendChild(media);
      cardWrap.appendChild(body);
      row.appendChild(cardWrap);
      row.classList.add("mobile-row-wrapped");

      const toggleDetail = () => {
        const expanded = row.classList.toggle("mobile-expanded");
        toggle.textContent = expanded ? "Hide" : "Details";
        if (expanded && img.dataset.large) {
          img.src = img.dataset.large;
          img.removeAttribute("data-large");
        }
      };

      row.addEventListener("click", (evt) => {
        if (evt.target.closest("a,button,input,select,label")) return;
        toggleDetail();
      });
      toggle.addEventListener("click", (evt) => {
        evt.preventDefault();
        evt.stopPropagation();
        toggleDetail();
      });
    });
  };

  initMobileRows();

  // Build-A-Deck: mobile tabs + filters + FAB (mobile only, non-desktop)
  const deckTabs = document.querySelectorAll("[data-build-tab]");
  const deckSections = document.querySelectorAll("[data-build-section]");
  const deckFab = document.getElementById("buildFab");

  const activateTab = (key) => {
    deckTabs.forEach((tab) => {
      const active = tab.dataset.buildTab === key;
      tab.classList.toggle("active", active);
    });
    deckSections.forEach((section) => {
      section.classList.toggle("d-none", section.dataset.buildSection !== key);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  deckTabs.forEach((tab) => {
    tab.addEventListener("click", (evt) => {
      evt.preventDefault();
      activateTab(tab.dataset.buildTab);
    });
  });

  if (deckFab) {
    deckFab.addEventListener("click", (evt) => {
      evt.preventDefault();
      const advBtn = document.querySelector('[data-bs-target="#cardSearchModal"]');
      advBtn?.click();
    });
  }
})();
