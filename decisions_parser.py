"""Décisions Subakoua scrapées (accueil → tuiles DÉCISIONS) → valeurs du simulateur.

Sans dépendance Streamlit / SQL. Entrée : le contenu JSON stocké dans ``erp_donnees``
pour les modules ``marketing``, ``rh``, ``production`` (extraction scraper >= 2.4.0,
qui ajoute ``__lignes__`` / ``__champs__``). Les anciennes extractions (sections →
lignes → listes de valeurs) sont lues en dernier recours.

Le parseur est volontairement heuristique (libellés reconnus sans accent ni casse) et
BORNÉ par des contrôles de vraisemblance : une valeur douteuse est ignorée plutôt
qu'injectée. ``report`` liste ce qui a été reconnu pour permettre le contrôle visuel.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping

PRODUCT_KEYS = ("s3", "i3", "s5", "i5", "i7")
_PRODUCT_PATTERNS = (
    ("s3", re.compile(r"\bshorty\s*(3|c)\b|\bshorty_c\b|\bs\s*3\b")),
    ("i3", re.compile(r"\bintegral\s*(3|c)\b|\bmono_c\b|\bi\s*3\b")),
    ("s5", re.compile(r"\bshorty\s*(5|t)\b|\bshorty_t\b|\bs\s*5\b")),
    ("i5", re.compile(r"\bintegral\s*(5|t)\b|\bmono_t\b|\bi\s*5\b")),
    ("i7", re.compile(r"\bintegral\s*(7|f)\b|\bmono_f\b|\bi\s*7\b")),
)
_DEPARTMENTS = (
    ("Prod", ("production",)),
    ("Appro", ("approvisionnement", "appro")),
    ("Admin", ("administration", "admin", "finance")),
)
_CATEGORIES = (("Employes", ("employe", "ouvrier")), ("Cadres", ("cadre",)), ("Directeurs", ("directeur", "direction")))


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def detect_product(*labels: Any) -> str | None:
    for label in labels:
        text = norm(label)
        if not text:
            continue
        for key, pattern in _PRODUCT_PATTERNS:
            if pattern.search(text):
                return key
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numbers(values: Iterable[Any]) -> list[float]:
    return [float(v) for v in values if _is_number(v)]


def _strings(values: Iterable[Any]) -> list[str]:
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


# ---------------------------------------------------------------------------
# Collecte des lignes (section, ligne, valeurs)
# ---------------------------------------------------------------------------
Row = tuple[str, str, list]


def _rows_from_scraper_24(node: Any, out: list[Row]) -> bool:
    found = False
    if isinstance(node, Mapping):
        lignes = node.get("__lignes__")
        if isinstance(lignes, list) and lignes:
            for item in lignes:
                if isinstance(item, Mapping):
                    out.append((str(item.get("section", "")), str(item.get("ligne", "")), list(item.get("valeurs") or [])))
            found = True
        else:
            champs = node.get("__champs__")
            if isinstance(champs, list) and champs:
                grouped: dict[tuple[str, str], list[tuple[int, Any]]] = {}
                for c in champs:
                    if isinstance(c, Mapping):
                        key = (str(c.get("section", "")), str(c.get("ligne", "") or c.get("label", "") or c.get("control", "")))
                        grouped.setdefault(key, []).append((int(c.get("index") or 0), c.get("value")))
                for (section, ligne), vals in grouped.items():
                    out.append((section, ligne, [v for _, v in sorted(vals, key=lambda x: x[0])]))
                found = True
        for key, child in node.items():
            if key in ("__lignes__", "__champs__", "__page__"):
                continue
            if isinstance(child, (Mapping, list)):
                found = _rows_from_scraper_24(child, out) or found
    elif isinstance(node, list):
        for child in node:
            if isinstance(child, (Mapping, list)):
                found = _rows_from_scraper_24(child, out) or found
    return found


def _dedupe_pairs(values: list) -> list:
    # L'ancien extracteur comptait deux fois les <input> imbriqués ([115, 115]).
    if len(values) >= 2 and len(values) % 2 == 0 and values[0::2] == values[1::2]:
        return values[0::2]
    return values


def _rows_from_legacy(node: Any, out: list[Row], section: str = "", depth: int = 0) -> None:
    if depth > 8 or not isinstance(node, Mapping):
        return
    for key, child in node.items():
        if str(key).startswith("__"):
            continue
        if isinstance(child, list) and child and all(not isinstance(x, (Mapping, list)) for x in child):
            out.append((section, str(key), _dedupe_pairs(list(child))))
        elif isinstance(child, Mapping):
            _rows_from_legacy(child, out, section=str(key), depth=depth + 1)


def collect_rows(contenu: Any) -> list[Row]:
    rows: list[Row] = []
    if not _rows_from_scraper_24(contenu, rows):
        rows = []
        _rows_from_legacy(contenu, rows)
    return rows


# ---------------------------------------------------------------------------
# Marketing
# ---------------------------------------------------------------------------
_BAD_PRICE = ("revient", "achat", "cout", "concurr", "moyen", "marche")


def parse_marketing(contenu: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for section, ligne, values in collect_rows(contenu):
        text = norm(f"{section} {ligne}")
        nums = _numbers(values)
        strs = _strings(values)
        product = detect_product(ligne, section)

        if strs and not nums:
            if "axe" in text and "princip" in text:
                out["Axe_Principal"] = strs[0]
            elif "axe" in text and ("access" in text or "second" in text):
                out["Axe_Accessoire"] = strs[0]
            continue
        if not nums:
            continue

        is_price = "prix" in text and not any(b in text for b in _BAD_PRICE)
        is_plv = "plv" in text or "publicite produit" in text or ("publicit" in text and product is not None)
        is_brand = ("marque" in text and ("budget" in text or "publicit" in text)) and product is None

        if is_brand and not is_plv:
            if 0 <= nums[0] <= 1e8:
                out["Budget_Marque"] = nums[0]
        elif is_price or is_plv:
            prefix = "Prix_" if is_price and not is_plv else "Pub_"
            low, high = (10.0, 5000.0) if prefix == "Prix_" else (0.0, 1e7)
            if product is not None:
                if low <= nums[0] <= high:
                    out[f"{prefix}{product.upper()}"] = nums[0]
            elif len(nums) == len(PRODUCT_KEYS):
                for key, v in zip(PRODUCT_KEYS, nums):
                    if low <= v <= high:
                        out[f"{prefix}{key.upper()}"] = v
    return out


# ---------------------------------------------------------------------------
# RH
# ---------------------------------------------------------------------------
def _department(text: str) -> str | None:
    for code, words in _DEPARTMENTS:
        if any(re.search(rf"\b{w}", text) for w in words):
            return code
    return None


def parse_rh(contenu: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for section, ligne, values in collect_rows(contenu):
        text = norm(f"{section} {ligne}")
        nums = _numbers(values)
        if not nums:
            continue
        dept = _department(text)
        cat = next((name for name, words in _CATEGORIES if any(w in text for w in words)), None)
        if dept is None or cat is None:
            continue
        only_salary = any(w in text for w in ("salaire", "remuneration"))
        only_headcount = any(w in text for w in ("effectif", "nombre", "nb "))
        eff = sal = None
        if only_salary and not only_headcount:
            sal = nums[0]
        elif only_headcount and not only_salary:
            eff = nums[0]
        elif len(nums) >= 2:
            first, second = nums[0], nums[1]
            # Convention de l'écran : effectif puis salaire ; on corrige si l'ordre est inversé.
            eff, sal = (first, second) if first < second else (second, first)
        elif nums[0] >= 300:
            sal = nums[0]
        else:
            eff = nums[0]
        if eff is not None and 0 <= eff <= 500:
            out[f"Eff_{cat}_{dept}"] = int(round(eff))
        if sal is not None and 300 <= sal <= 100000:
            out[f"Sal_{cat}_{dept}"] = float(sal)
    return out


# ---------------------------------------------------------------------------
# Production : quantités à produire par produit
# ---------------------------------------------------------------------------
def parse_production(contenu: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for section, ligne, values in collect_rows(contenu):
        text = norm(f"{section} {ligne}")
        nums = _numbers(values)
        product = detect_product(ligne, section)
        if not nums or product is None:
            continue
        if any(w in text for w in ("quantit", "produire", "ordre", "lancement", "fabrication")) and not any(
            w in text for w in ("stock", "cout", "prix", "qualite", "capacite")
        ):
            if 0 <= nums[0] <= 1e6:
                out[product] = int(round(nums[0]))
    return out


# ---------------------------------------------------------------------------
# Contrôle de contenu : une page de décisions « vide » n'est PAS une extraction réussie
# ---------------------------------------------------------------------------
def _champ_utile(champ: Mapping[str, Any]) -> bool:
    if champ.get("name") == "search-query" or champ.get("id") == "search-query":
        return False
    if champ.get("section") or champ.get("ligne") or champ.get("control"):
        return True
    return champ.get("type") not in ("texte", "text", "search", None, "")


def decisions_utiles(contenu: Any) -> bool:
    """Vrai si le contenu scrapé contient réellement des champs/valeurs de formulaire.

    Ignore la barre de recherche et le sélecteur de période, seuls éléments présents quand
    le scraper est resté sur l'accueil au lieu d'entrer dans la page de décisions.
    """
    if isinstance(contenu, Mapping):
        for item in contenu.get("__lignes__") or []:
            if isinstance(item, Mapping) and any(v not in (None, "") for v in (item.get("valeurs") or [])):
                return True
        for champ in contenu.get("__champs__") or []:
            if isinstance(champ, Mapping) and _champ_utile(champ):
                return True
        return any(
            decisions_utiles(child) for key, child in contenu.items()
            if key not in ("__lignes__", "__champs__", "__page__") and isinstance(child, (Mapping, list))
        )
    if isinstance(contenu, list):
        return any(decisions_utiles(child) for child in contenu if isinstance(child, (Mapping, list)))
    return False


def _est_scrape_24(contenu: Any) -> bool:
    if isinstance(contenu, Mapping):
        if "__champs__" in contenu or "__lignes__" in contenu:
            return True
        return any(_est_scrape_24(c) for c in contenu.values() if isinstance(c, (Mapping, list)))
    if isinstance(contenu, list):
        return any(_est_scrape_24(c) for c in contenu if isinstance(c, (Mapping, list)))
    return False


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------
def parse_decisions(modules: Mapping[str, Any]) -> dict[str, Any]:
    """modules : {nom_module: contenu JSON}. Retourne les valeurs reconnues + un rapport lisible."""
    report: list[str] = []
    modules = dict(modules)
    for name in ("marketing", "rh", "production"):
        contenu = modules.get(name)
        if contenu and _est_scrape_24(contenu) and not decisions_utiles(contenu):
            report.append(
                f"Décisions `{name}` : extraction VIDE en base (le scraper n'est pas entré dans la page de décisions) — "
                "relancer le scraper v2.4.1 (`--modules decisions --force`)."
            )
            modules[name] = None
    marketing = parse_marketing(modules.get("marketing")) if modules.get("marketing") else {}
    rh = parse_rh(modules.get("rh")) if modules.get("rh") else {}
    production = parse_production(modules.get("production")) if modules.get("production") else {}
    for name, parsed in (("marketing", marketing), ("rh", rh), ("production", production)):
        if modules.get(name) and not parsed:
            report.append(
                f"Décisions `{name}` présentes en base mais aucun champ reconnu : format à calibrer "
                "(envoyer un échantillon JSON du module)."
            )
        elif parsed:
            report.append(f"Décisions `{name}` scrapées : {len(parsed)} champ(s) reconnu(s).")
    return {"marketing": marketing, "rh": rh, "production": production, "report": report}
