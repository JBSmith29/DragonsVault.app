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
    const userId = parseInt(root.dataset.userId || '0', 10);

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

    function normalizeErrorMessage(raw) {
      const text = (raw || '').toString().trim();
      if (!text) return 'Unexpected game engine error.';
      const friendly = {
        game_not_started: 'Game has not started yet.',
        mulligan_in_progress: 'Finish mulligans before taking more actions.',
        choice_pending: 'Resolve the pending choice before continuing.',
        priority_required: 'You need priority to do that action.',
        not_active_player: 'Only the active player can do that action right now.',
        not_main_phase: 'That action is only available in a main phase.',
        stack_not_empty: 'Resolve the stack first.',
        stack_empty: 'The stack is currently empty.',
        defender_required: 'Select a defender first.',
        commander_min_players_required: 'Commander needs at least 2 players to start.',
        commander_max_players_exceeded: 'Commander supports up to 4 players.',
        commander_lobby_full: 'This Commander lobby is full (4 players max).',
      };
      if (friendly[text]) return friendly[text];
      return text.replace(/_/g, ' ');
    }

    async function runWithBusy(button, busyLabel, callback) {
      if (!button) return callback();
      if (button.dataset.busy === 'true') return false;
      const previous = button.textContent;
      button.dataset.busy = 'true';
      button.disabled = true;
      if (busyLabel) button.textContent = busyLabel;
      try {
        return await callback();
      } finally {
        button.dataset.busy = 'false';
        button.textContent = previous;
        setControlsEnabled(engineEnabled && engineReady);
      }
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

      const displayNameFor = (player) => {
        const label = (player && (player.display_name || player.username || player.name || player.label)) || '';
        if (label) return label;
        const numericId = Number(player && player.user_id);
        if (numericId && userId && numericId === userId) return 'You';
        return `Player ${player && player.user_id != null ? player.user_id : 'Unknown'}`;
      };

      players.forEach((player) => {
        const item = document.createElement('div');
        item.className = 'list-group-item bg-transparent border-secondary text-light d-flex align-items-center justify-content-between';

        const left = document.createElement('div');
        const name = document.createElement('strong');
        name.textContent = displayNameFor(player);
        const seat = document.createElement('span');
        seat.className = 'text-muted small';
        seat.textContent = `Seat ${player.seat_index}`;
        left.appendChild(name);
        left.appendChild(document.createTextNode(' '));
        left.appendChild(seat);

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
        const gameFormat = String(result.game.format || '').toLowerCase();
        const isCommander = gameFormat === 'commander';
        const playerCount = Array.isArray(players) ? players.length : 0;
        const commanderStartReady = !isCommander || (playerCount >= 2 && playerCount <= 4);
        if (result.game.status === 'waiting' && isCommander && !commanderStartReady) {
          if (playerCount < 2) {
            setStatus('Commander games need 2-4 players to start. Invite at least one more player.', 'text-warning');
          } else {
            setStatus('Commander lobbies support up to 4 players. Remove one player before starting.', 'text-warning');
          }
        } else {
          setStatus(`Loaded lobby ${result.game.id}.`, 'text-success');
        }
        if (startBtn) {
          startBtn.disabled = result.game.status !== 'waiting' || !commanderStartReady;
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
        setStatus(normalizeErrorMessage(err.message || 'Engine unavailable.'), 'text-danger');
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
      if (!(await ensureEngineReady())) return false;
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
        return Boolean(currentGameId);
      } catch (err) {
        setStatus(normalizeErrorMessage(err.message), 'text-danger');
        return false;
      }
    }

    async function joinGame() {
      if (!(await ensureEngineReady())) return false;
      const gameId = (gameIdEl && gameIdEl.value || '').trim();
      const deckId = joinDeckSelectEl ? joinDeckSelectEl.value : '';
      if (!gameId) {
        setStatus('Enter a lobby ID to join.', 'text-warning');
        return false;
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
        return Boolean(currentGameId);
      } catch (err) {
        setStatus(normalizeErrorMessage(err.message), 'text-danger');
        return false;
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
        setStatus(normalizeErrorMessage(err.message), 'text-danger');
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
        setStatus(normalizeErrorMessage(err.message), 'text-danger');
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
        setStatus(normalizeErrorMessage(err.message), 'text-danger');
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
        const created = await runWithBusy(createBtn, 'Creating...', createGame);
        if (created && currentGameId) {
          window.location.assign(`${openTableBase}?game_id=${encodeURIComponent(currentGameId)}`);
        }
      });
    }
    if (joinBtn) {
      joinBtn.addEventListener('click', async () => {
        const joined = await runWithBusy(joinBtn, 'Joining...', joinGame);
        if (joined && currentGameId) {
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
      gameIdEl.addEventListener('keydown', async (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        if (!joinBtn) {
          await joinGame();
          return;
        }
        const joined = await runWithBusy(joinBtn, 'Joining...', joinGame);
        if (joined && currentGameId) {
          window.location.assign(`${openTableBase}?game_id=${encodeURIComponent(currentGameId)}`);
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
