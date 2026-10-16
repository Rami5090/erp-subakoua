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
from decimal import Decimal
from typing import Any, Mapping, Sequence

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
