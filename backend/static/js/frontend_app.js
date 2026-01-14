// Minimal SPA-like frontend that consumes the JSON API endpoints.
(() => {
  const root = document.getElementById("dvApiFrontend");
  if (!root) return;

  const folderListEl = document.getElementById("dvFolderList");
  const folderEmptyEl = document.getElementById("dvFolderEmpty");
  const folderTitleEl = document.getElementById("dvFolderTitle");
  const folderMetaEl = document.getElementById("dvFolderMeta");
  const folderCountEl = document.getElementById("dvFolderCount");
  const cardsTableEl = document.getElementById("dvCardsTable");
  const cardsBodyEl = document.getElementById("dvCardsTbody");
  const statusEl = document.getElementById("dvStatus");
  const userBadge = document.getElementById("dvUserBadge");
  const refreshBtn = document.getElementById("dvRefreshBtn");

  const defaultLimit = parseInt(root.dataset.defaultLimit || "200", 10) || 200;

  const state = {
    folders: [],
    selectedId: null,
    selectedFolder: null,
    cards: [],
    pagination: { total: 0, limit: defaultLimit, offset: 0 },
    user: null,
  };

  const setStatus = (msg, isError = false) => {
    statusEl.textContent = msg;
    statusEl.classList.toggle("text-danger", isError);
  };

  const apiFetch = async (url) => {
    const res = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        detail = body.error || body.message || body.detail || "";
      } catch (_) {
        // ignore parse errors
      }
      throw new Error(detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  };

  const renderUser = () => {
    if (!userBadge) return;
    if (!state.user) {
      userBadge.textContent = "Not signed in";
      userBadge.classList.add("text-danger");
      return;
    }
    const name = state.user.username || "User";
    const email = state.user.email ? ` (${state.user.email})` : "";
    userBadge.textContent = `${name}${email}`;
    userBadge.classList.remove("text-danger");
  };

  const renderFolders = () => {
    folderListEl.innerHTML = "";
    const folders = state.folders || [];
    folderEmptyEl.hidden = folders.length > 0;
    folders.forEach((folder) => {
      const li = document.createElement("li");
      li.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
      if (folder.id === state.selectedId) li.classList.add("active");
      li.setAttribute("role", "button");
      li.tabIndex = 0;

      const name = document.createElement("div");
      name.className = "d-flex align-items-center gap-2";
      const nameText = document.createElement("span");
      nameText.className = "fw-semibold";
      nameText.textContent = `${folder.name}`;
      name.appendChild(nameText);

      const pill = document.createElement("span");
      pill.className = "badge text-bg-light";
      const counts = folder.counts || {};
      pill.textContent = `${counts.unique || 0} unique / ${counts.total || 0} qty`;

      const flags = document.createElement("div");
      flags.className = "d-flex align-items-center gap-1";
      if (folder.deck_tag) {
        const tag = document.createElement("span");
        tag.className = "badge text-bg-secondary";
        tag.textContent = folder.deck_tag;
        flags.appendChild(tag);
      }
      if (folder.is_proxy) {
        const proxy = document.createElement("span");
        proxy.className = "badge text-bg-warning";
        proxy.textContent = "Proxy";
        flags.appendChild(proxy);
      }
      if (folder.is_public) {
        const pub = document.createElement("span");
        pub.className = "badge text-bg-success";
        pub.textContent = "Public";
        flags.appendChild(pub);
      }
      if (flags.childElementCount) {
        name.appendChild(flags);
      }

      li.appendChild(name);
      li.appendChild(pill);
      li.addEventListener("click", () => selectFolder(folder.id));
      li.addEventListener("keypress", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          selectFolder(folder.id);
        }
      });
      folderListEl.appendChild(li);
    });
  };

  const renderCards = () => {
    cardsBodyEl.innerHTML = "";
    if (!state.cards || !state.cards.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 4;
      td.className = "text-muted text-center";
      td.textContent = "No cards found for this folder.";
      tr.appendChild(td);
      cardsBodyEl.appendChild(tr);
      return;
    }
    state.cards.forEach((card) => {
      const tr = document.createElement("tr");
      const fmt = (val) => (val === true ? "Yes" : val === false ? "No" : val || "—");

      const nameTd = document.createElement("td");
      nameTd.className = "fw-semibold";
      nameTd.textContent = `${card.name}`;

      const setTd = document.createElement("td");
      setTd.className = "text-uppercase";
      setTd.textContent = `${card.set_code || ""} #${card.collector_number || ""}`;

      const qtyTd = document.createElement("td");
      qtyTd.className = "text-center";
      qtyTd.textContent = `${card.quantity}`;

      const foilTd = document.createElement("td");
      foilTd.className = "text-center";
      foilTd.textContent = fmt(card.is_foil);

      tr.appendChild(nameTd);
      tr.appendChild(setTd);
      tr.appendChild(qtyTd);
      tr.appendChild(foilTd);
      cardsBodyEl.appendChild(tr);
    });
  };

  const renderFolderDetail = () => {
    const folder = state.selectedFolder;
    if (!folder) return;
    folderTitleEl.textContent = folder.name || "Folder";
    const tag = folder.deck_tag ? ` • ${folder.deck_tag}` : "";
    const commander = folder.commander_name ? ` • Commander: ${folder.commander_name}` : "";
    folderMetaEl.textContent = `${folder.category || "deck"}${tag}${commander}`;

    const counts = folder.counts || {};
    const total = state.pagination?.total ?? counts.total ?? 0;
    const uniques = counts.unique ?? 0;
    folderCountEl.textContent = `${total} cards • ${uniques} unique`;

    cardsTableEl.hidden = false;
    renderCards();
  };

  const selectFolder = async (folderId) => {
    state.selectedId = folderId;
    renderFolders();
    setStatus("Loading folder…");
    try {
      const [folderResp, cardsResp] = await Promise.all([
        apiFetch(`/api/folders/${folderId}`),
        apiFetch(`/api/folders/${folderId}/cards?limit=${defaultLimit}`),
      ]);
      state.selectedFolder = folderResp.data;
      state.cards = cardsResp.data || [];
      state.pagination = cardsResp.pagination || { total: state.cards.length, limit: defaultLimit, offset: 0 };
      renderFolderDetail();
      setStatus("Loaded.");
    } catch (err) {
      console.error(err);
      state.selectedFolder = null;
      state.cards = [];
      cardsTableEl.hidden = true;
      folderTitleEl.textContent = "Select a folder";
      folderMetaEl.textContent = "Waiting for selection.";
      folderCountEl.textContent = "";
      setStatus(`Failed to load folder: ${err.message}`, true);
    }
  };

  const loadFolders = async () => {
    setStatus("Loading folders…");
    try {
      const payload = await apiFetch("/api/folders");
      state.folders = payload.data || [];
      renderFolders();
      if (state.folders.length) {
        const fallbackId = state.selectedId || state.folders[0].id;
        selectFolder(fallbackId);
      } else {
        cardsTableEl.hidden = true;
        folderTitleEl.textContent = "Select a folder";
        folderMetaEl.textContent = "No folders found.";
        folderCountEl.textContent = "";
        setStatus("No folders yet.");
      }
    } catch (err) {
      console.error(err);
      setStatus(`Failed to load folders: ${err.message}`, true);
      folderEmptyEl.hidden = false;
    }
  };

  const loadUser = async () => {
    try {
      const payload = await apiFetch("/api/me");
      state.user = payload.data;
      renderUser();
    } catch (err) {
      console.error(err);
      userBadge.textContent = `User error: ${err.message}`;
      userBadge.classList.add("text-danger");
    }
  };

  refreshBtn?.addEventListener("click", () => {
    loadUser();
    loadFolders();
  });

  loadUser();
  loadFolders();
})();
