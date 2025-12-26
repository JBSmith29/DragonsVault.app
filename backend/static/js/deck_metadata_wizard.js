(function () {
  const dataEl = document.getElementById("deckMetadataWizardData");
  if (!dataEl) return;

  let payload = null;
  try {
    payload = JSON.parse(dataEl.textContent || "{}");
  } catch (_) {
    return;
  }

  const triggerBtn = document.getElementById("deckMetadataWizardBtn");
  const modalEl = document.getElementById("deckMetadataWizardModal");
  if (!triggerBtn || !modalEl || !window.bootstrap) return;

  const decks = Array.isArray(payload?.decks) ? payload.decks.slice() : [];
  const tagGroups = payload?.tag_groups && typeof payload.tag_groups === "object" ? payload.tag_groups : {};
  const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
  const csrfToken = window.csrfToken || document.querySelector('meta[name="csrf-token"]')?.content || "";

  const els = {
    alert: document.getElementById("deckMetadataWizardAlert"),
    body: document.getElementById("deckMetadataWizardBody"),
    complete: document.getElementById("deckMetadataWizardComplete"),
    deckName: document.getElementById("deckMetadataWizardDeckName"),
    progress: document.getElementById("deckMetadataWizardProgress"),
    commanderCurrent: document.getElementById("deckMetadataWizardCommanderCurrent"),
    tagCurrent: document.getElementById("deckMetadataWizardTagCurrent"),
    commanderHint: document.getElementById("deckMetadataWizardCommanderHint"),
    tagHint: document.getElementById("deckMetadataWizardTagHint"),
    commanderChangeBtn: document.getElementById("deckMetadataWizardCommanderChangeBtn"),
    commanderKeepBtn: document.getElementById("deckMetadataWizardCommanderKeepBtn"),
    commanderClearBtn: document.getElementById("deckMetadataWizardCommanderClearBtn"),
    tagChangeBtn: document.getElementById("deckMetadataWizardTagChangeBtn"),
    tagKeepBtn: document.getElementById("deckMetadataWizardTagKeepBtn"),
    tagClearBtn: document.getElementById("deckMetadataWizardTagClearBtn"),
    commanderPicker: document.getElementById("deckMetadataWizardCommanderPicker"),
    tagPicker: document.getElementById("deckMetadataWizardTagPicker"),
    commanderFilter: document.getElementById("deckMetadataWizardCommanderFilter"),
    commanderList: document.getElementById("deckMetadataWizardCommanderList"),
    tagFilter: document.getElementById("deckMetadataWizardTagFilter"),
    tagGroups: document.getElementById("deckMetadataWizardTagGroups"),
    tagEmpty: document.getElementById("deckMetadataWizardTagEmpty"),
    summary: document.getElementById("deckMetadataWizardSummary"),
    footerStatus: document.getElementById("deckMetadataWizardFooterStatus"),
    skipBtn: document.getElementById("deckMetadataWizardSkipBtn"),
    applyBtn: document.getElementById("deckMetadataWizardApplyBtn"),
  };

  if (!els.body || !els.complete) return;

  const ACTION_KEEP = "keep";
  const ACTION_SET = "set";
  const ACTION_CLEAR = "clear";

  let index = 0;
  let currentDeck = null;
  let commanderAction = ACTION_KEEP;
  let tagAction = ACTION_KEEP;
  let selectedCommanders = [];
  let selectedTag = "";
  let commanderCandidates = [];
  let commanderCache = new Map();
  let tagButtons = [];
  let commanderPickerVisible = true;
  let tagPickerVisible = true;
  let busy = false;

  function clearFilters() {
    if (els.commanderFilter) {
      els.commanderFilter.value = "";
    }
    if (els.tagFilter) {
      els.tagFilter.value = "";
    }
  }

  function showAlert(message, tone = "warning") {
    if (!els.alert) return;
    if (!message) {
      els.alert.classList.add("d-none");
      els.alert.textContent = "";
      return;
    }
    els.alert.className = `alert alert-${tone}`;
    els.alert.textContent = message;
  }

  function setFooterStatus(message) {
    if (!els.footerStatus) return;
    els.footerStatus.textContent = message || "";
  }

  function getSelectedCommanderNames() {
    return selectedCommanders.map(item => item.name).filter(Boolean);
  }

  function setCommanderAction(action) {
    commanderAction = action;
    if (action !== ACTION_SET) {
      selectedCommanders = [];
    }
    updateCommanderUI();
    updateSummary();
  }

  function setTagAction(action) {
    tagAction = action;
    if (action === ACTION_CLEAR) {
      selectedTag = "";
    } else if (action === ACTION_KEEP) {
      selectedTag = currentDeck?.deck_tag || "";
    } else if (action === ACTION_SET && !selectedTag) {
      selectedTag = currentDeck?.deck_tag || "";
    }
    updateTagUI();
    updateSummary();
  }

  function updateCommanderUI() {
    if (!currentDeck) return;
    const hasCommander = !currentDeck.missing_commander;
    const pickerVisible = commanderPickerVisible;
    if (els.commanderPicker) {
      els.commanderPicker.classList.toggle("d-none", !pickerVisible);
    }
    if (els.commanderChangeBtn) {
      const showChange = !pickerVisible;
      els.commanderChangeBtn.classList.toggle("d-none", !showChange);
      if (showChange) {
        els.commanderChangeBtn.textContent = hasCommander ? "Change commander" : "Set commander";
      }
    }
    if (els.commanderKeepBtn) {
      els.commanderKeepBtn.classList.toggle("d-none", !pickerVisible);
      els.commanderKeepBtn.textContent = hasCommander ? "Keep current" : "Skip for now";
    }
    if (els.commanderClearBtn) {
      els.commanderClearBtn.classList.toggle("d-none", !hasCommander || pickerVisible);
    }
    if (els.commanderHint) {
      if (!hasCommander && pickerVisible) {
        els.commanderHint.textContent = "Select a commander to complete this deck, or skip for now.";
      } else if (!hasCommander) {
        els.commanderHint.textContent = "Commander skipped for now.";
      } else if (pickerVisible) {
        els.commanderHint.textContent = "Select a new commander (optional).";
      } else if (commanderAction === ACTION_CLEAR) {
        els.commanderHint.textContent = "Commander will be cleared.";
      } else {
        els.commanderHint.textContent = "Current commander will be kept.";
      }
    }
    if (pickerVisible) {
      ensureCommanderCandidates();
    }
  }

  function updateTagUI() {
    if (!currentDeck) return;
    const hasTag = !currentDeck.missing_tag;
    const pickerVisible = tagPickerVisible;
    if (els.tagPicker) {
      els.tagPicker.classList.toggle("d-none", !pickerVisible);
    }
    if (els.tagChangeBtn) {
      const showChange = !pickerVisible;
      els.tagChangeBtn.classList.toggle("d-none", !showChange);
      if (showChange) {
        els.tagChangeBtn.textContent = hasTag ? "Change tag" : "Set tag";
      }
    }
    if (els.tagKeepBtn) {
      els.tagKeepBtn.classList.toggle("d-none", !pickerVisible);
      els.tagKeepBtn.textContent = hasTag ? "Keep current" : "Skip for now";
    }
    if (els.tagClearBtn) {
      els.tagClearBtn.classList.toggle("d-none", !hasTag || pickerVisible);
    }
    if (els.tagHint) {
      if (!hasTag && pickerVisible) {
        els.tagHint.textContent = "Select a tag to complete this deck, or skip for now.";
      } else if (!hasTag) {
        els.tagHint.textContent = "Tag skipped for now.";
      } else if (pickerVisible) {
        els.tagHint.textContent = "Select a new tag (optional).";
      } else if (tagAction === ACTION_CLEAR) {
        els.tagHint.textContent = "Tag will be cleared.";
      } else {
        els.tagHint.textContent = "Current tag will be kept.";
      }
    }
    if (pickerVisible) {
      renderTagSelection();
    }
  }

  function updateSummary() {
    if (!els.summary) return;
    const items = [];
    if (commanderAction === ACTION_SET) {
      const names = getSelectedCommanderNames();
      if (names.length) {
        items.push(`Commander → ${names.join(" // ")}`);
      } else {
        items.push("Commander → Not selected yet");
      }
    } else if (commanderAction === ACTION_CLEAR) {
      items.push("Commander → Will be cleared");
    }
    if (tagAction === ACTION_SET) {
      if (selectedTag) {
        items.push(`Tag → ${selectedTag}`);
      } else {
        items.push("Tag → Not selected yet");
      }
    } else if (tagAction === ACTION_CLEAR) {
      items.push("Tag → Will be cleared");
    }

    if (!items.length) {
      els.summary.textContent = "No changes selected yet.";
    } else {
      els.summary.innerHTML = `<ul class="mb-0 ps-3">${items.map(item => `<li>${item}</li>`).join("")}</ul>`;
    }
    updateApplyState();
  }

  function updateApplyState() {
    if (!els.applyBtn) return;
    const commanderReady = commanderAction !== ACTION_SET || selectedCommanders.length > 0;
    const tagReady = tagAction !== ACTION_SET || Boolean(selectedTag);
    const hasChange =
      (commanderAction === ACTION_SET && selectedCommanders.length > 0)
      || commanderAction === ACTION_CLEAR
      || (tagAction === ACTION_SET && selectedTag)
      || tagAction === ACTION_CLEAR;
    const canApply = !busy && commanderReady && tagReady && hasChange;
    els.applyBtn.disabled = !canApply;
  }

  function setCommanderSelection(candidate, mode) {
    if (!candidate) return;
    const key = (candidate.oracle_id || candidate.name || "").toLowerCase();
    if (!key) return;
    if (mode === "append") {
      const exists = selectedCommanders.some(item => (item.oracle_id || item.name || "").toLowerCase() === key);
      if (exists || selectedCommanders.length >= 2) return;
      selectedCommanders = [...selectedCommanders, candidate];
    } else {
      selectedCommanders = [candidate];
    }
    commanderAction = ACTION_SET;
    updateCommanderUI();
    renderCommanderCandidates();
    updateSummary();
  }

  function clearCommanderSelection() {
    selectedCommanders = [];
    commanderAction = ACTION_CLEAR;
    updateCommanderUI();
    renderCommanderCandidates();
    updateSummary();
  }

  function buildCommanderCard(candidate) {
    const card = document.createElement("div");
    card.className = "legend-card wizard-commander-card";
    const key = (candidate.oracle_id || candidate.name || "").toLowerCase();
    const isSelected = selectedCommanders.some(item => (item.oracle_id || item.name || "").toLowerCase() === key);
    if (isSelected) {
      card.classList.add("active");
    }

    const thumbWrap = document.createElement("div");
    thumbWrap.className = "legend-thumb-wrap";
    if (candidate.image) {
      const img = document.createElement("img");
      img.className = "legend-thumb";
      img.loading = "lazy";
      img.alt = candidate.name || "Commander";
      img.src = candidate.image;
      thumbWrap.appendChild(img);
    } else {
      const placeholder = document.createElement("div");
      placeholder.className = "bg-secondary-subtle rounded w-100 h-100";
      placeholder.style.opacity = ".35";
      thumbWrap.appendChild(placeholder);
    }

    const body = document.createElement("div");
    body.className = "legend-body";
    const title = document.createElement("div");
    title.className = "legend-title text-truncate";
    title.textContent = candidate.name || "Unknown";
    const meta = document.createElement("div");
    meta.className = "legend-sub text-truncate";
    const parts = [];
    if (candidate.set_code) parts.push(String(candidate.set_code).toUpperCase());
    if (candidate.collector_number) parts.push(`#${candidate.collector_number}`);
    if (candidate.is_foil) parts.push("Foil");
    meta.textContent = parts.join(" ") || "Legendary permanent";

    const actions = document.createElement("div");
    actions.className = "d-flex flex-wrap gap-2 mt-2";

    const selectBtn = document.createElement("button");
    selectBtn.type = "button";
    selectBtn.className = `btn btn-sm ${isSelected ? "btn-primary" : "btn-outline-primary"}`;
    selectBtn.textContent = isSelected ? "Selected" : "Use commander";
    selectBtn.addEventListener("click", () => setCommanderSelection(candidate, "replace"));

    const partnerBtn = document.createElement("button");
    partnerBtn.type = "button";
    partnerBtn.className = "btn btn-sm btn-outline-secondary";
    partnerBtn.textContent = "Add partner";
    partnerBtn.disabled = selectedCommanders.length >= 2;
    partnerBtn.addEventListener("click", () => setCommanderSelection(candidate, "append"));

    actions.append(selectBtn, partnerBtn);
    body.append(title, meta, actions);
    card.append(thumbWrap, body);
    return card;
  }

  function renderCommanderCandidates() {
    if (!els.commanderList) return;
    const query = (els.commanderFilter?.value || "").trim().toLowerCase();
    els.commanderList.innerHTML = "";
    const fragment = document.createDocumentFragment();
    let visible = 0;
    commanderCandidates.forEach(candidate => {
      const name = (candidate.name || "").toLowerCase();
      if (query && !name.includes(query)) return;
      fragment.appendChild(buildCommanderCard(candidate));
      visible += 1;
    });
    if (!visible) {
      els.commanderList.innerHTML = `<div class="text-muted text-center py-4 w-100">${query ? "No commanders match that search." : "No commander candidates found yet."}</div>`;
      return;
    }
    els.commanderList.appendChild(fragment);
  }

  async function ensureCommanderCandidates() {
    if (!currentDeck || !currentDeck.actions?.commander_candidates_url || !els.commanderList) return;
    const cached = commanderCache.get(currentDeck.id);
    if (cached) {
      commanderCandidates = cached;
      renderCommanderCandidates();
      return;
    }
    els.commanderList.innerHTML = '<div class="text-muted text-center py-4 w-100">Loading commander options…</div>';
    try {
      const response = await fetch(currentDeck.actions.commander_candidates_url, {
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw new Error((data && data.error) || "Unable to load commander options.");
      }
      commanderCandidates = Array.isArray(data.candidates) ? data.candidates : [];
      commanderCache.set(currentDeck.id, commanderCandidates);
      renderCommanderCandidates();
    } catch (error) {
      const message = error?.message || "Unable to load commander options.";
      els.commanderList.innerHTML = `<div class="text-danger text-center py-4 w-100">${message}</div>`;
    }
  }

  function buildTagGroups() {
    if (!els.tagGroups) return;
    els.tagGroups.innerHTML = "";
    tagButtons = [];
    const fragment = document.createDocumentFragment();

    Object.entries(tagGroups).forEach(([category, tags]) => {
      const section = document.createElement("div");
      section.className = "tag-category mb-3";
      section.dataset.category = (category || "").toLowerCase();
      const heading = document.createElement("div");
      heading.className = "small text-uppercase text-muted fw-semibold mb-2";
      heading.textContent = category;
      section.appendChild(heading);
      const wrap = document.createElement("div");
      wrap.className = "d-flex flex-wrap gap-2";
      (tags || []).forEach(tag => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "type-pill wizard-tag-option";
      btn.textContent = tag;
        btn.dataset.tag = tag;
        btn.dataset.label = String(tag || "").toLowerCase();
        btn.dataset.category = (category || "").toLowerCase();
      btn.addEventListener("click", () => {
        selectedTag = tag;
        tagAction = ACTION_SET;
        renderTagSelection();
        updateSummary();
      });
        tagButtons.push(btn);
        wrap.appendChild(btn);
      });
      section.appendChild(wrap);
      fragment.appendChild(section);
    });
    els.tagGroups.appendChild(fragment);
  }

  function renderTagSelection() {
    if (!currentDeck) return;
    if (!tagButtons.length) {
      buildTagGroups();
    }
    const query = (els.tagFilter?.value || "").trim().toLowerCase();
    let visibleCount = 0;
    tagButtons.forEach(btn => {
      const label = btn.dataset.label || "";
      const category = btn.dataset.category || "";
      const match = !query || label.includes(query) || category.includes(query);
      btn.classList.toggle("d-none", !match);
      if (match) visibleCount += 1;
      btn.classList.toggle("active", tagAction === ACTION_SET && selectedTag === btn.dataset.tag);
    });
    const sections = els.tagGroups?.querySelectorAll(".tag-category") || [];
    sections.forEach(section => {
      const hasVisible = Boolean(section.querySelector(".wizard-tag-option:not(.d-none)"));
      section.classList.toggle("d-none", !hasVisible);
    });
    if (els.tagEmpty) {
      els.tagEmpty.classList.toggle("d-none", visibleCount > 0);
    }
  }

  function updateDeckHeader() {
    if (!currentDeck) return;
    if (els.deckName) {
      els.deckName.textContent = currentDeck.name || "Deck";
    }
    if (els.progress) {
      const total = decks.length;
      els.progress.textContent = total ? `Deck ${Math.min(index + 1, total)} of ${total}` : "";
    }
    if (els.commanderCurrent) {
      els.commanderCurrent.textContent = currentDeck.commander_name
        || (currentDeck.missing_commander ? "Not set" : "Commander set");
    }
    if (els.tagCurrent) {
      els.tagCurrent.textContent = currentDeck.deck_tag || "Not set";
    }
  }

  function resetStateForDeck(deck) {
    currentDeck = deck;
    const hasCommander = !deck.missing_commander;
    const hasTag = !deck.missing_tag;
    commanderAction = hasCommander ? ACTION_KEEP : ACTION_SET;
    tagAction = hasTag ? ACTION_KEEP : ACTION_SET;
    selectedCommanders = [];
    selectedTag = hasTag ? deck.deck_tag : "";
    clearFilters();
    commanderPickerVisible = true;
    tagPickerVisible = true;
    showAlert("");
    setFooterStatus("");
    updateDeckHeader();
    updateCommanderUI();
    updateTagUI();
    updateSummary();
  }

  function showCompletion() {
    els.body.classList.add("d-none");
    els.complete.classList.remove("d-none");
    if (els.applyBtn) els.applyBtn.disabled = true;
    if (els.skipBtn) els.skipBtn.disabled = true;
  }

  function showWizard() {
    if (!decks.length) {
      showCompletion();
      modal.show();
      return;
    }
    if (index >= decks.length) index = 0;
    resetStateForDeck(decks[index]);
    els.body.classList.remove("d-none");
    els.complete.classList.add("d-none");
    if (els.skipBtn) els.skipBtn.disabled = false;
    updateApplyState();
    modal.show();
  }

  function advanceDeck() {
    if (!currentDeck) return;
    decks.splice(index, 1);
    if (!decks.length) {
      showCompletion();
      triggerBtn.title = "All decks already have a commander and tag.";
      return;
    }
    if (index >= decks.length) index = 0;
    resetStateForDeck(decks[index]);
  }

  function updateDeckRow(commanderLabel, tagLabel) {
    if (!currentDeck) return;
    const row = document.querySelector(`.deck-row[data-deck-id="${currentDeck.id}"]`);
    if (!row) return;
    const commanderButtons = row.querySelectorAll('[data-commander-trigger="true"]');
    commanderButtons.forEach(btn => {
      if (commanderLabel) {
        btn.dataset.commanderName = commanderLabel;
      } else {
        btn.dataset.commanderName = "";
      }
    });
    const commanderLink = row.querySelector(".commander-set-link");
    if (commanderLink) {
      commanderLink.textContent = commanderLabel ? `Commander: ${commanderLabel}` : "Click here to set commander";
    }
    const tagBtn = row.querySelector(".deck-tag-trigger");
    if (tagBtn) {
      if (tagLabel) {
        tagBtn.textContent = tagLabel;
        tagBtn.dataset.currentTag = tagLabel;
        tagBtn.dataset.currentLabel = tagLabel;
        tagBtn.title = "Manual deck tag";
      } else {
        tagBtn.textContent = "Set tag";
        tagBtn.dataset.currentTag = "";
        tagBtn.dataset.currentLabel = "";
        tagBtn.title = "Set a manual deck tag";
      }
    }
  }

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        ...(csrfToken ? { "X-CSRFToken": csrfToken } : {}),
      },
      credentials: "same-origin",
      body: JSON.stringify(payload || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const message = (data && (data.error || data.message)) || "Unable to complete request.";
      throw new Error(message);
    }
    return data;
  }

  async function applyChanges() {
    if (!currentDeck || busy) return;
    const commanderReady = commanderAction !== ACTION_SET || selectedCommanders.length > 0;
    const tagReady = tagAction !== ACTION_SET || Boolean(selectedTag);
    const hasChange =
      (commanderAction === ACTION_SET && selectedCommanders.length > 0)
      || commanderAction === ACTION_CLEAR
      || (tagAction === ACTION_SET && selectedTag)
      || tagAction === ACTION_CLEAR;
    if (!hasChange || !commanderReady || !tagReady) return;

    busy = true;
    updateApplyState();
    setFooterStatus("Saving changes...");
    showAlert("");

    try {
      if (commanderAction === ACTION_CLEAR) {
        await postJson(currentDeck.actions.commander_clear_url, {});
      } else if (commanderAction === ACTION_SET) {
        const payload = {
          commanders: selectedCommanders.map(item => ({
            name: item.name,
            oracle_id: item.oracle_id,
          })),
          mode: "replace",
        };
        await postJson(currentDeck.actions.commander_set_url, payload);
      }

      if (tagAction === ACTION_CLEAR) {
        await postJson(currentDeck.actions.tag_clear_url, {});
      } else if (tagAction === ACTION_SET) {
        await postJson(currentDeck.actions.tag_set_url, { tag: selectedTag });
      }

      const commanderLabel = commanderAction === ACTION_CLEAR
        ? ""
        : commanderAction === ACTION_SET
          ? getSelectedCommanderNames().join(" // ")
          : currentDeck.commander_name;
      const tagLabel = tagAction === ACTION_CLEAR
        ? ""
        : tagAction === ACTION_SET
          ? selectedTag
          : currentDeck.deck_tag;
      updateDeckRow(commanderLabel, tagLabel);
      clearFilters();
      advanceDeck();
    } catch (error) {
      showAlert(error?.message || "Unable to save changes right now.", "danger");
    } finally {
      busy = false;
      setFooterStatus("");
      updateApplyState();
    }
  }

  function skipDeck() {
    if (!currentDeck || busy) return;
    advanceDeck();
  }

  if (els.commanderChangeBtn) {
    els.commanderChangeBtn.addEventListener("click", () => {
      commanderPickerVisible = true;
      setCommanderAction(ACTION_SET);
    });
  }
  if (els.commanderKeepBtn) {
    els.commanderKeepBtn.addEventListener("click", () => {
      commanderPickerVisible = false;
      setCommanderAction(ACTION_KEEP);
    });
  }
  if (els.commanderClearBtn) {
    els.commanderClearBtn.addEventListener("click", () => clearCommanderSelection());
  }
  if (els.tagChangeBtn) {
    els.tagChangeBtn.addEventListener("click", () => {
      tagPickerVisible = true;
      setTagAction(ACTION_SET);
    });
  }
  if (els.tagKeepBtn) {
    els.tagKeepBtn.addEventListener("click", () => {
      tagPickerVisible = false;
      setTagAction(ACTION_KEEP);
    });
  }
  if (els.tagClearBtn) {
    els.tagClearBtn.addEventListener("click", () => {
      setTagAction(ACTION_CLEAR);
    });
  }
  if (els.commanderFilter) {
    els.commanderFilter.addEventListener("input", renderCommanderCandidates);
  }
  if (els.tagFilter) {
    els.tagFilter.addEventListener("input", renderTagSelection);
  }
  if (els.skipBtn) {
    els.skipBtn.addEventListener("click", skipDeck);
  }
  if (els.applyBtn) {
    els.applyBtn.addEventListener("click", applyChanges);
  }

  triggerBtn.addEventListener("click", showWizard);

  if (!decks.length) {
    triggerBtn.title = "All decks already have a commander and tag.";
  }
})();
