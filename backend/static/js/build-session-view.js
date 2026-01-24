(function () {
  function safeGetSession(key) {
    try {
      return window.sessionStorage ? sessionStorage.getItem(key) : null;
    } catch (_) {
      return null;
    }
  }

  function safeSetSession(key, value) {
    try {
      if (window.sessionStorage) {
        sessionStorage.setItem(key, value);
      }
    } catch (_) {
      // Ignore storage failures (private mode, quota).
    }
  }

  function init() {
    const currentBuild = document.getElementById('buildCurrent');
    if (!currentBuild || currentBuild.dataset.buildViewReady === 'true') {
      return;
    }

    const toggles = Array.from(currentBuild.querySelectorAll('[data-build-view-toggle]'));
    const views = Array.from(currentBuild.querySelectorAll('[data-build-view]'));
    if (!toggles.length || !views.length) {
      return;
    }

    currentBuild.dataset.buildViewReady = 'true';

    const sessionContainer = currentBuild.closest('[data-build-session-id]');
    const sessionId = sessionContainer ? (sessionContainer.dataset.buildSessionId || '0') : '0';
    const storageKey = `buildSessionView:${sessionId}`;
    let currentView = '';

    const viewInputs = () => Array.from(currentBuild.querySelectorAll('input[name="build_view"]'));

    function setView(name, persist = true) {
      const target = name || 'list';
      currentView = target;
      views.forEach((view) => {
        view.classList.toggle('d-none', view.dataset.buildView !== target);
      });
      toggles.forEach((btn) => {
        const isActive = btn.dataset.view === target;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
      });
      viewInputs().forEach((input) => {
        input.value = target;
      });
      if (persist) {
        safeSetSession(storageKey, target);
      }
    }

    toggles.forEach((btn) => {
      btn.addEventListener('click', () => setView(btn.dataset.view));
    });

    const initialFromServer = (currentBuild.dataset.buildViewInitial || '').trim();
    const stored = safeGetSession(storageKey);
    const initial = stored || initialFromServer || 'list';
    setView(initial, false);

    document.addEventListener(
      'submit',
      (event) => {
        if (!(event.target instanceof HTMLFormElement)) {
          return;
        }
        if (!currentBuild.contains(event.target)) {
          return;
        }
        let input = event.target.querySelector('input[name="build_view"]');
        if (!input) {
          input = document.createElement('input');
          input.type = 'hidden';
          input.name = 'build_view';
          event.target.appendChild(input);
        }
        const value = currentView || initial || 'list';
        input.value = value;
        safeSetSession(storageKey, value);
      },
      true,
    );
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  document.addEventListener('htmx:afterSwap', init);
})();
