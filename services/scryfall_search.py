# DragonsVault/services/scryfall_search.py
import os, urllib.parse
import requests
from requests.adapters import HTTPAdapter, Retry

UA = os.getenv("SCRYFALL_UA", "DragonsVault/6 (+https://dragonsvault.local)")
BASE = "https://api.scryfall.com"

def _session():
    s = requests.Session()
    retries = Retry(total=5, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({"User-Agent": UA})
    return s

def build_query(
    *,
    name="",
    set_code="",
    base_types=(),
    typal="",
    colors=(),
    color_mode="contains",
    commander_only=False,
    rarity="",
):
    """
    Build a Scryfall 'q' string from UI inputs.
    - base_types: e.g., ["Creature","Artifact","Enchantment"]
    - colors: e.g., ["W","U","B","R","G"]
    - color_mode: 'exact' or 'contains' (applies to color identity using 'id=' vs 'id<=')
    """
    terms = []

    # Name (partial matches okay; wrap exact quotes if you want exact)
    if name:
        terms.append(name)

    # Set
    if set_code:
        terms.append(f"set:{set_code}")

    # Types (Scryfall uses t:<type>)
    for t in base_types:
        t = t.strip()
        if t:
            terms.append(f"t:{t}")

    # Typal (subtype line)
    if typal:
        terms.append(f"t:{typal}")

    # Colors by *color identity*
    if colors:
        # Scryfall: id= (exact) vs id<= (subset/contains)
        op = "=" if color_mode == "exact" else "<="
        terms.append(f"id{op}{''.join(sorted(colors))}")

    # Commander legality
    if commander_only:
        terms.append("legal:commander")

    rarity = (rarity or "").strip().lower()
    if rarity and rarity not in {"any"}:
        terms.append(f"rarity:{rarity}")

    # Final query
    q = " ".join(terms).strip() or "*"
    return q

def search_cards(q, *, unique="cards", page=1, order="name", direction="asc"):
    sess = _session()
    params = {"q": q, "unique": unique, "order": order or "name", "page": page}
    if direction and direction.lower() == "desc":
        params["dir"] = "desc"
    url = f"{BASE}/cards/search?{urllib.parse.urlencode(params)}"
    r = sess.get(url, timeout=30, verify=False)
    if r.status_code == 404:
        return {"data": [], "total_cards": 0, "has_more": False, "next_page": None, "warnings": []}
    r.raise_for_status()
    return r.json()
