(function () {
  const deckSelectWrapper = document.querySelector('[data-dv-select="opening-deck"]');
  const deckSelectInput = deckSelectWrapper ? deckSelectWrapper.querySelector('[data-dv-select-input]') : null;
  const deckSelectLabel = deckSelectWrapper ? deckSelectWrapper.querySelector('[data-dv-select-label]') : null;
  const deckOptionButtons = deckSelectWrapper
    ? Array.from(deckSelectWrapper.querySelectorAll('[data-dv-select-option]'))
    : [];
  const deckListInput = document.getElementById('openingHandDeckList');
  const commanderInput = document.getElementById('openingHandCommander');
  const startBtn = document.getElementById('openingHandStartBtn');
  const helpText = document.getElementById('openingHandHelp');

  function selectionState() {
    const deckId = deckSelectInput ? (deckSelectInput.value || '').trim() : '';
    const deckList = deckListInput ? (deckListInput.value || '').trim() : '';
    return {
      deckId,
      deckList,
      hasSelection: Boolean(deckId || deckList),
    };
  }

  function setHelpText(message) {
    if (helpText) {
      helpText.textContent = message;
    }
  }

  function updateStartState() {
    const state = selectionState();
    if (startBtn) {
      startBtn.disabled = !state.hasSelection;
    }
    if (!state.hasSelection) {
      setHelpText('Select a deck or paste a list to continue.');
    } else if (state.deckId) {
      setHelpText('Using a saved deck or proxy build.');
    } else {
      setHelpText('Using a pasted list.');
    }
  }

  function clearDeckSelect() {
    if (!deckSelectWrapper) {
      return;
    }
    const placeholder = 'Select a deck';
    deckOptionButtons.forEach((btn) => {
      btn.classList.toggle('active', (btn.dataset.value || '') === '');
    });
    if (deckSelectInput) {
      deckSelectInput.value = '';
      deckSelectInput.dispatchEvent(new Event('input', { bubbles: true }));
      deckSelectInput.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (deckSelectLabel) {
      deckSelectLabel.textContent = placeholder;
    }
    deckSelectWrapper.dataset.dvSelectValue = '';
  }

  if (deckSelectWrapper) {
    deckSelectWrapper.addEventListener('dv-select:ready', updateStartState);
    deckSelectWrapper.addEventListener('dv-select:change', function (event) {
      const value = event.detail && event.detail.value ? event.detail.value : '';
      if (value && deckListInput && deckListInput.value.trim()) {
        deckListInput.value = '';
        if (commanderInput) {
          commanderInput.value = '';
        }
      }
      updateStartState();
    });
  }

  if (deckSelectInput) {
    deckSelectInput.addEventListener('input', updateStartState);
    deckSelectInput.addEventListener('change', updateStartState);
  }

  if (deckListInput) {
    deckListInput.addEventListener('input', function () {
      if (deckListInput.value.trim()) {
        clearDeckSelect();
      }
      updateStartState();
    });
  }

  updateStartState();
})();
