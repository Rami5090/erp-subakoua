"""Remplissage automatique du PDF « Mission spécifique Finance ».

Le test utilise le vrai gabarit s'il est présent sur la machine (sinon il est
ignoré) : c'est le seul moyen de vérifier que les 124 champs logiques du
livrable correspondent bien aux champs du formulaire, tolérance d'accent et de
casse comprise.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from livrable_finance import (
    LivrableFinance,
    build_bilan,
    build_marges,
    fill_pdf,
    pdf_field_values,
)

TEMPLATE = Path(r"C:/Users/ramih/OneDrive/BUT MLT3/Redoublement/Mission specifique Finance.pdf")

PRIX = {
    "Shorty 3": Decimal("130"), "Integral 3": Decimal("205"), "Shorty 5": Decimal("240"),
    "Integral 5": Decimal("310"), "Integral 7": Decimal("360"),
}
COUTS = {
    "Shorty 3": Decimal("60"), "Integral 3": Decimal("90"), "Shorty 5": Decimal("80"),
    "Integral 5": Decimal("120"), "Integral 7": Decimal("150"),
}
BILAN_ROWS = [
    {"Actif": "Banque", "Passif": "", "Net exercice N": "1250000", "Exercice N": ""},
    {"Actif": "Clients", "Passif": "", "Net exercice N": "1423680", "Exercice N": ""},
    {"Actif": "", "Passif": "Fournisseurs", "Net exercice N": "", "Exercice N": "300000"},
    {"Actif": "", "Passif": "Total capitaux propres", "Net exercice N": "", "Exercice N": "2000000"},
]


def _livrable(couts=COUTS):
    marges, _warns = build_marges(PRIX, Decimal("0"), Decimal("0.20"), couts)
    return LivrableFinance(
        periode="Année 1 - Août",
        bilan=build_bilan(BILAN_ROWS),
        ca_mensuel=Decimal("1186400"),
        resultat_avant_impot_mensuel=Decimal("950000"),
        ca_cumule=Decimal("7000000"),
        resultat_cumule=Decimal("5000000"),
        marges=tuple(marges),
        flux_banque={"initial": Decimal("1250000"), "final": Decimal("900000")},
        creances={"total": Decimal("1423680"), "encaissement": Decimal("1200000")},
        couts_source_periode="Année 1 - Août",
    )


def test_pdf_field_values_are_computed():
    liv = _livrable()
    valeurs = pdf_field_values(liv, {}, PRIX)

    assert len(valeurs) > 50
    assert all(isinstance(v, str) for v in valeurs.values())


def test_pdf_field_values_survive_to_a_missing_cost_study():
    """Étude de coûts non achetée : le livrable doit rester produisible."""
    liv = _livrable(couts={})
    valeurs = pdf_field_values(liv, {}, PRIX)

    assert len(valeurs) > 50


def test_pdf_is_filled_from_the_real_template(tmp_path):
    if not TEMPLATE.exists():
        pytest.skip("gabarit PDF « Mission spécifique Finance » absent de la machine")

    liv = _livrable()
    valeurs = pdf_field_values(liv, {}, PRIX)
    sortie = tmp_path / "rempli.pdf"

    n, missing = fill_pdf(TEMPLATE.read_bytes(), str(sortie), valeurs)

    assert n == len(valeurs)
    assert missing == []
    assert sortie.exists() and sortie.stat().st_size > 0


def test_fill_pdf_reports_a_missing_dependency_clearly(monkeypatch):
    """Sans pypdf, le message doit être explicite (pas une ModuleNotFoundError)."""
    import builtins

    vraie_import = builtins.__import__

    def _sans_pypdf(nom, *args, **kwargs):
        if nom == "pypdf" or nom.startswith("pypdf."):
            raise ImportError("No module named 'pypdf'")
        return vraie_import(nom, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _sans_pypdf)

    with pytest.raises(RuntimeError) as exc:
        fill_pdf(b"%PDF-1.4", "sortie.pdf", {"x": "1"})

    assert "pypdf" in str(exc.value)
