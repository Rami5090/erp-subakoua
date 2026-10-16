from decimal import Decimal
from livrable_finance import (
    build_bilan,
    build_marges,
    couts_from_cost_study,
    couts_detail_from_cost_study,
    missing_studies,
    parse_simplified_result,
    pdf_field_values,
    fmt_fr,
    fmt_pct,
    LivrableFinance,
)

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


def test_pdf_values_bilan_et_marges():
    bilan = build_bilan(BILAN_AOUT)
    prix = {"Shorty 3": Decimal("130"), "Integral 3": Decimal("205"),
            "Shorty 5": Decimal("240"), "Integral 5": Decimal("310"), "Integral 7": Decimal("360")}
    couts_detail = {"Shorty 3": {"neoprene": Decimal("14.21"), "renforts": Decimal("0"),
                                 "manchons": Decimal("0"), "fermetures": Decimal("3.21"),
                                 "decoupe": Decimal("4.46"), "assemblage": Decimal("15.15"),
                                 "conditionnement": Decimal("1.60"), "autres": Decimal("0")}}
    marges, _ = build_marges(prix, Decimal("0"), Decimal("0.20"),
                             {"Shorty 3": Decimal("38.63")})
    liv = LivrableFinance("Année 1 - Août", bilan, Decimal("4433396"), Decimal("1882635.56"),
                          Decimal("22307946.04"), Decimal("7398514.24"), tuple(marges))
    vals = pdf_field_values(liv, couts_detail, prix)
    assert vals["Compte banqueRow1"] == "3 082 581,20"
    assert vals["B F R ERow1"] == "6 086 348,44"
    assert vals["Chiffre daffairesRow1"] == "4 433 396,00"
    # net/CAF non achetés : vidés pour ne pas laisser le mois précédent
    assert vals["Résultat netRow1"] == ""
    assert vals["C A FRow1"] == ""
    # marges Shorty 3 : cout 14.21+3.21+4.46+15.15+1.60 = 38.63
    assert vals["Shorty 3Néoprène d"] == "14,21"
    assert vals["Shorty 3Coût Variable Un canal A l bdefghijk"] == "38,63"
    assert vals["Shorty 3Marge unitaire canal A na l"] == "91,37"
    assert vals["Shorty 3Marge unitaire canal B oa m"] == "65,37"
    # Integral 7 sans coût : champs vidés, pas de zéro trompeur
    assert vals["Integral 7Néoprène d"] == ""
    assert vals["Integral 7Marge unitaire canal A na l"] == ""
    # rédactionnel jamais touché
    assert "Réponses" not in vals and "Nos conclusions" not in vals


def test_couts_detail_breakdown():
    payload = {"productsProductionCostDetails": [{
        "productRef": "SHORTY_C", "producedProductsNumber": 900,
        "directCostDetail": {"phasesCostDetails": [
            {"materialsCosts": [{"objectRef": "NEOPRENE_3", "totalCost": 12787.06}],
             "workshopsCosts": [{"objectRef": "CUTTING", "totalCost": 4009.5}]},
            {"materialsCosts": [{"objectRef": "CLOSURES", "totalCost": 2892.43}],
             "workshopsCosts": [{"objectRef": "ASSEMBLY", "totalCost": 13635.0}]}]},
        "finalProductionPhaseCostDetail": {}}]}
    det, warns = couts_detail_from_cost_study(payload)
    assert det["Shorty 3"]["neoprene"] == Decimal("14.21")
    assert det["Shorty 3"]["decoupe"] == Decimal("4.46")
    assert det["Shorty 3"]["conditionnement"] == Decimal("1.60")
    assert any("conditionnement" in w for w in warns)


def test_format_fr():
    assert fmt_fr(Decimal("3082581.2")) == "3 082 581,20"
    assert fmt_pct(Decimal("0.1935")) == "19,35 %"


def test_simplifie_aout_net_et_caf():
    res = parse_simplified_result({
        "netResult": 1882635.56, "biensLivraisons": 4433396,
        "incomeTaxes": 0.0, "amortization": 258166.65,
        "depreciationProvision": 17140.76,
    })
    assert res is not None
    assert res.resultat_net == Decimal("1882635.56")
    assert res.caf == Decimal("2157942.97")
    assert parse_simplified_result({}) is None
    assert parse_simplified_result(None) is None  # type: ignore[arg-type]


def test_pdf_remplit_net_caf_ratios():
    bilan = build_bilan(BILAN_AOUT)
    res = parse_simplified_result({
        "netResult": 1882635.56, "biensLivraisons": 4433396,
        "incomeTaxes": 0.0, "amortization": 258166.65,
        "depreciationProvision": 17140.76,
    })
    liv = LivrableFinance("Année 1 - Août", bilan, Decimal("4433396"),
                          Decimal("1882635.56"), Decimal("22307946.04"),
                          Decimal("7398514.24"), (), resultat=res)
    vals = pdf_field_values(liv, {}, {})
    assert vals["Résultat netRow1"] == "1 882 635,56"
    assert vals["Résultat netRow1_2"] == "1 882 635,56"
    assert vals["C A FRow1"] == "2 157 942,97"
    assert vals["Taux de ProfitabilitéRow1"] == "42,46 %"
    assert vals["Taux de rentabilitéRow1"] == "14,05 %"
    assert vals["Ratio de capacité dautofinancem entRow1"] == "120,20 %"


def test_missing_studies():
    from livrable_finance import missing_studies
    catalog = {
        "productionCostDetailStudy": {"label": "Coûts de production", "price": 50.0},
        "peeedscrdl": {"label": "CR détaillé", "price": 100.0},
        "bankStatements": {"label": "Relevés", "price": 0.0},
    }
    missing = missing_studies({"bankStatements"}, catalog)
    ids = [m["study_id"] for m in missing]
    assert "productionCostDetailStudy" in ids
    assert "peeedscrdl" in ids
    assert "bankStatements" not in ids
    couts = next(m for m in missing if m["study_id"] == "productionCostDetailStudy")
    assert couts["price"] == 50.0 and "Marges" in couts["pourquoi"]
