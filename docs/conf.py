from __future__ import annotations

import os
import sys
from datetime import datetime

ROOT = os.path.abspath("..")
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

project = "DragonsVault"
author = "DragonsVault contributors"
copyright = f"{datetime.utcnow():%Y}, {author}"

extensions = [
    "sphinx.ext.extlinks",
]

extlinks = {
    "issue": ("https://github.com/JBSmith29/DragonsVault/issues/%s", "issue #%s"),
    "pr": ("https://github.com/JBSmith29/DragonsVault/pull/%s", "PR #%s"),
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "alabaster"
html_static_path = ["_static"]
