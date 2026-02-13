(function () {
  function initGameEngineTable() {
    const root = document.getElementById('gameEngineTable');
    if (!root || root.dataset.bound === 'true') return;
    root.dataset.bound = 'true';

    const engineEnabled = root.dataset.engineEnabled === 'true';
    const gameId = (root.dataset.gameId || '').trim();
    const userId = parseInt(root.dataset.userId || '0', 10);
    const gameUrlTemplate = root.dataset.gameUrl || '';
    const actionsUrlTemplate = root.dataset.actionsUrl || '';
    const eventsUrlTemplate = root.dataset.eventsUrl || '';
    const imagesUrl = root.dataset.imagesUrl || '';
    const storageKey = 'dvEngineLastLobbyId';

    const subtitleEl = root.querySelector('#engineTableSubtitle');
    const refreshBtn = root.querySelector('#engineTableRefresh');
    const statusEl = root.querySelector('#engineStatus');

    const opponentsEl = root.querySelector('#engineOpponents');
    const battlefieldEl = root.querySelector('#engineBattlefield');
    const handEl = root.querySelector('#engineHand');
    const zonesEl = root.querySelector('#engineZones');
    const battlefieldFocusEl = root.querySelector('#engineBattlefieldFocus');
    const selectionCountEl = root.querySelector('#engineSelectionCount');
    const boardFollowBtn = root.querySelector('#engineBoardFollowBtn');
    const boardMineBtn = root.querySelector('#engineBoardMineBtn');
    const boardActiveBtn = root.querySelector('#engineBoardActiveBtn');
    const turnPillsEl = root.querySelector('#engineTurnPills');
    const turnMetaEl = root.querySelector('#engineTurnMeta');
    const lastEventEl = root.querySelector('#engineLastEvent');
    const playerSummaryEl = root.querySelector('#enginePlayerSummary');
    const lifeBoardEl = root.querySelector('#engineLifeBoard');
    const commanderSourceSelect = root.querySelector('#engineCommanderSource');
    const cardDetailNameEl = root.querySelector('#engineCardDetailName');
    const cardDetailTypeEl = root.querySelector('#engineCardDetailType');
    const cardDetailTextEl = root.querySelector('#engineCardDetailText');
    const cardDetailMetaEl = root.querySelector('#engineCardDetailMeta');

    const drawBtn = root.querySelector('#engineDrawBtn');
    const passBtn = root.querySelector('#enginePassBtn');
    const resolveBtn = root.querySelector('#engineResolveBtn');
    const clearSelectionBtn = root.querySelector('#engineClearSelectionBtn');
    const playLandBtn = root.querySelector('#enginePlayLandBtn');
    const castSpellBtn = root.querySelector('#engineCastSpellBtn');
    const commanderBtn = root.querySelector('#engineCommanderBtn');
    const startBtn = root.querySelector('#engineStartBtn');
    const mulliganBtn = root.querySelector('#engineMulliganBtn');
    const keepBtn = root.querySelector('#engineKeepBtn');

    const defenderSelectEl = root.querySelector('#engineDefenderSelect');
    const declareAttackersBtn = root.querySelector('#engineDeclareAttackersBtn');
    const declareBlockersBtn = root.querySelector('#engineDeclareBlockersBtn');
    const combatDamageBtn = root.querySelector('#engineCombatDamageBtn');
    const blockerAssignmentsEl = root.querySelector('#engineBlockerAssignments');

    const stackEl = root.querySelector('#engineStack');
    const choicesEl = root.querySelector('#engineChoices');
    const eventsEl = root.querySelector('#engineEvents');
    const eventsBadgeEl = root.querySelector('#engineEventsBadge');
    const eventsFilterEl = root.querySelector('#engineEventsFilter');
    const missingGameInput = root.querySelector('#engineMissingGameId');
    const missingOpenBtn = root.querySelector('#engineMissingOpenBtn');
    const missingUseLastBtn = root.querySelector('#engineMissingUseLastBtn');

    let state = null;
    let selectedHandId = null;
    let selectedBattlefield = new Set();
    let selectedCardId = null;
    let cardLookup = new Map();
    let imageLookup = new Map();
    let pendingImageIds = new Set();
    let imageRequest = null;
    let lastGameResult = null;
    let lastEventSeq = 0;
    let eventFilter = 'all';
    let pollingTimer = null;
    let lastTurnKey = null;
    let seatLookup = new Map();
    let boardFocusMode = 'auto';
    let manualBoardFocusId = userId;
    let selectedCommanderSourceId = userId;
    let lifeInputValues = new Map();

    function templateUrl(tmpl) {
      return (tmpl || '').replace('__GAME__', encodeURIComponent(gameId));
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

    function redirectToGame(id) {
      if (!id) return;
      const url = new URL(window.location.href);
      url.searchParams.set('game_id', id);
      window.location.assign(url.toString());
    }

    function api(path, options) {
      const headers = Object.assign(
        { 'Content-Type': 'application/json' },
        (window.csrfHeader || {})
      );
      return fetch(path, Object.assign({ headers }, options || {}))
        .then(async (response) => {
          const raw = await response.text();
          let payload = null;
          try {
            payload = raw ? JSON.parse(raw) : null;
          } catch {
            payload = null;
          }
          if (!response.ok || !payload || !payload.ok) {
            const statusLabel = `HTTP ${response.status}`;
            const error = (payload && payload.error) ? payload.error : statusLabel;
            throw new Error(error);
          }
          return payload.result;
        });
    }

    function setStatus(message, kind) {
      if (!statusEl) return;
      statusEl.textContent = message;
      const tone = kind || 'text-muted';
      statusEl.className = `status-banner ${tone}`.trim();
    }

    function clearChildren(node) {
      if (!node) return;
      node.innerHTML = '';
    }

    function cardLabel(card) {
      if (!card) return 'Card';
      const name = card.name || 'Card';
      const typeLine = card.type_line || '';
      return `${name}${typeLine ? ` (${typeLine})` : ''}`;
    }

    function buildSeatLookup(playersMeta) {
      seatLookup = new Map();
      (playersMeta || []).forEach((player) => {
        if (player && player.user_id != null) {
          seatLookup.set(player.user_id, player.seat_index);
        }
      });
    }

    function seatLabel(userIdValue) {
      if (userIdValue == null) return '—';
      const seat = seatLookup.get(Number(userIdValue));
      if (seat != null) {
        return `Seat ${seat}`;
      }
      return `Player ${userIdValue}`;
    }

    function sortPlayersBySeat(playersList) {
      return (playersList || []).slice().sort((left, right) => {
        const leftSeat = seatLookup.get(Number(left?.user_id));
        const rightSeat = seatLookup.get(Number(right?.user_id));
        if (leftSeat != null && rightSeat != null) {
          return leftSeat - rightSeat;
        }
        if (leftSeat != null) return -1;
        if (rightSeat != null) return 1;
        return (Number(left?.user_id) || 0) - (Number(right?.user_id) || 0);
      });
    }

    function formatCommanderDamage(player) {
      if (!player) return '—';
      const dmg = player.commander_damage || {};
      const entries = Object.entries(dmg).filter(([, amount]) => (amount || 0) > 0);
      if (!entries.length) return '—';
      return entries
        .map(([sourceId, amount]) => `${seatLabel(sourceId)} ${amount}`)
        .join(' · ');
    }

    function buildCardLookup(stateObj) {
      cardLookup = new Map();
      const players = stateObj?.players || [];
      players.forEach((player) => {
        const zones = player.zones || {};
        Object.values(zones).forEach((cards) => {
          (cards || []).forEach((card) => {
            if (card && card.instance_id) {
              cardLookup.set(card.instance_id, card);
            }
          });
        });
      });
    }

    function collectOracleIds(stateObj) {
      const ids = new Set();
      const players = stateObj?.players || [];
      players.forEach((player) => {
        const zones = player.zones || {};
        Object.values(zones).forEach((cards) => {
          (cards || []).forEach((card) => {
            if (card && card.oracle_id) {
              ids.add(String(card.oracle_id));
            }
          });
        });
      });
      return Array.from(ids);
    }

    async function fetchImagesFor(ids) {
      if (!imagesUrl || !ids.length) return;
      try {
        const result = await api(imagesUrl, {
          method: 'POST',
          body: JSON.stringify({ oracle_ids: ids })
        });
        const images = result.images || {};
        Object.keys(images).forEach((oid) => {
          imageLookup.set(String(oid), images[oid]);
        });
      } catch (err) {
        // ignore image failures
      }
    }

    async function queueImageFetch(stateObj) {
      if (!imagesUrl) return;
      const oracleIds = collectOracleIds(stateObj);
      oracleIds.forEach((oid) => {
        if (!imageLookup.has(oid)) {
          pendingImageIds.add(oid);
        }
      });
      if (!pendingImageIds.size || imageRequest) return;
      const ids = Array.from(pendingImageIds);
      pendingImageIds = new Set();
      imageRequest = fetchImagesFor(ids)
        .then(() => {
          if (lastGameResult) {
            renderState(lastGameResult);
          }
        })
        .finally(() => {
          imageRequest = null;
        });
    }

    function formatCardStats(card) {
      if (!card) return '';
      if (card.power && card.toughness) return `${card.power}/${card.toughness}`;
      if (card.loyalty) return `Loyalty ${card.loyalty}`;
      if (card.defense) return `Defense ${card.defense}`;
      return '';
    }

    function formatCardStatus(card) {
      const parts = [];
      if (card?.tapped) parts.push('Tapped');
      if (card?.summoning_sick) parts.push('Summoning');
      return parts.join(' · ');
    }

    function cardImageUrl(card) {
      if (!card) return '';
      const fallback = window.CARD_BACK_PLACEHOLDER || '';
      const oracleId = card.oracle_id ? String(card.oracle_id) : '';
      const images = oracleId ? imageLookup.get(oracleId) : null;
      if (!images) return fallback;
      return images.normal || images.large || images.small || fallback;
    }

    function createCardButton(card, options) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'table-card';
      item.dataset.instanceId = card.instance_id;
      if (card.oracle_id) {
        item.dataset.oracleId = card.oracle_id;
      }
      if (options?.selected) {
        item.classList.add('is-selected');
      }
      if (card.tapped) {
        item.classList.add('is-tapped');
      }
      const cost = card.mana_cost || '';
      const stats = formatCardStats(card);
      const status = formatCardStatus(card);
      const imgUrl = cardImageUrl(card);
      if (imgUrl) {
        item.classList.add('has-art');
        item.innerHTML = `
          <img class="card-art" src="${imgUrl}" alt="${card.name || 'Card'}" loading="lazy" decoding="async">
          <div class="card-overlay">
            <div class="card-head">
              <div class="card-title">${card.name || 'Card'}</div>
              ${cost ? `<div class="card-cost">${cost}</div>` : ''}
            </div>
            <div class="card-type">${card.type_line || ''}</div>
            ${stats ? `<div class="card-meta">${stats}</div>` : ''}
            ${status ? `<div class="card-status">${status}</div>` : ''}
          </div>
        `;
      } else {
        item.innerHTML = `
          <div class="card-head">
            <div class="card-title">${card.name || 'Card'}</div>
            ${cost ? `<div class="card-cost">${cost}</div>` : ''}
          </div>
          <div class="card-type">${card.type_line || ''}</div>
          ${stats ? `<div class="card-meta">${stats}</div>` : ''}
          ${status ? `<div class="card-status">${status}</div>` : ''}
        `;
      }
      if (card.is_commander) {
        item.classList.add('is-commander');
        const badge = document.createElement('div');
        badge.className = 'commander-flag';
        badge.textContent = 'Commander';
        item.appendChild(badge);
      }
      if (options?.onClick) {
        item.addEventListener('click', options.onClick);
      }
      return item;
    }

    function renderPlayerSummary(player) {
      if (!playerSummaryEl) return;
      if (!player) {
        playerSummaryEl.innerHTML = '<div class="text-muted small">Join a lobby to see your seat.</div>';
        return;
      }
      const zones = player.zones || {};
      const handCount = (zones.hand || []).length;
      const libraryCount = (zones.library || []).length;
      const graveyardCount = (zones.graveyard || []).length;
      const commandCount = (zones.command || []).length;
      const cmdSummary = formatCommanderDamage(player);
      const seatValue = seatLookup.get(player.user_id);
      playerSummaryEl.innerHTML = `
        <div class="seat-row">
          <div class="seat-stat"><span>Life</span><strong>${player.life ?? 0}</strong></div>
          <div class="seat-stat"><span>Hand</span><strong>${handCount}</strong></div>
          <div class="seat-stat"><span>Library</span><strong>${libraryCount}</strong></div>
        </div>
        <div class="seat-row">
          <div class="seat-stat"><span>Graveyard</span><strong>${graveyardCount}</strong></div>
          <div class="seat-stat"><span>Command</span><strong>${commandCount}</strong></div>
          <div class="seat-stat"><span>Seat</span><strong>${seatValue ?? '—'}</strong></div>
        </div>
        <div class="seat-row">
          <div class="seat-stat is-wide"><span>Commander Damage</span><strong>${cmdSummary}</strong></div>
        </div>
      `;
    }

    function renderCardDetail(card) {
      if (!cardDetailNameEl || !cardDetailTypeEl || !cardDetailTextEl || !cardDetailMetaEl) return;
      if (!card) {
        cardDetailNameEl.textContent = 'Select a card';
        cardDetailTypeEl.textContent = '';
        cardDetailTextEl.textContent = '';
        cardDetailMetaEl.textContent = '';
        return;
      }
      cardDetailNameEl.textContent = card.name || 'Card';
      cardDetailTypeEl.textContent = card.type_line || '';
      cardDetailTextEl.textContent = card.oracle_text || '';
      const metaParts = [];
      if (card.mana_cost) metaParts.push(card.mana_cost);
      if (card.colors && card.colors.length) metaParts.push(`Colors: ${card.colors.join(', ')}`);
      if (card.color_identity && card.color_identity.length) metaParts.push(`CI: ${card.color_identity.join(', ')}`);
      const stats = formatCardStats(card);
      if (stats) metaParts.push(stats);
      if (card.is_commander) metaParts.push('Commander');
      cardDetailMetaEl.textContent = metaParts.join(' · ');
    }

    function resolveBoardFocus(stateObj, fallbackPlayer) {
      const activeId = stateObj?.turn?.active_player;
      let focusId = boardFocusMode === 'auto' ? activeId : manualBoardFocusId;
      if (!focusId) {
        focusId = fallbackPlayer?.user_id || userId;
      }
      const focusPlayer = (stateObj?.players || []).find((p) => p.user_id === focusId) || fallbackPlayer || null;
      if (battlefieldFocusEl) {
        const label = focusPlayer ? seatLabel(focusPlayer.user_id) : '—';
        const isActiveFocus = activeId && focusPlayer && focusPlayer.user_id === activeId;
        let suffix = '';
        if (isActiveFocus) {
          suffix = ' (Active)';
        } else if (boardFocusMode === 'manual') {
          suffix = ' (Pinned)';
        }
        battlefieldFocusEl.textContent = `Showing: ${label}${suffix}`;
      }
      if (boardFollowBtn) {
        if (boardFocusMode === 'auto' && activeId != null) {
          boardFollowBtn.textContent = `Following ${seatLabel(activeId)}`;
        } else if (boardFocusMode === 'auto') {
          boardFollowBtn.textContent = 'Following Turn';
        } else {
          boardFollowBtn.textContent = 'Follow Turn';
        }
      }
      return focusPlayer;
    }

    function adjustLife(targetId, delta) {
      submitAction('adjust_life', { target_id: targetId, delta });
    }

    function renderLifeBoard(players, activeId, priorityId, focusId) {
      clearChildren(lifeBoardEl);
      if (!lifeBoardEl) return;
      if (!players || !players.length) {
        const empty = document.createElement('div');
        empty.className = 'text-muted small';
        empty.textContent = 'No players yet.';
        lifeBoardEl.appendChild(empty);
        return;
      }
      players.forEach((player) => {
        if (!lifeInputValues.has(player.user_id)) {
          lifeInputValues.set(player.user_id, '');
        }
        const row = document.createElement('div');
        row.className = 'life-row';
        if (player.user_id === activeId) row.classList.add('is-active');
        if (player.user_id === priorityId) row.classList.add('is-priority');
        if (player.user_id === focusId) row.classList.add('is-viewing');

        const header = document.createElement('div');
        header.className = 'life-main';
        const name = document.createElement('div');
        name.className = 'life-name';
        name.textContent = player.user_id === userId
          ? `${seatLabel(player.user_id)} (You)`
          : seatLabel(player.user_id);
        const total = document.createElement('div');
        total.className = 'life-total';
        total.textContent = String(player.life ?? 0);
        header.appendChild(name);
        header.appendChild(total);
        row.appendChild(header);

        const controls = document.createElement('div');
        controls.className = 'life-controls';
        [-5, -1, 1, 5].forEach((delta) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-outline-secondary btn-sm';
          btn.textContent = delta > 0 ? `+${delta}` : `${delta}`;
          btn.addEventListener('click', (event) => {
            event.stopPropagation();
            adjustLife(player.user_id, delta);
          });
          controls.appendChild(btn);
        });
        row.appendChild(controls);

        const presets = document.createElement('div');
        presets.className = 'life-presets';
        [20, 30, 40].forEach((preset) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-outline-secondary btn-sm';
          btn.textContent = `${preset}`;
          btn.addEventListener('click', (event) => {
            event.stopPropagation();
            submitAction('adjust_life', { target_id: player.user_id, life: preset });
            lifeInputValues.set(player.user_id, String(preset));
          });
          presets.appendChild(btn);
        });
        row.appendChild(presets);

        const setWrap = document.createElement('div');
        setWrap.className = 'life-set';
        const input = document.createElement('input');
        input.type = 'number';
        input.inputMode = 'numeric';
        input.className = 'form-control form-control-sm';
        input.placeholder = 'Set life';
        const storedValue = lifeInputValues.get(player.user_id);
        input.value = storedValue != null ? storedValue : '';
        input.addEventListener('click', (event) => event.stopPropagation());
        input.addEventListener('input', (event) => {
          lifeInputValues.set(player.user_id, event.target.value);
        });
        input.addEventListener('keydown', (event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            applyLifeSet(player.user_id, input.value);
          }
        });
        setWrap.appendChild(input);

        const setBtn = document.createElement('button');
        setBtn.type = 'button';
        setBtn.className = 'btn btn-outline-secondary btn-sm';
        setBtn.textContent = 'Set';
        setBtn.addEventListener('click', (event) => {
          event.stopPropagation();
          applyLifeSet(player.user_id, input.value);
        });
        setWrap.appendChild(setBtn);
        row.appendChild(setWrap);

        const commander = document.createElement('div');
        commander.className = 'life-commander';

        const summary = document.createElement('div');
        summary.className = 'life-commander-summary';
        summary.textContent = `Commander damage: ${formatCommanderDamage(player)}`;
        commander.appendChild(summary);

        const list = document.createElement('div');
        list.className = 'life-commander-list';
        const entries = Object.entries(player.commander_damage || {}).filter(([, amount]) => (amount || 0) > 0);
        if (!entries.length) {
          const chip = document.createElement('span');
          chip.className = 'commander-chip';
          chip.textContent = 'None';
          list.appendChild(chip);
        } else {
          entries.forEach(([sourceId, amount]) => {
            const chip = document.createElement('span');
            chip.className = 'commander-chip';
            chip.innerHTML = `${seatLabel(sourceId)} <strong>${amount}</strong>`;
            list.appendChild(chip);
          });
        }
        commander.appendChild(list);

        const controlsRow = document.createElement('div');
        controlsRow.className = 'life-commander-controls';
        const sourceLabel = selectedCommanderSourceId != null ? seatLabel(selectedCommanderSourceId) : 'Select source';
        const sourceText = document.createElement('span');
        sourceText.className = 'life-commander-source';
        sourceText.textContent = `From ${sourceLabel}`;
        controlsRow.appendChild(sourceText);
        [-5, -1, 1, 5].forEach((delta) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-outline-secondary btn-sm';
          btn.textContent = delta > 0 ? `+${delta}` : `${delta}`;
          btn.disabled = selectedCommanderSourceId == null;
          btn.addEventListener('click', (event) => {
            event.stopPropagation();
            if (selectedCommanderSourceId == null) {
              setStatus('Select a commander source first.', 'text-warning');
              return;
            }
            submitAction('adjust_commander_damage', {
              target_id: player.user_id,
              source_id: selectedCommanderSourceId,
              delta,
            });
          });
          controlsRow.appendChild(btn);
        });
        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.className = 'btn btn-outline-warning btn-sm';
        resetBtn.textContent = 'Reset';
        resetBtn.disabled = selectedCommanderSourceId == null;
        resetBtn.addEventListener('click', (event) => {
          event.stopPropagation();
          if (selectedCommanderSourceId == null) {
            setStatus('Select a commander source first.', 'text-warning');
            return;
          }
          submitAction('adjust_commander_damage', {
            target_id: player.user_id,
            source_id: selectedCommanderSourceId,
            total: 0,
          });
        });
        controlsRow.appendChild(resetBtn);
        commander.appendChild(controlsRow);
        row.appendChild(commander);

        row.addEventListener('click', () => {
          boardFocusMode = 'manual';
          manualBoardFocusId = player.user_id;
          if (lastGameResult) {
            renderState(lastGameResult);
          }
        });

        lifeBoardEl.appendChild(row);
      });
    }

    function applyLifeSet(targetId, rawValue) {
      const trimmed = String(rawValue ?? '').trim();
      if (!trimmed.length) {
        setStatus('Enter a life total first.', 'text-warning');
        return;
      }
      const value = Number.parseInt(trimmed, 10);
      if (Number.isNaN(value)) {
        setStatus('Life total must be a number.', 'text-warning');
        return;
      }
      submitAction('adjust_life', { target_id: targetId, life: value });
      lifeInputValues.set(targetId, '');
    }

    function renderOpponents(players, meId, activeId, priorityId, focusId) {
      clearChildren(opponentsEl);
      if (!opponentsEl) return;
      const opponents = players.filter((p) => p.user_id !== meId);
      if (!opponents.length) {
        const empty = document.createElement('div');
        empty.className = 'text-muted small';
        empty.textContent = 'Waiting for opponents.';
        opponentsEl.appendChild(empty);
        return;
      }
      opponents.forEach((player) => {
        const card = document.createElement('div');
        card.className = 'opponent-card';
        if (player.user_id === activeId) card.classList.add('is-active');
        if (player.user_id === priorityId) card.classList.add('is-priority');
        if (player.user_id === focusId) card.classList.add('is-viewing');
        const handCount = (player.zones?.hand || []).length;
        const libraryCount = (player.zones?.library || []).length;
        const seatText = seatLabel(player.user_id);
        const cmdSummary = formatCommanderDamage(player);
        const tags = [];
        if (player.user_id === focusId) tags.push('<span class="turn-tag is-viewing">Viewing</span>');
        if (player.user_id === activeId) tags.push('<span class="turn-tag is-active">Active</span>');
        if (player.user_id === priorityId) tags.push('<span class="turn-tag is-priority">Priority</span>');
        card.innerHTML = `
          <div class="opponent-head">
            <strong>${seatLabel(player.user_id)}</strong>
            <span class="seat-pill">${seatText}</span>
          </div>
          <div class="opponent-tags">${tags.join('')}</div>
          <div class="small text-muted">Life: ${player.life ?? 0}</div>
          <div class="small text-muted">Hand: ${handCount} · Library: ${libraryCount}</div>
          <div class="small text-muted">Cmd dmg: ${cmdSummary}</div>
        `;
        card.addEventListener('click', () => {
          boardFocusMode = 'manual';
          manualBoardFocusId = player.user_id;
          renderState(lastGameResult);
        });
        opponentsEl.appendChild(card);
      });
    }

    function renderZones(player) {
      clearChildren(zonesEl);
      if (!player || !zonesEl) return;
      const zones = player.zones || {};
      const focusChip = document.createElement('div');
      focusChip.className = 'zone-chip';
      focusChip.textContent = `Viewing: ${seatLabel(player.user_id)}`;
      zonesEl.appendChild(focusChip);
      const zoneEntries = [
        ['Library', zones.library],
        ['Graveyard', zones.graveyard],
        ['Exile', zones.exile],
        ['Command', zones.command],
      ];
      zoneEntries.forEach(([label, list]) => {
        const chip = document.createElement('div');
        chip.className = 'zone-chip';
        chip.textContent = `${label}: ${(list || []).length}`;
        zonesEl.appendChild(chip);
      });
    }

    function renderBattlefield(player, options) {
      clearChildren(battlefieldEl);
      if (!player || !battlefieldEl) return;
      const interactive = options?.interactive === true;
      const battlefield = player.zones?.battlefield || [];
      if (!battlefield.length) {
        const empty = document.createElement('div');
        empty.className = 'text-muted small';
        empty.textContent = interactive
          ? 'No permanents on the battlefield.'
          : `No permanents on ${seatLabel(player.user_id)}'s battlefield.`;
        battlefieldEl.appendChild(empty);
        updateSelectionCount();
        return;
      }
      battlefield.forEach((card) => {
        const item = createCardButton(card, {
          selected: interactive && selectedBattlefield.has(card.instance_id),
          onClick: () => {
            selectedCardId = card.instance_id;
            if (interactive) {
              if (selectedBattlefield.has(card.instance_id)) {
                selectedBattlefield.delete(card.instance_id);
              } else {
                selectedBattlefield.add(card.instance_id);
              }
              renderBattlefield(player, { interactive: true });
            }
            renderCardDetail(cardLookup.get(selectedCardId));
            if (state) {
              updateActionAvailability(state);
            }
            updateSelectionCount();
          }
        });
        battlefieldEl.appendChild(item);
      });
      updateSelectionCount();
    }

    function updateSelectionCount() {
      if (!selectionCountEl) return;
      const count = selectedBattlefield.size;
      selectionCountEl.textContent = `Selected: ${count}`;
      selectionCountEl.classList.toggle('is-empty', count === 0);
    }

    function renderHand(player) {
      clearChildren(handEl);
      if (!player || !handEl) return;
      const hand = player.zones?.hand || [];
      if (!hand.length) {
        const empty = document.createElement('div');
        empty.className = 'text-muted small';
        empty.textContent = 'Hand is empty.';
        handEl.appendChild(empty);
        return;
      }
      hand.forEach((card) => {
        const item = createCardButton(card, {
          selected: selectedHandId === card.instance_id,
          onClick: () => {
            const wasSelected = selectedHandId === card.instance_id;
            selectedHandId = wasSelected ? null : card.instance_id;
            selectedCardId = wasSelected ? null : card.instance_id;
            renderHand(player);
            renderCardDetail(selectedCardId ? cardLookup.get(selectedCardId) : null);
            if (state) {
              updateActionAvailability(state);
            }
          }
        });
        handEl.appendChild(item);
      });
    }

    function renderTurn(stateObj) {
      clearChildren(turnPillsEl);
      if (!stateObj) return;
      const turn = stateObj.turn || {};
      const pills = [
        `Turn ${turn.number || 1}`,
        `Phase ${turn.phase || '-'}`,
        `Step ${turn.step || '-'}`,
      ];
      pills.forEach((text) => {
        const pill = document.createElement('div');
        pill.className = 'turn-pill';
        pill.textContent = text;
        turnPillsEl.appendChild(pill);
      });
      if (turnPillsEl) {
        const currentKey = `${turn.number || 0}-${turn.phase || ''}-${turn.step || ''}`;
        if (lastTurnKey && lastTurnKey !== currentKey) {
          turnPillsEl.classList.remove('turn-pulse');
          void turnPillsEl.offsetWidth;
          turnPillsEl.classList.add('turn-pulse');
        }
        lastTurnKey = currentKey;
      }
      if (turnMetaEl) {
        const prioritySeat = turn.priority_player ? seatLabel(turn.priority_player) : '—';
        const activeSeat = turn.active_player ? seatLabel(turn.active_player) : '—';
        const youHavePriority = turn.priority_player === userId;
        const youAreActive = turn.active_player === userId;
        turnMetaEl.innerHTML = `
          <div class="small text-muted">Active: ${activeSeat}</div>
          <div class="small text-muted">Priority: ${prioritySeat}</div>
          <div class="turn-badges">
            <span class="turn-badge ${youAreActive ? 'is-active' : ''}">
              ${youAreActive ? 'Your Turn' : `Active: ${activeSeat}`}
            </span>
            <span class="turn-badge ${youHavePriority ? 'is-priority' : ''}">
              ${youHavePriority ? 'You have priority' : `Priority: ${prioritySeat}`}
            </span>
          </div>
        `;
      }
    }

    function renderStack(stateObj) {
      clearChildren(stackEl);
      if (!stateObj) return;
      const stack = stateObj.stack || [];
      if (!stack.length) {
        const empty = document.createElement('div');
        empty.className = 'log-item';
        empty.textContent = 'Stack is empty.';
        stackEl.appendChild(empty);
        return;
      }
      stack.slice().reverse().forEach((item) => {
        const entry = document.createElement('div');
        entry.className = 'log-item';
        const card = item.card || {};
        entry.textContent = `${card.name || 'Spell'} (${seatLabel(item.controller_id)})`;
        stackEl.appendChild(entry);
      });
    }

    function formatEvent(evt) {
      if (!evt) return 'Event';
      const type = evt.event_type || evt.type || 'event';
      const payload = evt.payload || {};
      if (type === 'life_adjusted') {
        const delta = payload.delta ?? 0;
        const total = payload.total ?? 0;
        const target = payload.target != null ? seatLabel(payload.target) : 'Player';
        const sign = delta >= 0 ? '+' : '';
        return `Life ${sign}${delta} for ${target} (now ${total})`;
      }
      if (type === 'commander_damage' || type === 'commander_damage_adjusted') {
        const target = payload.target != null ? seatLabel(payload.target) : 'Player';
        const source = payload.source != null ? seatLabel(payload.source) : 'Commander';
        const amount = payload.amount ?? payload.delta ?? 0;
        const total = payload.total ?? null;
        const totalText = total != null ? ` (total ${total})` : '';
        return `Commander damage ${amount} from ${source} to ${target}${totalText}`;
      }
      if (type === 'commander_flagged') {
        const status = payload.is_commander ? 'marked as commander' : 'removed as commander';
        return `${payload.card || 'Card'} ${status}`;
      }
      if (payload.card) {
        return `${type}: ${payload.card}`;
      }
      if (payload.choice && payload.choice.prompt) {
        return `Choice: ${payload.choice.prompt}`;
      }
      if (payload.amount != null && payload.target != null) {
        return `${type}: ${payload.amount} to ${payload.target}`;
      }
      if (payload.count != null) {
        return `${type}: ${payload.count}`;
      }
      return type;
    }

    function formatEventCategory(evt) {
      const type = evt?.event_type || evt?.type || '';
      if (type.includes('combat')) return 'combat';
      if (type.includes('life')) return 'life';
      if (type.includes('commander')) return 'commander';
      if (type.includes('spell') || type.includes('land') || type.includes('draw')) return 'game';
      if (type.includes('choice')) return 'choices';
      return 'other';
    }

    function eventMatchesFilter(evt, filter) {
      if (!evt) return false;
      if (filter === 'all') return true;
      return formatEventCategory(evt) === filter;
    }

    function shouldIgnoreHotkeys(target) {
      if (!target) return false;
      const tag = target.tagName ? target.tagName.toLowerCase() : '';
      if (target.isContentEditable) return true;
      return tag === 'input' || tag === 'textarea' || tag === 'select';
    }

    function ensureEventsPlaceholder() {
      if (!eventsEl) return;
      if (eventsEl.childElementCount > 0) return;
      const empty = document.createElement('div');
      empty.className = 'log-item';
      empty.textContent = 'No events yet.';
      empty.dataset.placeholder = 'true';
      eventsEl.appendChild(empty);
      if (eventsBadgeEl) {
        eventsBadgeEl.classList.remove('is-visible');
        eventsBadgeEl.textContent = '';
      }
    }

    function rebuildEventsLog(fullEvents) {
      if (!eventsEl) return;
      eventsEl.innerHTML = '';
      const filtered = (fullEvents || []).filter((evt) => eventMatchesFilter(evt, eventFilter));
      if (!filtered.length) {
        const empty = document.createElement('div');
        empty.className = 'log-item';
        empty.textContent = 'No events yet.';
        empty.dataset.placeholder = 'true';
        eventsEl.appendChild(empty);
        return;
      }
      filtered.slice().reverse().forEach((evt) => {
        const item = document.createElement('div');
        item.className = 'log-item';
        item.textContent = formatEvent(evt);
        eventsEl.appendChild(item);
      });
      eventsEl.scrollTop = 0;
    }

    function renderDefenders(players, meId) {
      clearChildren(defenderSelectEl);
      if (!defenderSelectEl) return;
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'Select defender';
      defenderSelectEl.appendChild(placeholder);
      players.filter((p) => p.user_id !== meId).forEach((player) => {
        const opt = document.createElement('option');
        opt.value = String(player.user_id);
        opt.textContent = seatLabel(player.user_id);
        defenderSelectEl.appendChild(opt);
      });
    }

    function renderCommanderSource(players) {
      if (!commanderSourceSelect) return;
      clearChildren(commanderSourceSelect);
      const list = players || [];
      list.forEach((player) => {
        const opt = document.createElement('option');
        opt.value = String(player.user_id);
        opt.textContent = player.user_id === userId
          ? `${seatLabel(player.user_id)} (You)`
          : seatLabel(player.user_id);
        commanderSourceSelect.appendChild(opt);
      });
      if (!list.length) {
        selectedCommanderSourceId = null;
        return;
      }
      const ids = new Set(list.map((player) => player.user_id));
      if (!ids.has(selectedCommanderSourceId)) {
        selectedCommanderSourceId = list[0].user_id;
      }
      commanderSourceSelect.value = String(selectedCommanderSourceId);
    }

    function renderBlockers(stateObj, me) {
      clearChildren(blockerAssignmentsEl);
      if (!stateObj || !me || !blockerAssignmentsEl) return;
      const combat = stateObj.combat || {};
      const attackers = combat.attackers || {};
      const myBattlefield = me.zones?.battlefield || [];
      const myBlockers = myBattlefield.filter((card) => !card.tapped);
      const attackerIds = Object.keys(attackers);
      if (!attackerIds.length || !myBlockers.length) return;
      attackerIds.forEach((attackerId) => {
        const attacker = findCardById(stateObj, attackerId);
        const group = document.createElement('div');
        group.className = 'blocker-group';
        const title = document.createElement('div');
        title.className = 'small text-muted';
        title.textContent = `Block ${attacker?.name || 'Attacker'}`;
        group.appendChild(title);
        myBlockers.forEach((blocker) => {
          const label = document.createElement('label');
          label.className = 'choice-option';
          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.dataset.attackerId = attackerId;
          checkbox.dataset.blockerId = blocker.instance_id;
          label.appendChild(checkbox);
          const span = document.createElement('span');
          span.textContent = blocker.name || 'Blocker';
          label.appendChild(span);
          group.appendChild(label);
        });
        blockerAssignmentsEl.appendChild(group);
      });
    }

    function findCardById(stateObj, instanceId) {
      if (!stateObj) return null;
      const players = stateObj.players || [];
      for (const player of players) {
        const zones = player.zones || {};
        for (const zoneName of Object.keys(zones)) {
          const cards = zones[zoneName] || [];
          for (const card of cards) {
            if (card.instance_id === instanceId) return card;
          }
        }
      }
      return null;
    }

    function renderChoices(stateObj, meId) {
      clearChildren(choicesEl);
      if (!stateObj || !choicesEl) return;
      const choices = (stateObj.choices || []).filter((choice) => choice.player_id === meId);
      if (!choices.length) {
        const empty = document.createElement('div');
        empty.className = 'text-muted small';
        empty.textContent = 'No pending choices.';
        choicesEl.appendChild(empty);
        return;
      }
      choices.forEach((choice) => {
        const panel = document.createElement('div');
        panel.className = 'choice-panel';
        const prompt = document.createElement('div');
        prompt.className = 'small text-muted';
        prompt.textContent = choice.prompt || 'Make a choice.';
        panel.appendChild(prompt);
        const optionsWrap = document.createElement('div');
        optionsWrap.className = 'choice-options';
        (choice.options || []).forEach((opt) => {
          const label = document.createElement('label');
          label.className = 'choice-option';
          const checkbox = document.createElement('input');
          checkbox.type = choice.max && choice.max <= 1 ? 'radio' : 'checkbox';
          checkbox.name = `choice-${choice.id}`;
          checkbox.value = opt.id;
          label.appendChild(checkbox);
          const span = document.createElement('span');
          span.textContent = opt.label || opt.id;
          label.appendChild(span);
          optionsWrap.appendChild(label);
        });
        panel.appendChild(optionsWrap);
        const submit = document.createElement('button');
        submit.className = 'btn btn-outline-primary btn-sm';
        submit.type = 'button';
        submit.textContent = 'Resolve';
        submit.addEventListener('click', async () => {
          const selections = Array.from(optionsWrap.querySelectorAll('input:checked')).map((input) => input.value);
          try {
            await api(templateUrl(actionsUrlTemplate), {
              method: 'POST',
              body: JSON.stringify({ action_type: 'resolve_choice', payload: { choice_id: choice.id, selections } })
            });
            await refresh();
          } catch (err) {
            setStatus(err.message, 'text-danger');
          }
        });
        panel.appendChild(submit);
        choicesEl.appendChild(panel);
      });
    }

    function updateActionAvailability(stateObj) {
      const turn = stateObj?.turn || {};
      const hasPriority = turn.priority_player === userId;
      const isActive = turn.active_player === userId;
      const phase = turn.phase || '';
      const step = turn.step || '';
      const stackEmpty = !(stateObj?.stack || []).length;
      const status = stateObj?.status || '';

      if (playLandBtn) {
        playLandBtn.disabled = !(hasPriority && isActive && phase === 'main' && stackEmpty);
      }
      if (castSpellBtn) {
        castSpellBtn.disabled = !hasPriority;
      }
      if (passBtn) {
        passBtn.disabled = !hasPriority;
      }
      if (resolveBtn) {
        resolveBtn.disabled = !(hasPriority && !stackEmpty);
      }
      if (declareAttackersBtn) {
        declareAttackersBtn.disabled = !(isActive && phase === 'combat' && step === 'declare_attackers');
      }
      if (declareBlockersBtn) {
        declareBlockersBtn.disabled = !(phase === 'combat' && step === 'declare_blockers');
      }
      if (combatDamageBtn) {
        combatDamageBtn.disabled = !(isActive && phase === 'combat' && step === 'damage');
      }
      if (commanderBtn) {
        const card = selectedCardId ? cardLookup.get(selectedCardId) : null;
        const ownerId = card?.owner_id ?? card?.controller_id;
        commanderBtn.disabled = !card || ownerId !== userId;
        commanderBtn.textContent = card?.is_commander ? 'Unmark Commander' : 'Mark Commander';
      }
      if (startBtn) {
        startBtn.disabled = status !== 'waiting';
      }
      if (mulliganBtn) {
        mulliganBtn.disabled = status !== 'mulligan';
      }
      if (keepBtn) {
        keepBtn.disabled = status !== 'mulligan';
      }
    }

    function renderState(gameResult) {
      if (!gameResult || !gameResult.game) return;
      lastGameResult = gameResult;
      const game = gameResult.game;
      const players = gameResult.players || [];
      state = game.state || {};
      if (game.id) {
        storeGameId(game.id);
      }
      subtitleEl.textContent = `Lobby ${game.id} · ${game.status}`;
      const me = state.players?.find((p) => p.user_id === userId);
      const meFromPlayers = me || players.find((p) => p.user_id === userId);
      const effectiveMe = me || meFromPlayers || null;
      buildSeatLookup(players);
      const orderedPlayers = sortPlayersBySeat(state.players || []);

      buildCardLookup(state);
      if (selectedCardId && !cardLookup.has(selectedCardId)) {
        selectedCardId = null;
      }
      if (effectiveMe) {
        const handIds = new Set((effectiveMe.zones?.hand || []).map((card) => card.instance_id));
        const battlefieldIds = new Set(
          (effectiveMe.zones?.battlefield || []).map((card) => card.instance_id)
        );
        if (selectedHandId && !handIds.has(selectedHandId)) {
          selectedHandId = null;
        }
        selectedBattlefield = new Set(
          Array.from(selectedBattlefield).filter((cardId) => battlefieldIds.has(cardId))
        );
      } else {
        selectedHandId = null;
        selectedBattlefield = new Set();
      }

      const activeId = state.turn?.active_player;
      const priorityId = state.turn?.priority_player;
      const focusPlayer = resolveBoardFocus(state, effectiveMe);
      if (focusPlayer && focusPlayer.user_id !== userId) {
        selectedBattlefield = new Set();
      }
      const battlefieldSurface = battlefieldEl ? battlefieldEl.closest('.table-surface') : null;
      if (battlefieldSurface) {
        const isActiveFocus = focusPlayer && activeId && focusPlayer.user_id === activeId;
        battlefieldSurface.classList.toggle('is-active-focus', Boolean(isActiveFocus));
        battlefieldSurface.classList.toggle('is-manual-focus', boardFocusMode === 'manual' && !isActiveFocus);
      }

      renderOpponents(orderedPlayers, userId, activeId, priorityId, focusPlayer?.user_id);
      renderBattlefield(focusPlayer, { interactive: focusPlayer?.user_id === userId });
      renderHand(effectiveMe);
      renderZones(focusPlayer);
      renderPlayerSummary(effectiveMe);
      renderCommanderSource(orderedPlayers);
      renderLifeBoard(orderedPlayers, activeId, priorityId, focusPlayer?.user_id);
      updateSelectionCount();
      renderCardDetail(selectedCardId ? cardLookup.get(selectedCardId) : null);
      renderTurn(state);
      renderStack(state);
      renderDefenders(orderedPlayers, userId);
      renderBlockers(state, effectiveMe);
      renderChoices(state, userId);
      ensureEventsPlaceholder();
      queueImageFetch(state);

      const status = state.status || game.status;
      setStatus(`Game status: ${status}`, 'text-muted');

      updateActionAvailability(state);
    }

    async function refresh() {
      if (!engineEnabled) {
        setStatus('Engine not configured.', 'text-warning');
        return;
      }
      if (!gameId) {
        setStatus('No lobby ID provided.', 'text-warning');
        return;
      }
      try {
        const result = await api(templateUrl(gameUrlTemplate), { method: 'GET' });
        renderState(result);
      } catch (err) {
        setStatus(err.message, 'text-danger');
      }
    }

    async function refreshEvents() {
      if (!gameId || !eventsUrlTemplate) return;
      try {
        const url = templateUrl(eventsUrlTemplate);
        const params = lastEventSeq ? `?since=${lastEventSeq}` : '';
        const result = await api(`${url}${params}`, { method: 'GET' });
        const events = result.events || [];
        if (!window.__gameEngineEventsStore) {
          window.__gameEngineEventsStore = [];
        }
        const store = window.__gameEngineEventsStore;
        if (events.length) {
        events.forEach((evt) => store.push(evt));
        if (store.length > 250) {
          store.splice(0, store.length - 250);
        }
      }
        if (!events.length) {
          ensureEventsPlaceholder();
          return;
        }
        if (eventsEl) {
          const shouldStick = eventsEl.scrollTop <= 24;
          const placeholder = eventsEl.querySelector('[data-placeholder="true"]');
          if (placeholder) placeholder.remove();
          if (!shouldStick) {
            eventsEl.dataset.preserveScroll = 'true';
          } else {
            eventsEl.dataset.preserveScroll = 'false';
          }
        }
        let latestEvent = null;
        events.forEach((evt) => {
          if (!eventMatchesFilter(evt, eventFilter)) {
            latestEvent = evt;
            lastEventSeq = Math.max(lastEventSeq, evt.seq || lastEventSeq);
            return;
          }
          const item = document.createElement('div');
          item.className = 'log-item';
          item.textContent = formatEvent(evt);
          if (eventsEl) {
            eventsEl.prepend(item);
          }
          latestEvent = evt;
          lastEventSeq = Math.max(lastEventSeq, evt.seq || lastEventSeq);
        });
        if (eventsEl && eventsEl.dataset.preserveScroll !== 'true') {
          eventsEl.scrollTop = 0;
        }
        if (eventsBadgeEl) {
          if (eventsEl && eventsEl.dataset.preserveScroll === 'true') {
            const currentCount = Number(eventsBadgeEl.dataset.count || 0);
            const matchedCount = events.filter((evt) => eventMatchesFilter(evt, eventFilter)).length;
            if (!matchedCount) {
              if (lastEventEl && latestEvent) {
                lastEventEl.textContent = `Last event: ${formatEvent(latestEvent)}`;
                lastEventEl.className = 'small text-muted';
              }
              return;
            }
            const nextCount = currentCount + matchedCount;
            eventsBadgeEl.dataset.count = String(nextCount);
            eventsBadgeEl.textContent = `${nextCount} new`;
            eventsBadgeEl.classList.add('is-visible');
          } else {
            eventsBadgeEl.classList.remove('is-visible');
            eventsBadgeEl.dataset.count = '0';
            eventsBadgeEl.textContent = '';
          }
        }
        if (lastEventEl && latestEvent) {
          lastEventEl.textContent = `Last event: ${formatEvent(latestEvent)}`;
          lastEventEl.className = 'small text-muted';
        }
      } catch (err) {
        // ignore event errors
      }
    }

    async function submitAction(actionType, payload) {
      if (!gameId) return;
      try {
        await api(templateUrl(actionsUrlTemplate), {
          method: 'POST',
          body: JSON.stringify({ action_type: actionType, payload: payload || {} })
        });
        await refresh();
      } catch (err) {
        setStatus(err.message, 'text-danger');
      }
    }

    function buildBlocks() {
      const blocks = [];
      const selections = blockerAssignmentsEl ? blockerAssignmentsEl.querySelectorAll('input[type="checkbox"]:checked') : [];
      selections.forEach((input) => {
        blocks.push({
          blocker_id: input.dataset.blockerId,
          attacker_id: input.dataset.attackerId,
        });
      });
      return blocks;
    }

    if (refreshBtn) refreshBtn.addEventListener('click', refresh);
    if (commanderSourceSelect) {
      commanderSourceSelect.addEventListener('change', (event) => {
        const value = event.target.value;
        selectedCommanderSourceId = value ? Number(value) : null;
        if (lastGameResult) {
          renderState(lastGameResult);
        }
      });
    }
    if (boardFollowBtn) {
      boardFollowBtn.addEventListener('click', () => {
        if (boardFocusMode === 'auto') {
          boardFocusMode = 'manual';
          manualBoardFocusId = userId;
        } else {
          boardFocusMode = 'auto';
        }
        if (lastGameResult) {
          renderState(lastGameResult);
        }
      });
    }
    if (boardMineBtn) {
      boardMineBtn.addEventListener('click', () => {
        boardFocusMode = 'manual';
        manualBoardFocusId = userId;
        if (lastGameResult) {
          renderState(lastGameResult);
        }
      });
    }
    if (boardActiveBtn) {
      boardActiveBtn.addEventListener('click', () => {
        const activeId = state?.turn?.active_player;
        if (!activeId) {
          setStatus('No active player yet.', 'text-warning');
          return;
        }
        boardFocusMode = 'manual';
        manualBoardFocusId = activeId;
        if (lastGameResult) {
          renderState(lastGameResult);
        }
      });
    }
    if (drawBtn) drawBtn.addEventListener('click', () => submitAction('draw', { count: 1 }));
    if (passBtn) passBtn.addEventListener('click', () => submitAction('pass_priority'));
    if (resolveBtn) resolveBtn.addEventListener('click', () => submitAction('resolve_top'));
    if (clearSelectionBtn) {
      clearSelectionBtn.addEventListener('click', () => {
        selectedHandId = null;
        selectedBattlefield = new Set();
        selectedCardId = null;
        if (lastGameResult) {
          renderState(lastGameResult);
        }
      });
    }
    if (playLandBtn) playLandBtn.addEventListener('click', () => {
      if (!selectedHandId) {
        setStatus('Select a land in your hand first.', 'text-warning');
        return;
      }
      submitAction('play_land', { card_id: selectedHandId });
    });
    if (castSpellBtn) castSpellBtn.addEventListener('click', () => {
      if (!selectedHandId) {
        setStatus('Select a spell in your hand first.', 'text-warning');
        return;
      }
      submitAction('cast_spell', { card_id: selectedHandId });
    });
    if (commanderBtn) commanderBtn.addEventListener('click', () => {
      if (!selectedCardId) {
        setStatus('Select a card to toggle commander.', 'text-warning');
        return;
      }
      const card = cardLookup.get(selectedCardId);
      if (!card) {
        setStatus('Selected card not found.', 'text-warning');
        return;
      }
      const ownerId = card.owner_id ?? card.controller_id;
      if (ownerId !== userId) {
        setStatus('Only your cards can be marked as commander.', 'text-warning');
        return;
      }
      submitAction('set_commander', { card_id: selectedCardId, is_commander: !card.is_commander });
    });
    if (startBtn) startBtn.addEventListener('click', () => submitAction('start_game'));
    if (mulliganBtn) mulliganBtn.addEventListener('click', () => submitAction('mulligan'));
    if (keepBtn) keepBtn.addEventListener('click', () => submitAction('keep_hand'));
    if (declareAttackersBtn) declareAttackersBtn.addEventListener('click', () => {
      const defenderId = defenderSelectEl && defenderSelectEl.value;
      if (!defenderId) {
        setStatus('Select a defender.', 'text-warning');
        return;
      }
      if (!selectedBattlefield.size) {
        setStatus('Select at least one attacker on the battlefield.', 'text-warning');
        return;
      }
      const attackers = Array.from(selectedBattlefield);
      submitAction('declare_attackers', {
        attackers,
        defender: { type: 'player', id: Number(defenderId) }
      });
      selectedBattlefield = new Set();
    });
    if (declareBlockersBtn) declareBlockersBtn.addEventListener('click', () => {
      const blocks = buildBlocks();
      if (!blocks.length) {
        setStatus('Select blockers for attackers first.', 'text-warning');
        return;
      }
      submitAction('declare_blockers', { blocks });
    });
    if (combatDamageBtn) combatDamageBtn.addEventListener('click', () => submitAction('combat_damage'));

    if (!gameId) {
      const storedId = getStoredGameId();
      if (missingGameInput && storedId) {
        missingGameInput.value = storedId;
      }
      if (missingUseLastBtn) {
        missingUseLastBtn.disabled = !storedId;
        missingUseLastBtn.addEventListener('click', () => {
          if (storedId) {
            redirectToGame(storedId);
          }
        });
      }
      if (missingOpenBtn) {
        missingOpenBtn.addEventListener('click', () => {
          const value = (missingGameInput && missingGameInput.value || '').trim();
          if (!value) {
            setStatus('Enter a lobby ID first.', 'text-warning');
            return;
          }
          redirectToGame(value);
        });
      }
      return;
    }

    if (engineEnabled && gameId) {
      refresh();
      refreshEvents();
      pollingTimer = window.setInterval(() => {
        refresh();
        refreshEvents();
      }, 4000);
    } else if (!engineEnabled) {
      setStatus('Engine not configured.', 'text-warning');
    }

    if (eventsEl && eventsBadgeEl) {
      const clearBadgeIfTop = () => {
        if (eventsEl.scrollTop <= 24) {
          eventsBadgeEl.classList.remove('is-visible');
          eventsBadgeEl.dataset.count = '0';
          eventsBadgeEl.textContent = '';
        }
      };
      eventsEl.addEventListener('scroll', clearBadgeIfTop);
      eventsBadgeEl.addEventListener('click', () => {
        eventsEl.scrollTop = 0;
        clearBadgeIfTop();
      });
    }
    if (eventsFilterEl) {
      eventsFilterEl.addEventListener('change', (event) => {
        eventFilter = event.target.value || 'all';
        const store = window.__gameEngineEventsStore || [];
        rebuildEventsLog(store);
      });
    }

    if (!window.__gameEngineTableHotkeysBound) {
      window.__gameEngineTableHotkeysBound = true;
      document.addEventListener('keydown', (event) => {
        if (!engineEnabled || !gameId) return;
        if (event.metaKey || event.ctrlKey || event.altKey) return;
        if (shouldIgnoreHotkeys(event.target)) return;
        const key = event.key.toLowerCase();
        if (key === 'f') {
          boardFocusMode = 'auto';
          if (lastGameResult) renderState(lastGameResult);
        } else if (key === 'm') {
          boardFocusMode = 'manual';
          manualBoardFocusId = userId;
          if (lastGameResult) renderState(lastGameResult);
        } else if (key === 'a') {
          const activeId = state?.turn?.active_player;
          if (activeId) {
            boardFocusMode = 'manual';
            manualBoardFocusId = activeId;
            if (lastGameResult) renderState(lastGameResult);
          }
        } else if (key === 'c' || key === 'escape') {
          selectedHandId = null;
          selectedBattlefield = new Set();
          selectedCardId = null;
          if (lastGameResult) renderState(lastGameResult);
        }
      });
    }
  }

  function boot() {
    initGameEngineTable();
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
