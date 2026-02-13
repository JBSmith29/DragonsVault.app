(function () {
  function initGameEngineApp() {
    const root = document.getElementById('gameEngineApp');
    if (!root || root.dataset.bound === 'true') return;
    root.dataset.bound = 'true';

    const engineEnabled = root.dataset.engineEnabled === 'true';
    const pingUrl = root.dataset.enginePingUrl || '';
    const deckOptionsUrl = root.dataset.deckOptionsUrl || '';

    const createBtn = root.querySelector('#engineCreateBtn');
    const joinBtn = root.querySelector('#engineJoinBtn');
    const refreshBtn = root.querySelector('#engineRefreshBtn');
    const startBtn = root.querySelector('#engineStartBtn');
    const loadDeckBtn = root.querySelector('#engineLoadDeckBtn');
    const copyBtn = root.querySelector('#engineCopyGameId');
    const openTableBtn = root.querySelector('#engineOpenTableBtn');

    const formatEl = root.querySelector('#engineFormat');
    const gameIdEl = root.querySelector('#engineGameId');
    const inviteIdEl = root.querySelector('#engineInviteId');

    const createDeckSelectEl = root.querySelector('#engineCreateDeckSelect');
    const joinDeckSelectEl = root.querySelector('#engineJoinDeckSelect');
    const swapDeckSelectEl = root.querySelector('#engineSwapDeckSelect');
    const autoLoadCreateEl = root.querySelector('#engineAutoLoadCreate');
    const autoLoadJoinEl = root.querySelector('#engineAutoLoadJoin');
    const shuffleEl = root.querySelector('#engineShuffle');

    const statusEl = root.querySelector('#engineGameStatus');
    const metaEl = root.querySelector('#engineGameMeta');
    const playersListEl = root.querySelector('#enginePlayersList');
    const configStatusEl = root.querySelector('#engineConfigStatus');

    let currentGameId = '';
    let engineReady = engineEnabled;
    let deckOptionsLoaded = root.dataset.deckOptionsLoaded === 'true';
    const openTableBase = openTableBtn ? (openTableBtn.getAttribute('href') || '') : '/games/engine/play';
    const storageKey = 'dvEngineLastLobbyId';

    function setControlsEnabled(enabled) {
      [createBtn, joinBtn, refreshBtn, startBtn, loadDeckBtn].forEach((btn) => {
        if (btn) btn.disabled = !enabled;
      });
      [createDeckSelectEl, joinDeckSelectEl, swapDeckSelectEl].forEach((selectEl) => {
        if (selectEl) selectEl.disabled = !enabled && !(selectEl.options && selectEl.options.length > 1);
      });
    }

    function setConfigStatus(message, tone) {
      if (!configStatusEl) return;
      const toneClass = tone ? ` ${tone}` : '';
      configStatusEl.textContent = message;
      configStatusEl.className = `engine-pill${toneClass}`.trim();
    }

    function setStatus(message, kind) {
      if (!statusEl) return;
      statusEl.textContent = message;
      statusEl.className = `engine-status ${kind || ''}`.trim();
    }

    function getStoredGameId() {
      try {
        return (window.localStorage && localStorage.getItem(storageKey)) || '';
      } catch {
        return '';
      }
    }

    function storeGameId(value) {
      if (!value) return;
      try {
        if (window.localStorage) {
          localStorage.setItem(storageKey, String(value));
        }
      } catch {
        // ignore storage failures
      }
    }

    function hydrateStoredGameId() {
      if (currentGameId) return;
      if (gameIdEl && gameIdEl.value.trim()) return;
      const stored = getStoredGameId();
      if (!stored) return;
      if (gameIdEl) gameIdEl.value = stored;
      if (inviteIdEl) inviteIdEl.value = stored;
      currentGameId = stored;
      updateTableLink(stored);
    }

    function updateTableLink(gameId) {
      if (!openTableBtn) return;
      if (!gameId) {
        openTableBtn.classList.add('disabled');
        openTableBtn.setAttribute('aria-disabled', 'true');
        if (openTableBtn.dataset.baseHref) {
          openTableBtn.setAttribute('href', openTableBtn.dataset.baseHref);
        } else if (openTableBase) {
          openTableBtn.dataset.baseHref = openTableBase;
          openTableBtn.setAttribute('href', openTableBase);
        }
        return;
      }
      const baseHref = openTableBtn.dataset.baseHref || openTableBase || '/games/engine/play';
      const url = new URL(baseHref, window.location.origin);
      url.searchParams.set('game_id', gameId);
      openTableBtn.setAttribute('href', `${url.pathname}${url.search}`);
      openTableBtn.classList.remove('disabled');
      openTableBtn.setAttribute('aria-disabled', 'false');
    }

    function renderPlayers(players) {
      if (!playersListEl) return;
      playersListEl.innerHTML = '';
      if (!players || !players.length) {
        const empty = document.createElement('div');
        empty.className = 'text-muted small';
        empty.textContent = 'No players yet.';
        playersListEl.appendChild(empty);
        return;
      }
      players.forEach((player) => {
        const item = document.createElement('div');
        item.className = 'list-group-item bg-transparent border-secondary text-light d-flex align-items-center justify-content-between';
        const left = document.createElement('div');
        left.innerHTML = `<strong>Player ${player.user_id}</strong> <span class="text-muted small">Seat ${player.seat_index}</span>`;
        const right = document.createElement('div');
        right.className = 'text-muted small';
        right.textContent = player.deck_ref ? `Deck: ${player.deck_ref}` : (player.status || 'active');
        item.appendChild(left);
        item.appendChild(right);
        playersListEl.appendChild(item);
      });
    }

    function populateSelect(selectEl, options, placeholderLabel) {
      if (!selectEl) return;
      const currentValue = selectEl.value;
      selectEl.innerHTML = '';
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = options && options.length ? placeholderLabel : 'No commander decks found';
      selectEl.appendChild(placeholder);
      (options || []).forEach((deck) => {
        const opt = document.createElement('option');
        opt.value = String(deck.id || '');
        opt.textContent = deck.label || `Deck ${deck.id}`;
        selectEl.appendChild(opt);
      });
      if (currentValue && options && options.some((deck) => String(deck.id) === String(currentValue))) {
        selectEl.value = currentValue;
      }
      selectEl.disabled = !(options && options.length);
    }

    function renderDeckOptions(options) {
      populateSelect(createDeckSelectEl, options, 'Select a deck');
      populateSelect(joinDeckSelectEl, options, 'Select a deck');
      populateSelect(swapDeckSelectEl, options, 'Select a deck');
    }

    async function loadDeckOptions(force = false) {
      if (!deckOptionsUrl) return;
      if (deckOptionsLoaded && !force) return;
      try {
        const result = await api(deckOptionsUrl, { method: 'GET' });
        renderDeckOptions(result.options || []);
        deckOptionsLoaded = true;
      } catch (err) {
        if (!deckOptionsLoaded) {
          renderDeckOptions([]);
        }
        setStatus('Unable to load decks. Try refreshing the page.', 'text-warning');
      }
    }

    function renderGame(result) {
      if (!result) return;
      const players = result.players || [];
      if (result.game && result.game.id) {
        currentGameId = result.game.id;
        storeGameId(currentGameId);
        if (gameIdEl) gameIdEl.value = currentGameId;
        if (inviteIdEl) inviteIdEl.value = currentGameId;
        updateTableLink(currentGameId);
      }
      if (metaEl) {
        metaEl.innerHTML = '';
        if (result.game) {
          metaEl.innerHTML = `
            <div>Lobby ID: <code>${result.game.id}</code></div>
            <div>Status: ${result.game.status}</div>
            <div>Format: ${result.game.format}</div>
          `;
        }
      }
      renderPlayers(players);
      if (result.game) {
        setStatus(`Loaded lobby ${result.game.id}.`, 'text-success');
        if (startBtn) {
          startBtn.disabled = result.game.status !== 'waiting';
        }
      }
    }

    async function api(path, options) {
      const headers = Object.assign(
        { 'Content-Type': 'application/json' },
        (window.csrfHeader || {})
      );
      const response = await fetch(path, Object.assign({ headers }, options || {}));
      const raw = await response.text();
      let payload = null;
      try {
        payload = raw ? JSON.parse(raw) : null;
      } catch (err) {
        payload = null;
      }
      if (!response.ok || !payload || !payload.ok) {
        const statusLabel = `HTTP ${response.status}`;
        let error = (payload && payload.error) ? payload.error : '';
        if (!error) {
          const snippet = (raw || '').replace(/\s+/g, ' ').trim();
          if (snippet && !snippet.startsWith('<')) {
            error = `${statusLabel}: ${snippet.slice(0, 200)}`;
          } else {
            error = statusLabel;
          }
        }
        throw new Error(error);
      }
      return payload.result;
    }

    async function checkEngine() {
      if (!engineEnabled || !pingUrl) return false;
      try {
        await api(pingUrl, { method: 'GET' });
        engineReady = true;
        setControlsEnabled(true);
        setConfigStatus('Engine connected', 'is-good');
        return true;
      } catch (err) {
        engineReady = false;
        setControlsEnabled(false);
        setConfigStatus('Engine unavailable', 'is-warn');
        setStatus(err.message || 'Engine unavailable.', 'text-danger');
        return false;
      }
    }

    async function ensureEngineReady() {
      if (!engineEnabled) return false;
      if (engineReady) return true;
      return checkEngine();
    }

    async function loadDeckForGame(gameId, folderId) {
      if (!gameId || !folderId) return false;
      setStatus('Syncing deck...');
      const deckResult = await api('/api/game-engine/decks/from-folder', {
        method: 'POST',
        body: JSON.stringify({ folder_id: Number(folderId) })
      });
      const deckId =
        (deckResult.deck && (deckResult.deck.id || deckResult.deck.deck_id)) ||
        deckResult.id ||
        deckResult.deck_id;
      if (!deckId) {
        throw new Error('Deck sync failed.');
      }
      setStatus('Loading deck...');
      await api(`/api/game-engine/games/${gameId}/actions`, {
        method: 'POST',
        body: JSON.stringify({
          action_type: 'load_deck',
          payload: { deck_id: deckId, shuffle: shuffleEl ? shuffleEl.checked : true }
        })
      });
      return true;
    }

    async function createGame() {
      if (!(await ensureEngineReady())) return;
      setStatus('Creating lobby...');
      const format = formatEl ? formatEl.value : 'commander';
      const deckId = createDeckSelectEl ? createDeckSelectEl.value : '';
      try {
        const result = await api('/api/game-engine/games', {
          method: 'POST',
          body: JSON.stringify({ format })
        });
        currentGameId = result.game_id || '';
        storeGameId(currentGameId);
        if (gameIdEl && currentGameId) gameIdEl.value = currentGameId;
        if (inviteIdEl && currentGameId) inviteIdEl.value = currentGameId;
        updateTableLink(currentGameId);
        if (autoLoadCreateEl && autoLoadCreateEl.checked && deckId) {
          await loadDeckForGame(currentGameId, deckId);
        }
        await refreshGame();
      } catch (err) {
        setStatus(err.message, 'text-danger');
      }
    }

    async function joinGame() {
      if (!(await ensureEngineReady())) return;
      const gameId = (gameIdEl && gameIdEl.value || '').trim();
      const deckId = joinDeckSelectEl ? joinDeckSelectEl.value : '';
      if (!gameId) {
        setStatus('Enter a lobby ID to join.', 'text-warning');
        return;
      }
      setStatus('Joining lobby...');
      try {
        await api(`/api/game-engine/games/${gameId}/join`, { method: 'POST' });
        currentGameId = gameId;
        storeGameId(currentGameId);
        if (inviteIdEl) inviteIdEl.value = gameId;
        updateTableLink(currentGameId);
        if (autoLoadJoinEl && autoLoadJoinEl.checked && deckId) {
          await loadDeckForGame(currentGameId, deckId);
        }
        await refreshGame();
      } catch (err) {
        setStatus(err.message, 'text-danger');
      }
    }

    async function refreshGame() {
      if (!(await ensureEngineReady())) return;
      const gameId = currentGameId || (gameIdEl && gameIdEl.value || '').trim();
      if (!gameId) {
        setStatus('No lobby loaded yet.');
        return;
      }
      setStatus('Loading lobby...');
      try {
        const result = await api(`/api/game-engine/games/${gameId}`, { method: 'GET' });
        renderGame(result);
      } catch (err) {
        setStatus(err.message, 'text-danger');
      }
    }

    async function startGame() {
      if (!(await ensureEngineReady())) return;
      const gameId = currentGameId || (gameIdEl && gameIdEl.value || '').trim();
      if (!gameId) {
        setStatus('No lobby loaded yet.');
        return;
      }
      setStatus('Starting game...');
      try {
        await api(`/api/game-engine/games/${gameId}/actions`, {
          method: 'POST',
          body: JSON.stringify({ action_type: 'start_game', payload: {} })
        });
        await refreshGame();
      } catch (err) {
        setStatus(err.message, 'text-danger');
      }
    }

    async function loadDeck() {
      if (!(await ensureEngineReady())) return;
      const gameId = currentGameId || (gameIdEl && gameIdEl.value || '').trim();
      const folderId = swapDeckSelectEl && swapDeckSelectEl.value;
      if (!gameId || !folderId) {
        setStatus('Select a commander deck and provide a lobby ID.', 'text-warning');
        return;
      }
      try {
        await loadDeckForGame(gameId, folderId);
        await refreshGame();
      } catch (err) {
        setStatus(err.message, 'text-danger');
      }
    }

    async function copyGameId() {
      if (!currentGameId && inviteIdEl && inviteIdEl.value) {
        currentGameId = inviteIdEl.value.trim();
      }
      if (!currentGameId) {
        setStatus('Create or join a lobby to copy its ID.', 'text-warning');
        return;
      }
      try {
        await navigator.clipboard.writeText(currentGameId);
        setStatus('Lobby ID copied to clipboard.', 'text-success');
      } catch (err) {
        if (inviteIdEl) {
          inviteIdEl.removeAttribute('readonly');
          inviteIdEl.select();
          inviteIdEl.setSelectionRange(0, inviteIdEl.value.length);
          inviteIdEl.setAttribute('readonly', 'readonly');
        }
        setStatus('Select the lobby ID and copy it.', 'text-warning');
      }
    }

    if (createBtn) {
      createBtn.addEventListener('click', async () => {
        await createGame();
        if (currentGameId) {
          window.location.assign(`${openTableBase}?game_id=${encodeURIComponent(currentGameId)}`);
        }
      });
    }
    if (joinBtn) {
      joinBtn.addEventListener('click', async () => {
        await joinGame();
        if (currentGameId) {
          window.location.assign(`${openTableBase}?game_id=${encodeURIComponent(currentGameId)}`);
        }
      });
    }
    if (refreshBtn) refreshBtn.addEventListener('click', refreshGame);
    if (startBtn) startBtn.addEventListener('click', startGame);
    if (loadDeckBtn) loadDeckBtn.addEventListener('click', loadDeck);
    if (copyBtn) copyBtn.addEventListener('click', copyGameId);

    if (gameIdEl) {
      gameIdEl.addEventListener('input', () => {
        const value = (gameIdEl.value || '').trim();
        if (value) {
          currentGameId = value;
          updateTableLink(currentGameId);
        }
      });
    }

    setControlsEnabled(engineEnabled);
    loadDeckOptions();
    if (!engineEnabled) {
      setConfigStatus('Engine not configured', 'is-warn');
      setStatus('Engine not configured.', 'text-warning');
      return;
    }

    checkEngine().then(() => {
      if (engineReady) {
        hydrateStoredGameId();
        refreshGame();
      }
    });
  }

  function boot() {
    initGameEngineApp();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }

  document.addEventListener('htmx:afterSwap', (event) => {
    if (event.target && event.target.id === 'main') {
      boot();
    }
  });
  document.addEventListener('htmx:load', boot);
})();
