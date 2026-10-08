"""Validation hors réseau du catalogue UI des études.

Vérifie que chaque study_id du catalogue local possède une entrée de routage UI.
Une étude peut explicitement être marquée ui_supported=false lorsque l'audit
fourni ne permet pas d'établir une route UI sûre (cas companyResults).
"""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
catalog = pd.read_csv(ROOT / 'subakoua_study_catalog.csv')
routes = json.loads((ROOT / 'study_ui_routes.json').read_text(encoding='utf-8')).get('routes', {})

ids = set(catalog['study_id'].astype(str))
missing = sorted(ids - set(routes))
empty = sorted(sid for sid in ids if not routes.get(sid, {}).get('ui_supported', False))
malformed = sorted(
    sid for sid in ids
    if routes.get(sid, {}).get('url_template') and '/companies/partners/' not in routes[sid]['url_template']
)

print(f'catalogue={len(ids)} routes={len(ids & set(routes))}')
print('missing=', missing)
print('ui_unsupported=', empty)
print('malformed=', malformed)
raise SystemExit(1 if missing or malformed else 0)
