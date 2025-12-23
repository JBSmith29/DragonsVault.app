/* flip-normalize.js
 * Use a single two-arrows flip button and remove any legacy circular buttons.
 * - Removes .flip-face (circular) if present
 * - Ensures a .cycle-flip button exists with "⇄"
 * - Positions it top-left above prev/next overlays
 * - Rebinds face-flip (if loaded) after normalization / HTMX swaps
 */
(function () {
  function normalize(root) {
    (root || document).querySelectorAll(".js-prints-cycler").forEach((el) => {
      // Remove legacy circular flip buttons injected elsewhere
      el.querySelectorAll(".flip-face").forEach((n) => n.remove());

      // Ensure our single flip control exists
      let btn = el.querySelector(":scope > .cycle-flip");
      if (!btn) {
        btn = document.createElement("button");
        btn.type = "button";
        btn.className = "cycle-flip glyph-btn text-light";
        btn.title = "Flip face";
        btn.setAttribute("aria-label", "Flip face");
        el.appendChild(btn);
      }
      // Use the two arrows symbol (instead of circular)
      btn.textContent = "⇄";

      // Place above the prev/next half-overlays
      btn.style.position = "absolute";
      btn.style.top = "8px";
      btn.style.left = "8px";
      btn.style.zIndex = "3";

      // Make sure a click on flip doesn't bubble into the cycler
      btn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); }, { capture: true, once: true });
    });

    // Re-bind flip handler if face-flip.js is present
    if (window.initFaceFlips) {
      try { window.initFaceFlips(root || document); } catch {}
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => normalize());
  } else {
    normalize();
  }

  // Re-run after HTMX updates
  ["htmx:afterSwap", "htmx:afterSettle", "htmx:load"].forEach((ev) => {
    document.addEventListener(ev, (e) => normalize(e && e.target));
  });

  // Manual hook if needed
  window.dvNormalizeFlipButtons = normalize;
})();
