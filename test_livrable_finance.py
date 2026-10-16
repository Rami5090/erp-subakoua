from decimal import Decimal
from livrable_finance import build_bilan, build_marges, couts_from_cost_study

# Bilan Août : 19 lignes extraites le 16/10/2026 (montants Net exercice N / Exercice N)
BILAN_AOUT = [
    {"Actif": "Matières", "Net exercice N": 708686.9, "Passif": "Fournisseurs", "Exercice N": 1962349.2},
    {"Actif": "Produits finis", "Net exercice N": 15964.55, "Passif": "TVA collectée", "Exercice N": 885226.17},
    {"Actif": "Clients", "Net exercice N": 7840038.72, "Passif": "Impôt sur les bénéfices", "Exercice N": 0},
    {"Actif": "TVA déductible", "Net exercice N": 285467.54, "Passif": "Total capitaux propres", "Exercice N": 13401110.81},
    {"Actif": "Acomptes sur IS", "Net exercice N": 83766.1, "Passif": "Emprunts mensualisés contractés", "Exercice N": 2593819.11},
    {"Actif": "Compte épargne", "Net exercice N": 0, "Passif": "Total dettes", "Exercice N": 5441394.48},
    {"Actif": "Banque", "Net exercice N": 3082581.2, "Passif": "", "Exercice N": ""},
    {"Actif": "Total actif circulant", "Net exercice N": 12016505.01, "Passif": "", "Exercice N": ""},
    {"Actif": "En-cours alpha", "Net exercice N": 0, "Passif": "Emprunts libres", "Exercice N": 0},
]


def test_bilan_aout():
    b = build_bilan(BILAN_AOUT)
    assert b.banque == Decimal("3082581.2")
    assert b.epargne == Decimal("0")
    assert b.disponibilites == Decimal("3082581.2")
    assert b.stocks_matieres == Decimal("708686.9")
    assert b.creances_clients == Decimal("7840038.72")
    assert b.capitaux_propres == Decimal("13401110.81")
    assert b.dette_financiere == Decimal("2593819.11")
    # Actifs : 708686.9 + 15964.55 + 7840038.72 + 83766.1 + 285467.54
    assert b.actifs_exploitation == Decimal("8933923.81")
    assert b.dettes_exploitation == Decimal("2847575.37")
    assert b.bfre == Decimal("6086348.44")


def test_marges_aout_avec_couts_juillet():
    prix = {"Shorty 3": Decimal("130"), "Integral 3": Decimal("205"),
            "Shorty 5": Decimal("240"), "Integral 5": Decimal("310"), "Integral 7": Decimal("360")}
    couts = {"Shorty 3": Decimal("38.62"), "Integral 3": Decimal("58.11"),
             "Shorty 5": Decimal("54.62"), "Integral 5": Decimal("70.86"), "Integral 7": Decimal("0")}
    lignes, warns = build_marges(prix, Decimal("0"), Decimal("0.20"), couts)
    s3 = next(x for x in lignes if x.produit == "Shorty 3")
    assert s3.marge_a == Decimal("91.38")
    assert s3.marge_b == Decimal("65.38")
    i3 = next(x for x in lignes if x.produit == "Integral 3")
    assert i3.marge_a == Decimal("146.89")


def test_couts_study_production_nulle_signalee():
    payload = {"productsProductionCostDetails": [
        {"productRef": "MONO_F", "producedProductsNumber": 0,
         "directCostDetail": {"phasesCostDetails": []}, "finalProductionPhaseCostDetail": {}}]}
    couts, warns = couts_from_cost_study(payload)
    assert "Integral 7" not in couts
    assert any("nulle" in w for w in warns)
