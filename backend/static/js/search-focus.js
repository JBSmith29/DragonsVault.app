(function () {
  // Utility: should we ignore this keypress (because user is typing into an input/textarea/etc.)?
  function isTypingContext(target) {
    if (!target) return false;
    const tag = target.tagName;
    return (
      target.isContentEditable ||
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT"
    );
  }

  // Focus the element, opening the sidebar first if needed
  function focusSearch() {
    var el = document.getElementById("globalSearch");
    if (!el) return;

    // If search is hidden (e.g., inside collapsed sidebar), try opening it
    if (el.offsetParent === null) {
      var menuToggle = document.getElementById("menuToggle"); // your existing hamburger button id
      if (menuToggle) menuToggle.click();
    }

    // Small delay if layout needs to open/animate
    setTimeout(function () {
      el.focus({ preventScroll: false });
      if (typeof el.select === "function") el.select();
    }, 0);
  }

  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || e.isComposing) return;
    if (isTypingContext(e.target)) return;

    const key = e.key || "";
    const isSlash = key === "/" || e.code === "Slash" || e.keyCode === 191;
    const isCmdOrCtrlK = (key.toLowerCase && key.toLowerCase() === "k") && (e.metaKey || e.ctrlKey);

    if (isSlash || isCmdOrCtrlK) {
      e.preventDefault();  // don’t type "/" into the page
      focusSearch();
    }
  });
})();
