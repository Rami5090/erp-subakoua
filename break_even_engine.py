"""Moteur d'analyse quantitative : point d'équilibre, marge et mix optimal.

Aucune dépendance Streamlit ni SQL : les calculs sont testables et
réutilisables par le centre de décision comme par la page d'analyse.

Vocabulaire
-----------
* **coût variable unitaire** : ce qui varie avec le volume produit (matières,
  ateliers, conditionnement). Les charges fixes (masse salariale, assurances,
  amortissements, marketing…) ne sont pas dedans.
* **marge sur coût variable unitaire** : prix HT − coût variable unitaire.
* **point d'équilibre** : le volume (et le chiffre d'affaires) à partir duquel
  la marge sur coût variable couvre exactement les charges fixes.
* **mix optimal** : la répartition des volumes qui maximise la marge sous la
  contrainte de capacité industrielle (ateliers).

Aucune valeur de calage n'est injectée : si une donnée manque (coût unitaire,
prix, charge fixe), le moteur le signale au lieu de combler le trou.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

ATELIERS = ("Decoupe", "Assemblage", "Conditionnement")

#: Modes d'imputation des charges fixes sur les produits.
ALLOCATIONS = ("revenue", "units")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
            return default
        return out
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ProductEconomics:
    """Économie unitaire d'un produit : prix, coût variable, volumes."""

    key: str
    name: str
    price: float
    unit_variable_cost: float
    current_units: float = 0.0
    demand_units: float | None = None

    @property
    def margin_per_unit(self) -> float:
        return self.price - self.unit_variable_cost

    @property
    def margin_ratio(self) -> float | None:
        """Taux de marge sur coût variable (marge / prix HT)."""
        return (self.margin_per_unit / self.price) if self.price > 0 else None

    @property
    def revenue(self) -> float:
        return self.price * self.current_units

    @property
    def margin(self) -> float:
        return self.margin_per_unit * self.current_units


@dataclass(frozen=True)
class MixEntry:
    """Part d'un produit dans un mix de production."""

    key: str
    name: str
    units: float
    revenue: float
    margin: float
    minutes: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class BreakEvenResult:
    """Résultat complet d'une analyse quantitative."""

    products: tuple[ProductEconomics, ...]
    fixed_costs: float
    reference_mix: Mapping[str, float]
    break_even_units: float | None
    break_even_revenue: float | None
    weighted_margin_per_unit: float | None
    weighted_margin_ratio: float | None
    current_units: float
    current_revenue: float
    current_margin: float
    margin_of_safety_units: float | None
    margin_of_safety_revenue: float | None
    margin_of_safety_ratio: float | None
    fixed_cost_allocation: Mapping[str, float]
    break_even_units_by_product: Mapping[str, float]
    capacity_minutes: Mapping[str, float] = field(default_factory=dict)
    max_margin_under_capacity: float | None = None
    equilibrium_reachable: bool | None = None
    capacity_gap: float | None = None
    optimal_mix: tuple[MixEntry, ...] = ()
    optimal_mix_units: float = 0.0
    optimal_mix_revenue: float = 0.0
    optimal_mix_margin: float = 0.0
    allocation_method: str = "revenue"
    objective: str = "margin"
    warnings: tuple[str, ...] = ()


def _reference_mix(products: Sequence[ProductEconomics], mix: Mapping[str, float] | None) -> dict[str, float]:
    """Mix de référence : explicite, sinon volumes courants, sinon demande."""
    if mix:
        return {p.key: max(0.0, _num(mix.get(p.key))) for p in products}
    courant = {p.key: max(0.0, _num(p.current_units)) for p in products}
    if sum(courant.values()) > 0:
        return courant
    demande = {p.key: max(0.0, _num(p.demand_units)) for p in products}
    if sum(demande.values()) > 0:
        return demande
    return {p.key: 0.0 for p in products}


def allocate_fixed_costs(
    products: Sequence[ProductEconomics],
    fixed_costs: float,
    mix: Mapping[str, float],
    method: str = "revenue",
) -> dict[str, float]:
    """Répartit les charges fixes sur les produits.

    `revenue` : au poids du chiffre d'affaires (convention la plus usuelle,
    elle suit la contribution au résultat) ; `units` : au poids des volumes.
    """
    fc = max(0.0, _num(fixed_costs))
    if method not in ALLOCATIONS:
        method = "revenue"
    poids: dict[str, float] = {}
    for p in products:
        qte = max(0.0, _num(mix.get(p.key)))
        if method == "units":
            poids[p.key] = qte
        else:
            poids[p.key] = p.price * qte
    total = sum(poids.values())
    if total <= 0:
        # Aucune base d'imputation : les charges fixes restent non ventilées.
        return {p.key: 0.0 for p in products}
    return {p.key: fc * poids[p.key] / total for p in products}


def optimize_mix(
    products: Sequence[ProductEconomics],
    times: Mapping[str, Mapping[str, float]],
    capacity_minutes: Mapping[str, float],
    demand_caps: Mapping[str, float] | None = None,
    objective: str = "margin",
) -> tuple[tuple[MixEntry, ...], tuple[str, ...]]:
    """Mix optimisé sous contrainte de capacité d'atelier.

    Deux objectifs sont possibles :

    * `margin` : maximiser la **marge sur coût variable** ;
    * `volume` : maximiser le **volume vendu**.

    Heuristique gloutonne : les produits sont servis par **marge par fraction
    de capacité consommée** décroissante (ou par volume par fraction de
    capacité, pour l'objectif `volume`) ; chaque allocation est limitée par
    *tous* les ateliers, donc le mix returned est toujours réalisable (aucune
    capacité dépassée).

    Ce n'est pas un optimum exact d'un programme linéaire, mais une solution
    réalisable et proche : le résultat est annoncé comme tel.
    """
    warnings: list[str] = []
    objectif = "volume" if str(objective).lower() == "volume" else "margin"
    rest_cap = {w: max(0.0, _num(capacity_minutes.get(w))) for w in capacity_minutes}
    rest_demande: dict[str, float] = {}
    for p in products:
        cap = (demand_caps or {}).get(p.key, p.demand_units)
        rest_demande[p.key] = float("inf") if cap is None else max(0.0, _num(cap))

    # Coût en minutes d'atelier par unité de produit.
    minutes_unitaire: dict[str, dict[str, float]] = {}
    for p in products:
        t = times.get(p.name) or times.get(p.key) or {}
        minutes_unitaire[p.key] = {
            w: max(0.0, _num(t.get(w))) for w in ATELIERS if _num(t.get(w)) > 0
        }

    allocation: dict[str, float] = {p.key: 0.0 for p in products}
    restants = [p for p in products if p.margin_per_unit > 0 and rest_demande[p.key] > 0]
    garde = 0
    while restants and garde < 200:
        garde += 1

        def score(p: ProductEconomics) -> float:
            """Score de priorité du produit pour l'objectif courant.

            Pour chaque atelier, la fraction de la capacité résiduelle que le
            produit consommerait par unité ; on retient l'atelier le plus
            disputé. Un produit qui ne consomme aucun atelier passe devant.
            """
            m = minutes_unitaire[p.key]
            pression = 0.0
            for w, t in m.items():
                dispo = rest_cap.get(w, 0.0)
                if t > 0 and dispo > 0:
                    pression = max(pression, t / dispo)
            if pression <= 0:
                return float("inf")
            if objectif == "volume":
                # Maximiser le volume : on sert d'abord ce qui consomme le
                # moins de capacité rare par unité vendue.
                return 1.0 / pression
            # Maximiser la marge : marge par fraction de capacité consommée.
            return p.margin_per_unit / pression

        restants.sort(key=lambda p: (-score(p), p.key))
        choisi = restants[0]
        # Quantité servie : limitée par la demande et par chaque atelier restant.
        maxi = rest_demande[choisi.key]
        for w, dispo in rest_cap.items():
            t = minutes_unitaire[choisi.key].get(w, 0.0)
            if t > 0:
                maxi = min(maxi, dispo / t)
        if maxi <= 1e-9:
            restants = [p for p in restants if p.key != choisi.key]
            continue
        allocation[choisi.key] += maxi
        rest_demande[choisi.key] -= maxi
        for w, dispo in rest_cap.items():
            rest_cap[w] = dispo - maxi * minutes_unitaire[choisi.key].get(w, 0.0)
        if rest_demande[choisi.key] <= 1e-9:
            restants = [p for p in restants if p.key != choisi.key]

    entrees: list[MixEntry] = []
    for p in products:
        qte = allocation.get(p.key, 0.0)
        if qte <= 0:
            continue
        charge = {w: qte * minutes_unitaire[p.key].get(w, 0.0) for w in ATELIERS}
        entrees.append(MixEntry(
            key=p.key,
            name=p.name,
            units=qte,
            revenue=p.price * qte,
            margin=p.margin_per_unit * qte,
            minutes=charge,
        ))
    if not entrees and any(p.margin_per_unit > 0 for p in products):
        warnings.append("Aucune allocation possible : capacité d'atelier nulle sur tous les produits.")
    return tuple(entrees), tuple(warnings)


def compute_break_even(
    products: Sequence[ProductEconomics],
    fixed_costs: Any,
    mix: Mapping[str, float] | None = None,
    allocation: str = "revenue",
    times: Mapping[str, Mapping[str, float]] | None = None,
    capacity_minutes: Mapping[str, float] | None = None,
    demand_caps: Mapping[str, float] | None = None,
    objective: str = "margin",
) -> BreakEvenResult:
    """Point d'équilibre, marge de sécurité et mix sous contrainte de capacité."""
    warnings: list[str] = []
    prods = list(products)
    fc = max(0.0, _num(fixed_costs))

    for p in prods:
        if p.price <= 0:
            warnings.append(f"{p.name} : prix nul ou manquant — marge non calculable.")
        elif p.unit_variable_cost <= 0:
            warnings.append(f"{p.name} : coût variable unitaire manquant — marge surestimée.")
        if p.margin_per_unit < 0:
            warnings.append(
                f"{p.name} : marge unitaire négative ({p.margin_per_unit:,.2f} €) — "
                "ce produit détruit de la marge sur coût variable."
            )

    ref = _reference_mix(prods, mix)
    qte_totale = sum(ref.values())
    ca_ref = sum(p.price * ref.get(p.key, 0.0) for p in prods)
    marge_ref = sum(p.margin_per_unit * ref.get(p.key, 0.0) for p in prods)

    if qte_totale <= 0:
        warnings.append(
            "Mix de référence absent (aucun volume courant ni demande) : "
            "le point d'équilibre ne peut pas être calculé."
        )
        return BreakEvenResult(
            products=tuple(prods), fixed_costs=fc, reference_mix=ref,
            break_even_units=None, break_even_revenue=None,
            weighted_margin_per_unit=None, weighted_margin_ratio=None,
            current_units=0.0, current_revenue=0.0, current_margin=0.0,
            margin_of_safety_units=None, margin_of_safety_revenue=None,
            margin_of_safety_ratio=None,
            fixed_cost_allocation={p.key: 0.0 for p in prods},
            break_even_units_by_product={p.key: 0.0 for p in prods},
            allocation_method=allocation, warnings=tuple(warnings),
        )

    marge_unitaire_ponderee = marge_ref / qte_totale
    taux_pondere = (marge_ref / ca_ref) if ca_ref > 0 else None

    if fc <= 0:
        equilibre_atteignable = True
        seuil_unites = 0.0
        seuil_ca = 0.0
    else:
        seuil_unites = fc / marge_unitaire_ponderee if marge_unitaire_ponderee > 0 else None
        seuil_ca = fc / taux_pondere if (taux_pondere and taux_pondere > 0) else None
        equilibre_atteignable = None
    if marge_unitaire_ponderee <= 0 and fc > 0:
        warnings.append(
            "Marge sur coût variable pondérée nulle ou négative : aucun volume ne permet "
            "d'atteindre l'équilibre avec ce mix."
        )

    # Imputation des charges fixes et seuil par produit.
    imputation = allocate_fixed_costs(prods, fc, ref, method=allocation)
    seuil_par_produit: dict[str, float] = {}
    for p in prods:
        part = imputation.get(p.key, 0.0)
        if p.margin_per_unit > 0:
            seuil_par_produit[p.key] = part / p.margin_per_unit
        else:
            seuil_par_produit[p.key] = float("inf")
            if part > 0:
                warnings.append(
                    f"{p.name} : charges fixes imputées mais marge unitaire nulle — seuil non défini."
                )

    # Volumes courants (s'ils sont fournis) pour la marge de sécurité.
    courant = {p.key: max(0.0, _num(p.current_units)) for p in prods}
    qte_courante = sum(courant.values())
    ca_courant = sum(p.price * courant[p.key] for p in prods)
    marge_courante = sum(p.margin_per_unit * courant[p.key] for p in prods)
    if seuil_unites is not None:
        marge_securite_u = qte_courante - seuil_unites
        marge_securite_ca = (ca_courant - seuil_ca) if seuil_ca is not None else None
        taux_securite = (marge_securite_u / qte_courante) if qte_courante > 0 else None
    else:
        marge_securite_u = marge_securite_ca = taux_securite = None

    # Contrainte de capacité : mix maximisant la marge + équilibre atteignable ?
    mix_opt: tuple[MixEntry, ...] = ()
    max_marge_cap = None
    ecart_cap = None
    if capacity_minutes and times:
        mix_opt, warn_mix = optimize_mix(prods, times, capacity_minutes, demand_caps, objective=objective)
        warnings.extend(warn_mix)
        max_marge_cap = sum(e.margin for e in mix_opt)
        if fc > 0:
            equilibre_atteignable = max_marge_cap >= fc
            ecart_cap = fc - max_marge_cap
            if not equilibre_atteignable:
                warnings.append(
                    f"Équilibre inatteignable avec la capacité industrielle : la marge maximale "
                    f"atteignable ({max_marge_cap:,.0f} €) est inférieure aux charges fixes "
                    f"({fc:,.0f} €) — écart {ecart_cap:,.0f} €. Il faut augmenter la capacité, "
                    "les prix ou réduire les charges fixes."
                )
        elif fc == 0:
            equilibre_atteignable = True

    return BreakEvenResult(
        products=tuple(prods),
        fixed_costs=fc,
        reference_mix=ref,
        break_even_units=seuil_unites,
        break_even_revenue=seuil_ca,
        weighted_margin_per_unit=marge_unitaire_ponderee,
        weighted_margin_ratio=taux_pondere,
        current_units=qte_courante,
        current_revenue=ca_courant,
        current_margin=marge_courante,
        margin_of_safety_units=marge_securite_u,
        margin_of_safety_revenue=marge_securite_ca,
        margin_of_safety_ratio=taux_securite,
        fixed_cost_allocation=imputation,
        break_even_units_by_product=seuil_par_produit,
        capacity_minutes=dict(capacity_minutes or {}),
        max_margin_under_capacity=max_marge_cap,
        equilibrium_reachable=equilibre_atteignable,
        capacity_gap=ecart_cap,
        optimal_mix=mix_opt,
        optimal_mix_units=sum(e.units for e in mix_opt),
        optimal_mix_revenue=sum(e.revenue for e in mix_opt),
        optimal_mix_margin=sum(e.margin for e in mix_opt),
        allocation_method=allocation,
        objective="volume" if str(objective).lower() == "volume" else "margin",
        warnings=tuple(warnings),
    )


def sensitivity_price(
    products: Sequence[ProductEconomics],
    fixed_costs: Any,
    delta_ratio: float,
    mix: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Effet d'une variation de prix (ratio) sur le point d'équilibre.

    `delta_ratio` de +0,05 = prix en hausse de 5 %. Le volume de référence
    est conservé : c'est la sensibilité du seuil, pas une prévision.
    """
    d = _num(delta_ratio)
    nouveaux = [
        ProductEconomics(
            key=p.key, name=p.name,
            price=max(0.0, p.price * (1.0 + d)),
            unit_variable_cost=p.unit_variable_cost,
            current_units=p.current_units,
            demand_units=p.demand_units,
        )
        for p in products
    ]
    avant = compute_break_even(products, fixed_costs, mix=mix)
    apres = compute_break_even(nouveaux, fixed_costs, mix=mix)
    return {
        "delta_ratio": d,
        "avant": avant,
        "apres": apres,
        "delta_break_even_units": (
            (apres.break_even_units - avant.break_even_units)
            if (apres.break_even_units is not None and avant.break_even_units is not None)
            else None
        ),
        "delta_break_even_revenue": (
            (apres.break_even_revenue - avant.break_even_revenue)
            if (apres.break_even_revenue is not None and avant.break_even_revenue is not None)
            else None
        ),
    }


def capacity_units_per_product(
    products: Sequence[ProductEconomics],
    times: Mapping[str, Mapping[str, float]],
    capacity_minutes: Mapping[str, float],
) -> dict[str, float]:
    """Volume maximal par produit s'il était produit seul (contrainte atelier)."""
    out: dict[str, float] = {}
    for p in products:
        t = times.get(p.name) or times.get(p.key) or {}
        maxi = float("inf")
        for w, dispo in capacity_minutes.items():
            minutes = _num(t.get(w))
            if minutes > 0:
                maxi = min(maxi, max(0.0, _num(dispo)) / minutes)
        out[p.key] = 0.0 if maxi == float("inf") else maxi
    return out


def unit_variable_costs(
    bom: Mapping[str, Mapping[str, float]],
    prix_matieres: Mapping[str, float],
    produits: Sequence[str],
) -> tuple[dict[str, float], tuple[str, ...]]:
    """Coût variable unitaire par produit : nomenclature x prix des matières.

    `bom` : produit -> {matière : quantité par unité}. `prix_matieres` : prix
    unitaire par matière. Retourne (coûts par produit, avertissements).

    Une matière sans prix n'est pas valorisée : le coût unitaire serait alors
    sous-estimé — l'avertissement le dit explicitement, aucun prix n'est
    inventé pour combler le trou.
    """
    warnings: list[str] = []
    out: dict[str, float] = {}
    matieres_sans_prix: set[str] = set()
    for produit in produits:
        total = 0.0
        for matiere, quantite in (bom.get(produit) or {}).items():
            qte = max(0.0, _num(quantite))
            prix = _num(prix_matieres.get(matiere), -1.0)
            if prix < 0:
                matieres_sans_prix.add(matiere)
                continue
            total += qte * prix
        out[produit] = total
    if matieres_sans_prix:
        warnings.append(
            "Matières sans prix de référence : "
            + ", ".join(sorted(matieres_sans_prix))
            + " — le coût variable unitaire est sous-estimé."
        )
    return out, tuple(warnings)


@dataclass(frozen=True)
class OptimumTarget:
    """Une valeur à atteindre pour atteindre l'optimum, avec son écart.

    `sens` indique comment lire la cible :
    * `min` : plancher (prix minimum, capacité minimale) — atteint si l'on est
      déjà **au-dessus** de la cible ;
    * `max` : plafond (coût unitaire maximum) — atteint si l'on est déjà
      **en-dessous** de la cible ;
    * `cible` : valeur à rejoindre exactement (volume, CA, marge).
    """

    cle: str
    categorie: str          # Volume · Prix · Coût · Capacité · Chiffre d'affaires · Marge
    libelle: str
    valeur_actuelle: float
    valeur_cible: float
    unite: str              # "u" · "€" · "€/u" · "min"
    levier: str             # Production · Prix · Achats · Machines · Mix
    action: str
    sens: str = "cible"     # "min" · "max" · "cible"
    bloquant: bool = False  # l'écart conditionne l'atteinte de l'optimum

    @property
    def ecart(self) -> float:
        """Écart restant à combler (négatif si la cible est dépassée)."""
        if self.sens == "min":
            return self.valeur_cible - self.valeur_actuelle
        if self.sens == "max":
            return self.valeur_actuelle - self.valeur_cible
        return self.valeur_cible - self.valeur_actuelle

    @property
    def atteint(self) -> bool:
        tol = max(1e-6, abs(self.valeur_cible) * 1e-6)
        if self.sens == "min":
            return self.valeur_actuelle >= self.valeur_cible - tol
        if self.sens == "max":
            return self.valeur_actuelle <= self.valeur_cible + tol
        return abs(self.ecart) <= tol


def compute_optimum_targets(
    products: Sequence[ProductEconomics],
    resultat: BreakEvenResult,
    times: Mapping[str, Mapping[str, float]] | None = None,
    capacity_minutes: Mapping[str, float] | None = None,
    machines: Mapping[str, int] | None = None,
    capacity_min_per_machine: float = 9600.0,
    objective: str = "margin",
    mix_demande: Mapping[str, float] | None = None,
) -> tuple[list[OptimumTarget], tuple[str, ...]]:
    """Valeurs à atteindre pour atteindre l'optimum, une ligne par levier.

    Pour chaque produit et par atelier, la valeur actuelle et la valeur cible
    sont comparées : volume à vendre, prix plancher (à volume actuel), plafond
    de coût variable, capacité d'atelier nécessaire. S'y ajoutent les objectifs
    de chiffre d'affaires et de marge.

    `mix_demande` : la demande non servie (volume souhaité par produit) ; elle
    sert à chiffrer la capacité à acquérir pour tout servir.
    """
    warnings: list[str] = []
    cibles: list[OptimumTarget] = []
    produits = list(products)
    par_key = {p.key: p for p in produits}

    # --- 1. Volume à vendre (mix optimal) ---------------------------------
    volumes_cibles = {e.key: e.units for e in resultat.optimal_mix}
    for p in produits:
        cible = volumes_cibles.get(p.key, 0.0)
        if cible <= 0 and p.current_units <= 0:
            continue
        ecart = cible - p.current_units
        if abs(ecart) < 1e-6:
            action = "Volume déjà aligné sur l'optimum."
        elif ecart > 0:
            action = f"Vendre {ecart:,.0f} u de plus ({p.name}).".replace(",", " ")
        else:
            action = f"Réduire la production de {abs(ecart):,.0f} u ({p.name}).".replace(",", " ")
        cibles.append(OptimumTarget(
            cle=f"volume_{p.key}",
            categorie="Volume",
            libelle=f"Volume {p.name}",
            valeur_actuelle=p.current_units,
            valeur_cible=cible,
            unite="u",
            levier="Mix",
            action=action,
        ))

    # --- 2. Prix plancher par produit (à volume actuel) --------------------
    for p in produits:
        qte = p.current_units
        part_fixe = resultat.fixed_cost_allocation.get(p.key, 0.0)
        if qte <= 0 or part_fixe <= 0:
            continue
        prix_plancher = p.unit_variable_cost + part_fixe / qte
        if prix_plancher <= p.price:
            action = f"Prix suffisant : couvre la part imputée des charges fixes."
        elif prix_plancher > 2 * p.price:
            action = (
                f"À ce volume, aucun prix raisonnable ne couvre la part imputée : "
                f"il faut d'abord augmenter le volume."
            )
        else:
            action = f"Monter le prix à {prix_plancher:,.0f} € HT (+{(prix_plancher / p.price - 1) * 100:,.0f} %) pour couvrir la part imputée.".replace(",", " ")
        cibles.append(OptimumTarget(
            cle=f"prix_{p.key}",
            categorie="Prix",
            libelle=f"Prix plancher {p.name}",
            valeur_actuelle=p.price,
            valeur_cible=prix_plancher,
            unite="€",
            levier="Prix",
            action=action,
            sens="min",
        ))

    # --- 3. Plafond de coût variable (maintien du taux de marge) ----------
    taux = resultat.weighted_margin_ratio
    if taux:
        for p in produits:
            plafond = p.price * (1.0 - taux)
            if plafond <= 0:
                continue
            action = (
                f"Coût unitaire à tenir sous {plafond:,.0f} €/u "
                f"(maintien du taux de marge de {taux * 100:,.0f} %).".replace(",", " ")
            )
            cibles.append(OptimumTarget(
                cle=f"cout_{p.key}",
                categorie="Coût",
                libelle=f"Plafond coût variable {p.name}",
                valeur_actuelle=p.unit_variable_cost,
                valeur_cible=plafond,
                unite="€/u",
                levier="Achats",
                action=action,
                sens="max",
            ))

    # --- 4. Capacité : minutes nécessaires pour servir la demande ---------
    if times and capacity_minutes:
        _t = times or {}
        charge_demande: dict[str, float] = {}
        for atelier in capacity_minutes:
            minutes = 0.0
            for p in produits:
                qte = (mix_demande or {}).get(p.key, 0.0)
                if qte <= 0:
                    continue
                t = _t.get(p.name) or _t.get(p.key) or {}
                minutes += qte * max(0.0, _num(t.get(atelier)))
            charge_demande[atelier] = minutes
        for atelier, dispo in capacity_minutes.items():
            besoin = charge_demande.get(atelier, 0.0)
            ecart = besoin - max(0.0, _num(dispo))
            if besoin <= 0:
                continue
            if ecart > 0:
                machines_ajoutees = int(-(-ecart // max(1.0, _num(capacity_min_per_machine, 9600.0))))
                action = (
                    f"Ajouter {machines_ajoutees} machine(s) en {atelier} "
                    f"({ecart:,.0f} min manquantes) pour servir toute la demande."
                    .replace(",", " ")
                )
            else:
                action = (
                    f"Capacité suffisante en {atelier} : "
                    f"{abs(ecart):,.0f} min de marge, soit "
                    f"{abs(ecart) / max(1.0, _num(capacity_min_per_machine, 9600.0)):.1f} machine(s) "
                    "de réserve.".replace(",", " ")
                )
            cibles.append(OptimumTarget(
                cle=f"capacite_{atelier}",
                categorie="Capacité",
                libelle=f"Capacité {atelier}",
                valeur_actuelle=max(0.0, _num(dispo)),
                valeur_cible=besoin,
                unite="min",
                levier="Machines",
                action=action,
                sens="min",
                bloquant=ecart > 0,
            ))

    # --- 5. Chiffre d'affaires et marge du mix optimal ---------------------
    if resultat.optimal_mix:
        cibles.append(OptimumTarget(
            cle="ca_optimum",
            categorie="Chiffre d'affaires",
            libelle="Chiffre d'affaires de l'optimum",
            valeur_actuelle=resultat.current_revenue,
            valeur_cible=resultat.optimal_mix_revenue,
            unite="€",
            levier="Mix",
            action=(
                f"Mix optimal : {resultat.optimal_mix_units:,.0f} u pour "
                f"{resultat.optimal_mix_revenue:,.0f} € de CA.".replace(",", " ")
            ),
        ))
        cibles.append(OptimumTarget(
            cle="marge_optimum",
            categorie="Marge",
            libelle="Marge sur coût variable de l'optimum",
            valeur_actuelle=resultat.current_margin,
            valeur_cible=resultat.optimal_mix_margin,
            unite="€",
            levier="Mix",
            action=(
                f"Marge maximale atteignable sous capacité : "
                f"{resultat.optimal_mix_margin:,.0f} €.".replace(",", " ")
            ),
        ))

    return cibles, tuple(warnings)
