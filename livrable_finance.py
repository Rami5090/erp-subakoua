"""Livrable Finance mensuel Subakoua (Mission spécifique Finance).

Moteur PUR : aucune dépendance Streamlit / SQL.
Il transforme les données déjà extraites (bilan, ratios, ventes, coûts,
stocks, flux bancaires) en tableaux prêts à recopier dans le document PDF.

Convention :
- montants en Decimal (euros), quantités en float/int.
- chaque builder retourne (valeurs, warnings) : on ne masque jamais un
  SPECIMEN, un fallback Juillet ou une hypothèse de mapping prix->produit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence
import re
import unicodedata

# Mapping stable observé : prix catalogue -> produit.
# Hypothèse explicite : si les prix changent de gamme, le mapping doit être revu.
PRIX_TO_PRODUIT = {
    130: "Shorty 3",
    205: "Integral 3",
    240: "Shorty 5",
    310: "Integral 5",
    360: "Integral 7",
}

PRODUCT_ID_TO_NAME = {
    "SHORTY_C": "Shorty 3",
    "MONO_C": "Integral 3",
    "SHORTY_T": "Shorty 5",
    "MONO_T": "Integral 5",
    "MONO_F": "Integral 7",
}


def _d(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        s = str(value).replace("\u202f", "").replace("\xa0", "").replace(" ", "").replace("€", "").replace(",", ".")
        if s in ("", "-"):
            return default
        return Decimal(s)
    except Exception:
        return default


@dataclass(frozen=True)
class BilanDiagnostic:
    banque: Decimal
    epargne: Decimal
    disponibilites: Decimal
    actions: Decimal
    obligations: Decimal
    actifs_exploitation: Decimal
    dettes_exploitation: Decimal
    bfre: Decimal
    actif_circulant_net: Decimal
    dettes_totales: Decimal
    capitaux_propres: Decimal
    dette_financiere: Decimal
    stocks_matieres: Decimal
    stocks_produits: Decimal
    creances_clients: Decimal
    acomptes_is: Decimal
    tva_deductible: Decimal
    tva_collectee: Decimal
    warnings: tuple[str, ...] = ()


def build_bilan(bilan_rows: Sequence[Mapping[str, Any]]) -> BilanDiagnostic:
    """Parse le bilan détaillé 19 lignes (Net exercice N / Exercice N)."""
    actif: dict[str, Decimal] = {}
    passif: dict[str, Decimal] = {}
    for row in bilan_rows:
        a = str(row.get("Actif", "")).strip().lower()
        p = str(row.get("Passif", "")).strip().lower()
        va = _d(row.get("Net exercice N", row.get("Net")), None)  # type: ignore[arg-type]
        vp = _d(row.get("Exercice N", row.get("Montant")), None)  # type: ignore[arg-type]
        if a:
            actif[a] = va if va is not None else Decimal("0")
        if p:
            passif[p] = vp if vp is not None else Decimal("0")

    def ga(*names: str) -> Decimal:
        for n in names:
            if n in actif:
                return actif[n]
        return Decimal("0")

    def gp(*names: str) -> Decimal:
        for n in names:
            if n in passif:
                return passif[n]
        return Decimal("0")

    banque = ga("banque")
    epargne = ga("compte épargne", "compte epargne")
    stocks_mat = ga("matières", "matieres")
    stocks_prod = ga("produits finis")
    clients = ga("clients")
    acomptes = ga("acomptes sur is")
    tva_ded = ga("tva déductible", "tva deductible")
    tva_col = gp("tva collectée", "tva collectee")
    fournisseurs = gp("fournisseurs")
    capitaux = gp("total capitaux propres")
    dette_fin = gp("emprunts mensualisés contractés", "emprunts mensualises contractes") + gp("emprunts libres")
    actif_circ = ga("total actif circulant")
    dettes_tot = gp("total dettes")

    actifs_exp = stocks_mat + stocks_prod + clients + acomptes + tva_ded
    # en-cours alpha/beta inclus s'ils existent
    actifs_exp += ga("en-cours alpha", "en-cours beta", "en-cours")
    dettes_exp = fournisseurs + tva_col + gp("impôt sur les bénéfices", "impot sur les benefices")
    warnings: list[str] = []
    if not bilan_rows:
        warnings.append("Bilan détaillé vide : document SPECIMEN ou non acheté.")
    return BilanDiagnostic(
        banque=banque, epargne=epargne, disponibilites=banque + epargne,
        actions=ga("actions"), obligations=ga("obligations"),
        actifs_exploitation=actifs_exp, dettes_exploitation=dettes_exp,
        bfre=actifs_exp - dettes_exp,
        actif_circulant_net=actif_circ, dettes_totales=dettes_tot,
        capitaux_propres=capitaux, dette_financiere=dette_fin,
        stocks_matieres=stocks_mat, stocks_produits=stocks_prod,
        creances_clients=clients, acomptes_is=acomptes,
        tva_deductible=tva_ded, tva_collectee=tva_col,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class MargeLigne:
    produit: str
    prix_ht: Decimal
    remise_a: Decimal
    remise_b: Decimal
    cout_variable: Decimal
    marge_a: Decimal
    marge_b: Decimal
    taux_a: Decimal | None
    taux_b: Decimal | None


def build_marges(
    prix_catalogue: Mapping[str, Decimal],
    remise_taux_a: Decimal = Decimal("0"),
    remise_taux_b: Decimal = Decimal("0.20"),
    couts_unitaires: Mapping[str, Decimal] | None = None,
    quantites: Mapping[str, float] | None = None,
) -> tuple[list[MargeLigne], tuple[str, ...]]:
    warnings: list[str] = []
    if couts_unitaires is None:
        warnings.append("Coûts unitaires absents : marges non calculables.")
        couts_unitaires = {}
    lignes: list[MargeLigne] = []
    for produit in ["Shorty 3", "Integral 3", "Shorty 5", "Integral 5", "Integral 7"]:
        prix = _d(prix_catalogue.get(produit, 0))
        ra = (prix * remise_taux_a).quantize(Decimal("0.01")) if prix else Decimal("0")
        rb = (prix * remise_taux_b).quantize(Decimal("0.01")) if prix else Decimal("0")
        cout = _d(couts_unitaires.get(produit)) if couts_unitaires.get(produit) is not None else None
        if cout is None:
            warnings.append(f"Coût manquant pour {produit} (pas de production dans l'étude de référence).")
            cout = Decimal("0")
        ma = prix - ra - cout
        mb = prix - rb - cout
        ta = (ma / prix).quantize(Decimal("0.0001")) if prix else None
        tb = (mb / prix).quantize(Decimal("0.0001")) if prix else None
        lignes.append(MargeLigne(produit, prix, ra, rb, cout, ma, mb, ta, tb))
    return lignes, tuple(warnings)


def couts_from_cost_study(payload: Mapping[str, Any]) -> tuple[dict[str, Decimal], tuple[str, ...]]:
    """Coût variable unitaire direct par produit depuis productionCostDetailStudy.

    Somme matières + ateliers (+ conditionnement quand présent), divisée par
    le nombre produit. Les indirects (dotations, maintenance, masse salariale)
    sont exclus : ce ne sont pas des coûts variables unitaires.
    """
    warnings: list[str] = []
    out: dict[str, Decimal] = {}
    details = payload.get("productsProductionCostDetails", []) if isinstance(payload, Mapping) else []
    for prod in details:
        name = PRODUCT_ID_TO_NAME.get(str(prod.get("productRef", "")), str(prod.get("productRef", "")))
        n = prod.get("producedProductsNumber") or 0
        if not n:
            warnings.append(f"{name} : production nulle dans l'étude de référence, coût non calculable.")
            continue
        total = Decimal("0")
        dc = prod.get("directCostDetail", {}) or {}
        for ph in dc.get("phasesCostDetails", []) or []:
            for m in ph.get("materialsCosts", []) or []:
                total += _d(m.get("totalCost"))
            for w in ph.get("workshopsCosts", []) or []:
                total += _d(w.get("totalCost"))
        fp = prod.get("finalProductionPhaseCostDetail", {}) or {}
        for w in fp.get("workshopsCosts", []) or []:
            total += _d(w.get("totalCost"))
        # Si le conditionnement est absent du payload (cas observé en Juillet),
        # on l'estime à 1,60 € (1,5-1,7 observé) avec avertissement explicite.
        has_cond = bool((fp.get("workshopsCosts", []) or []))
        if not has_cond:
            total += Decimal("1.60") * Decimal(str(n))
            warnings.append(f"{name} : conditionnement absent du payload, estimé à 1,60 €/u.")
        out[name] = (total / Decimal(str(n))).quantize(Decimal("0.01"))
    return out, tuple(warnings)


@dataclass(frozen=True)
class LivrableFinance:
    periode: str
    bilan: BilanDiagnostic
    ca_mensuel: Decimal
    resultat_avant_impot_mensuel: Decimal
    ca_cumule: Decimal
    resultat_cumule: Decimal
    marges: tuple[MargeLigne, ...] = ()
    stocks_quantites: Mapping[str, Any] = field(default_factory=dict)
    flux_banque: Mapping[str, Decimal] = field(default_factory=dict)
    creances: Mapping[str, Decimal] = field(default_factory=dict)
    couts_source_periode: str = ""
    warnings: tuple[str, ...] = ()

    def markdown(self) -> str:
        lines = [f"# Livrable Finance — {self.periode}", ""]
        b = self.bilan
        lines += ["## 1. Diagnostic bilan",
                  f"- Banque : {b.banque} €", f"- Épargne : {b.epargne} €",
                  f"- Disponibilités : {b.disponibilites} €",
                  f"- Actifs d'exploitation : {b.actifs_exploitation} €",
                  f"- Dettes d'exploitation : {b.dettes_exploitation} €",
                  f"- BFRE : {b.bfre} €",
                  f"- Capitaux propres : {b.capitaux_propres} €",
                  f"- Dette financière : {b.dette_financiere} €", ""]
        lines += ["## 2. Diagnostic résultat",
                  f"- CA mensuel : {self.ca_mensuel} €",
                  f"- Résultat avant impôt mensuel : {self.resultat_avant_impot_mensuel} €",
                  f"- CA cumulé : {self.ca_cumule} €",
                  f"- Résultat cumulé : {self.resultat_cumule} €", ""]
        lines += [f"## 3. Marges (coûts source : {self.couts_source_periode})",
                  "| Produit | Prix HT | Remise A | Remise B | Coût var. | Marge A | Marge B |",
                  "|---|---|---|---|---|---|---|"]
        for m in self.marges:
            lines.append(f"| {m.produit} | {m.prix_ht} | {m.remise_a} | {m.remise_b} | {m.cout_variable} | {m.marge_a} | {m.marge_b} |")
        lines += [""]
        if self.warnings:
            lines += ["## Avertissements", *[f"- {w}" for w in self.warnings], ""]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Remplissage automatique du PDF « Mission spécifique Finance ».
# Le template contient 155 champs texte (noms avec accents). On matche les
# noms de façon tolérante (accents/casse/espaces) pour ne pas dépendre de
# l'encodage exact du champ.
# ---------------------------------------------------------------------------

def fmt_fr(value: Any) -> str:
    """Format français : espace milliers, virgule décimale, 2 décimales."""
    d = _d(value)
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    neg = q < 0
    s = f"{abs(q):,.2f}"  # 3,082,581.20
    s = s.replace(",", " ").replace(".", ",")
    return f"-{s}" if neg else s


def fmt_pct(ratio: Any) -> str:
    """Ratio 0.4234 -> '42,34 %'. None -> ''."""
    if ratio is None:
        return ""
    pct = (_d(ratio) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = f"{pct:,}".replace(",", " ").replace(".", ",")
    return f"{s} %"


def _norm_field(name: Any) -> str:
    t = unicodedata.normalize("NFKD", str(name))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().casefold()


#: objectRef de l'étude de coûts -> composant du tableau des marges.
COUT_REF_TO_COMPOSANT = {
    "NEOPRENE_3": "neoprene", "NEOPRENE_5": "neoprene", "NEOPRENE_7": "neoprene",
    "REINFORCEMENTS": "renforts", "SLEEVES": "manchons", "CLOSURES": "fermetures",
    "CUTTING": "decoupe", "ASSEMBLY": "assemblage", "PACKAGING": "conditionnement",
}

COMPOSANTS_MARGES = ("neoprene", "renforts", "manchons", "fermetures",
                     "decoupe", "assemblage", "conditionnement", "autres")


def couts_detail_from_cost_study(
    payload: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Decimal]], tuple[str, ...]]:
    """Détail unitaire par produit et par composant (matières + ateliers).

    Retourne {produit: {composant: coût_unitaire}}. Les indirects sont exclus.
    Si le conditionnement est absent du payload, estimé à 1,60 €/u (signalé).
    Production nulle -> produit absent + warning (laisser le champ PDF vide).
    """
    warnings: list[str] = []
    out: dict[str, dict[str, Decimal]] = {}
    details = payload.get("productsProductionCostDetails", []) if isinstance(payload, Mapping) else []
    for prod in details:
        name = PRODUCT_ID_TO_NAME.get(str(prod.get("productRef", "")), str(prod.get("productRef", "")))
        n = prod.get("producedProductsNumber") or 0
        if not n:
            warnings.append(f"{name} : production nulle dans l'étude de référence, coût non calculable.")
            continue
        tot: dict[str, Decimal] = {c: Decimal("0") for c in COMPOSANTS_MARGES}
        dc = prod.get("directCostDetail", {}) or {}
        for ph in dc.get("phasesCostDetails", []) or []:
            for m in ph.get("materialsCosts", []) or []:
                comp = COUT_REF_TO_COMPOSANT.get(str(m.get("objectRef", "")))
                if comp:
                    tot[comp] += _d(m.get("totalCost"))
            for w in ph.get("workshopsCosts", []) or []:
                comp = COUT_REF_TO_COMPOSANT.get(str(w.get("objectRef", "")))
                if comp:
                    tot[comp] += _d(w.get("totalCost"))
        fp = prod.get("finalProductionPhaseCostDetail", {}) or {}
        cond_seen = False
        for w in fp.get("workshopsCosts", []) or []:
            comp = COUT_REF_TO_COMPOSANT.get(str(w.get("objectRef", "")), "conditionnement")
            tot[comp] += _d(w.get("totalCost"))
            cond_seen = True
        if not cond_seen:
            tot["conditionnement"] += Decimal("1.60") * Decimal(str(n))
            warnings.append(f"{name} : conditionnement absent du payload, estimé à 1,60 €/u.")
        denom = Decimal(str(n))
        out[name] = {c: (v / denom).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for c, v in tot.items()}
    return out, tuple(warnings)


def pdf_field_values(
    livrable: LivrableFinance,
    couts_detail: Mapping[str, Mapping[str, Any]] | None = None,
    prix_catalogue: Mapping[str, Any] | None = None,
    remise_taux_b: Any = Decimal("0.20"),
    taux_b_key: Any = None,
) -> dict[str, str]:
    """Construit les valeurs logiques {nom_de_champ_pdf: texte} pour le mois.

    Seules les cellules calculables sont renseignées ; le rédactionnel
    (Réponses, Commentaires, Nos conclusions, Nom/Prénom) n'est jamais touché.
    Les résultats nets/CAF non achetés (SPECIMEN) sont vidés ("") pour ne pas
    laisser les chiffres périmés d'un mois précédent dans le PDF.
    """
    couts_detail = couts_detail or {}
    prix_catalogue = prix_catalogue or {m.produit: m.prix_ht for m in livrable.marges}
    taux_b = _d(remise_taux_b)
    b = livrable.bilan
    v: dict[str, str] = {
        # --- P.4 diagnostic bilan ---
        "Compte banqueRow1": fmt_fr(b.banque),
        "Compte épargneRow1": fmt_fr(b.epargne),
        "DisponibilitésRow1": fmt_fr(b.disponibilites),
        "Portefeuille actionsRow1": fmt_fr(b.actions),
        "Portefeuille obligationsRow1": fmt_fr(b.obligations),
        "Portefeuille titresRow1": fmt_fr(Decimal("0")),
        "Actifs circulants dexploitationRow1": fmt_fr(b.actifs_exploitation),
        "Dettes à CT dexploitationRow1": fmt_fr(b.dettes_exploitation),
        "B F R ERow1": fmt_fr(b.bfre),
        "Actifs circulantsRow1": fmt_fr(b.actif_circulant_net),
        "Dettes à court termeRow1": fmt_fr(b.dettes_totales),
        "B F RRow1": fmt_fr(b.actif_circulant_net - b.dettes_totales + b.dette_financiere)
        if (b.actif_circulant_net or b.dettes_totales) else fmt_fr(b.bfre),
        # --- P.5 diagnostic résultat (mensuel ; net/CAF vidés si SPECIMEN) ---
        "Chiffre daffairesRow1": fmt_fr(livrable.ca_mensuel),
        "Capitaux propresRow1": fmt_fr(b.capitaux_propres),
        "Capitaux propresRow1_2": fmt_fr(b.capitaux_propres),
        "Dettes financièresRow1": fmt_fr(b.dette_financiere),
        "Dettes financièresRow1_2": fmt_fr(b.dette_financiere),
        "Résultat netRow1": "",
        "Résultat netRow1_2": "",
        "C A FRow1": "",
        "Taux de ProfitabilitéRow1": "",
        "Taux de rentabilitéRow1": "",
        "Ratio dindépendance financièreRow1": fmt_pct(b.dette_financiere / b.capitaux_propres) if b.capitaux_propres else "",
        # NB : le template écrit "dautofinancem ent" (espace parasite) — on garde son orthographe exacte.
        "Ratio de capacité dautofinancem entRow1": "",
    }
    if taux_b_key is not None:
        pass
    for m in livrable.marges:
        p = m.produit
        det = couts_detail.get(p, {}) or {}
        prix = _d(prix_catalogue.get(p, m.prix_ht))
        ra = (prix * Decimal("0")).quantize(Decimal("0.01"))
        rb = (prix * taux_b).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        comp = {c: fmt_fr(det.get(c, Decimal("0"))) for c in COMPOSANTS_MARGES if c != "autres"}
        cout_a = _d(det.get("neoprene")) + _d(det.get("renforts")) + _d(det.get("manchons")) + _d(det.get("fermetures")) + _d(det.get("decoupe")) + _d(det.get("assemblage")) + _d(det.get("conditionnement")) + ra
        cout_b = _d(det.get("neoprene")) + _d(det.get("renforts")) + _d(det.get("manchons")) + _d(det.get("fermetures")) + _d(det.get("decoupe")) + _d(det.get("assemblage")) + _d(det.get("conditionnement")) + rb
        marge_a = prix - cout_a
        marge_b = prix - cout_b
        t_a = fmt_pct(marge_a / prix) if prix else ""
        t_b = fmt_pct(marge_b / prix) if prix else ""
        has_cost = p in couts_detail
        v.update({
            f"{p}Prix de vente HT a": fmt_fr(prix),
            f"{p}Remise canal A b": fmt_fr(ra),
            f"{p}Remise canal B c": fmt_fr(rb),
            f"{p}Néoprène d": comp.get("neoprene", "") if has_cost else "",
            f"{p}Renforts e": comp.get("renforts", "") if has_cost else "",
            f"{p}Manchons f": comp.get("manchons", "") if has_cost else "",
            f"{p}Fermetures g": comp.get("fermetures", "") if has_cost else "",
            f"{p}Découpe h": comp.get("decoupe", "") if has_cost else "",
            f"{p}Assemblage i": comp.get("assemblage", "") if has_cost else "",
            f"{p}Conditionnement j": comp.get("conditionnement", "") if has_cost else "",
            f"{p}Autres frais variables k": fmt_fr(Decimal("0")) if has_cost else "",
            f"{p}Coût Variable Un canal A l bdefghijk": fmt_fr(cout_a) if has_cost else "",
            f"{p}Coût Variable Un canal B m cdefghijk": fmt_fr(cout_b) if has_cost else "",
            f"{p}Marge unitaire canal A na l": fmt_fr(marge_a) if has_cost else "",
            f"{p}Marge unitaire canal B oa m": fmt_fr(marge_b) if has_cost else "",
            f"{p}Prix de vente brut HT a": fmt_fr(prix),
            f"{p}Marge unitaire canal A b": fmt_fr(marge_a) if has_cost else "",
            f"{p}Taux de marge canal A ba": t_a if has_cost else "",
            f"{p}Marge unitaire canal B c": fmt_fr(marge_b) if has_cost else "",
            f"{p}Taux de marge canal B ca": t_b if has_cost else "",
        })
    return v


# ---------------------------------------------------------------------------
# Documents requis pour un livrable complet.
# Si une étude manque (SPECIMEN / non achetée), la cellule correspondante
# du PDF reste vide ou en fallback — d'où la liste ci-dessous, avec le
# « pourquoi » qui dit exactement à quoi elle sert dans le document.
# ---------------------------------------------------------------------------

REQUIRED_STUDIES: tuple[dict[str, str], ...] = (
    {"study_id": "productionCostDetailStudy",
     "pourquoi": "Marges p.6-7 : coûts variables unitaires par produit"},
    {"study_id": "peeedscrdl",
     "pourquoi": "Résultat p.5 : résultat net, AACE, dotations (détaillé)"},
    {"study_id": "peeedscrsm",
     "pourquoi": "Résultat p.5 : résultat net (simplifié, alternative au détaillé)"},
    {"study_id": "peeedsftt",
     "pourquoi": "Budget p.12 : flux de trésorerie, ΔBFR, CAF"},
    {"study_id": "peeefstvp",
     "pourquoi": "Coordination p.11 + budget : TVA à payer"},
    {"study_id": "peeefsisp",
     "pourquoi": "Coordination p.11 + budget : impôt sur les sociétés"},
    {"study_id": "peeedcahm",
     "pourquoi": "Marges + BFR : achats et variations de stocks"},
    {"study_id": "peeedcahsx",
     "pourquoi": "Résultat : autres achats et charges externes (AACE)"},
    {"study_id": "peeedccscl",
     "pourquoi": "BFR + coordination p.10 : créances clients et délais"},
    {"study_id": "peeedsbidt",
     "pourquoi": "Diagnostic p.4 : bilan détaillé"},
    {"study_id": "peeedccg",
     "pourquoi": "Résultat p.5 : chiffre d'affaires"},
    {"study_id": "bankStatements",
     "pourquoi": "Trésorerie + budget : relevé bancaire et flux"},
)


def missing_studies(
    available_ids: Any,
    catalog: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Études requises absentes de la période, avec libellé/prix/motif.

    `available_ids` : study_id lisibles (read_allowed) sur la période.
    `catalog` : {study_id: {"label": str, "price": float}} (ex. profils documentaires).
    """
    have = set(available_ids or [])
    out: list[dict[str, Any]] = []
    for req in REQUIRED_STUDIES:
        sid = req["study_id"]
        if sid in have:
            continue
        info = catalog.get(sid, {}) if isinstance(catalog, Mapping) else {}
        try:
            price = float(info.get("price", 0) or 0)
        except Exception:
            price = 0.0
        out.append({
            "study_id": sid,
            "label": str(info.get("label", sid)),
            "price": price,
            "pourquoi": req["pourquoi"],
        })
    return out


def fill_pdf(template: Any, output_path: str, values: Mapping[str, str]) -> tuple[int, list[str]]:
    """Remplit les champs du PDF template et écrit output_path.

    `template` : chemin (str/Path) du PDF ou son contenu (bytes).
    Retourne (nb_champs_renseignés, champs_logiques_non_trouvés).
    Le matching est tolérant aux accents/casse/espaces.
    """
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    if isinstance(template, (bytes, bytearray)):
        reader = PdfReader(BytesIO(bytes(template)))
        writer = PdfWriter(clone_from=reader)
    else:
        reader = PdfReader(str(template))
        writer = PdfWriter(clone_from=str(template))
    fields = reader.get_fields() or {}
    norm_index: dict[str, str] = {_norm_field(k): k for k in fields.keys()}
    matched: dict[str, str] = {}
    missing: list[str] = []
    for logical, text in values.items():
        key = norm_index.get(_norm_field(logical))
        if key is None:
            missing.append(logical)
        else:
            matched[key] = text
    for page in writer.pages:
        writer.update_page_form_field_values(page, matched)
    with open(output_path, "wb") as f:
        writer.write(f)
    return len(matched), missing
