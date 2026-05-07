/**
 * Global keyboard shortcuts for DragonsVault
 * Provides quick navigation and actions via keyboard
 */

(function () {
  if (window.dvKeyboardShortcuts) return;

  const shortcuts = {
    // Navigation
    '/': { action: 'focusSearch', description: 'Focus search' },
    'g d': { action: 'gotoDashboard', description: 'Go to Dashboard' },
    'g c': { action: 'gotoCards', description: 'Go to Cards' },
    'g k': { action: 'gotoDecks', description: 'Go to Decks' },
    'g g': { action: 'gotoGames', description: 'Go to Games' },
    'g w': { action: 'gotoWishlist', description: 'Go to Wishlist' },
    '?': { action: 'showHelp', description: 'Show keyboard shortcuts' },
    'Escape': { action: 'closeModals', description: 'Close modals/dialogs' },
  };

  let sequenceBuffer = '';
  let sequenceTimeout = null;

  const actions = {
    focusSearch() {
      const searchInput = document.querySelector('input[type="search"], input[name="q"], input[placeholder*="Search"]');
      if (searchInput) {
        searchInput.focus();
        searchInput.select();
      }
    },

    gotoDashboard() {
      window.location.href = '/dashboard';
    },

    gotoCards() {
      window.location.href = '/cards';
    },

    gotoDecks() {
      window.location.href = '/decks';
    },

    gotoGames() {
      window.location.href = '/games';
    },

    gotoWishlist() {
      window.location.href = '/wishlist';
    },

    closeModals() {
      // Close Bootstrap modals
      const modals = document.querySelectorAll('.modal.show');
      modals.forEach(modal => {
        const bsModal = bootstrap?.Modal?.getInstance(modal);
        if (bsModal) {
          bsModal.hide();
        }
      });

      // Close any custom overlays
      const overlays = document.querySelectorAll('[data-dismiss="overlay"]');
      overlays.forEach(overlay => overlay.click());
    },

    showHelp() {
      const helpModal = createHelpModal();
      document.body.appendChild(helpModal);
      const bsModal = new bootstrap.Modal(helpModal);
      bsModal.show();
      helpModal.addEventListener('hidden.bs.modal', () => {
        helpModal.remove();
      });
    },
  };

  function createHelpModal() {
    const modal = document.createElement('div');
    modal.className = 'modal fade';
    modal.tabIndex = -1;
    modal.innerHTML = `
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Keyboard Shortcuts</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <table class="table table-sm">
              <thead>
                <tr>
                  <th>Shortcut</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                ${Object.entries(shortcuts)
                  .map(([key, { description }]) => `
                    <tr>
                      <td><kbd>${key}</kbd></td>
                      <td>${description}</td>
                    </tr>
                  `)
                  .join('')}
              </tbody>
            </table>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
          </div>
        </div>
      </div>
    `;
    return modal;
  }

  function handleKeydown(event) {
    // Ignore if user is typing in an input/textarea
    const target = event.target;
    if (
      target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.isContentEditable
    ) {
      // Allow '/' to focus search even from inputs
      if (event.key === '/' && target.tagName === 'INPUT' && target.type !== 'search') {
        return;
      }
      // Allow Escape to work everywhere
      if (event.key !== 'Escape' && event.key !== '/') {
        return;
      }
    }

    // Handle single-key shortcuts
    if (shortcuts[event.key]) {
      event.preventDefault();
      const action = shortcuts[event.key].action;
      if (actions[action]) {
        actions[action]();
      }
      return;
    }

    // Handle multi-key sequences (e.g., 'g d')
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      sequenceBuffer += event.key;
      
      // Clear timeout
      if (sequenceTimeout) {
        clearTimeout(sequenceTimeout);
      }

      // Check if we have a matching shortcut
      if (shortcuts[sequenceBuffer]) {
        event.preventDefault();
        const action = shortcuts[sequenceBuffer].action;
        if (actions[action]) {
          actions[action]();
        }
        sequenceBuffer = '';
      } else {
        // Wait for next key (1 second timeout)
        sequenceTimeout = setTimeout(() => {
          sequenceBuffer = '';
        }, 1000);
      }
    }
  }

  // Add keyboard event listener
  document.addEventListener('keydown', handleKeydown);

  // Add visual indicator for kbd elements
  const style = document.createElement('style');
  style.textContent = `
    kbd {
      display: inline-block;
      padding: 0.2em 0.4em;
      font-size: 0.875em;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
      line-height: 1;
      color: var(--bs-body-color);
      background-color: var(--bs-tertiary-bg);
      border: 1px solid var(--bs-border-color);
      border-radius: 0.25rem;
      box-shadow: 0 1px 0 rgba(0, 0, 0, 0.1);
    }
  `;
  document.head.appendChild(style);

  window.dvKeyboardShortcuts = {
    register(key, action, description) {
      shortcuts[key] = { action, description };
    },
    unregister(key) {
      delete shortcuts[key];
    },
    addAction(name, fn) {
      actions[name] = fn;
    },
  };
})();
