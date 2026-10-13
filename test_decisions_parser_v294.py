from decisions_parser import (
    detect_product, parse_decisions, parse_marketing, parse_production, parse_rh,
)


def L(section, ligne, *valeurs):
    return {"section": section, "ligne": ligne, "valeurs": list(valeurs)}


def test_detect_product_variants():
    assert detect_product("Shorty 3") == "s3"
    assert detect_product("Integral 7 (€)") == "i7"
    assert detect_product("MONO_T") == "i5"
    assert detect_product("Prix de vente") is None


def test_marketing_scraper24_per_product_rows_and_readonly_budget():
    contenu = {"__lignes__": [
        L("Prix de vente", "Shorty 3", 115), L("Prix de vente", "Integral 3", 190),
        L("Prix de vente", "Shorty 5", 220), L("Prix de vente", "Integral 5", 280),
        L("Prix de vente", "Integral 7", 375),
        L("Budget publicitaire", "Budget Pub Marque HT", 60500),
        L("PLV", "Shorty 3", 3100), L("PLV", "Integral 7", 3500),
        L("Budget publicitaire", "Axe principal", "Prix"),
        L("Études", "Prix de revient Shorty 3", 80),
    ]}
    got = parse_marketing(contenu)
    assert got["Prix_S3"] == 115 and got["Prix_I7"] == 375
    assert got["Budget_Marque"] == 60500
    assert got["Pub_S3"] == 3100 and got["Pub_I7"] == 3500
    assert got["Axe_Principal"] == "Prix"
    assert got["Prix_S3"] != 80  # prix de revient exclu


def test_marketing_single_row_of_five_values_under_tab():
    contenu = {"Politique": {"__lignes__": [L("Marketing", "Prix de vente", 115, 190, 220, 280, 375)]}}
    got = parse_marketing(contenu)
    assert [got[f"Prix_{k}"] for k in ("S3", "I3", "S5", "I5", "I7")] == [115, 190, 220, 280, 375]


def test_marketing_legacy_format_dedupes_nested_inputs():
    legacy = {"Prix de vente": {"Shorty 3": [115, 115], "Integral 3": [190, 190]}}
    got = parse_marketing(legacy)
    assert got == {"Prix_S3": 115, "Prix_I3": 190}


def test_marketing_rejects_implausible_values():
    assert parse_marketing({"__lignes__": [L("Prix", "Shorty 3", 3)]}) == {}


def test_rh_headcount_and_salary_in_either_order():
    contenu = {"__lignes__": [
        L("Production", "Employés", 15, 2000), L("Production", "Cadres", 3000, 3),
        L("Administration / Finance", "Directeurs", 1, 2000),
        L("Approvisionnement", "Salaire cadres", 2250),
    ]}
    got = parse_rh(contenu)
    assert got["Eff_Employes_Prod"] == 15 and got["Sal_Employes_Prod"] == 2000
    assert got["Eff_Cadres_Prod"] == 3 and got["Sal_Cadres_Prod"] == 3000
    assert got["Eff_Directeurs_Admin"] == 1
    assert got["Sal_Cadres_Appro"] == 2250 and "Eff_Cadres_Appro" not in got


def test_production_quantities():
    contenu = {"__lignes__": [L("Quantités à produire", "Shorty 3", 50), L("Stocks", "Shorty 3 stock", 999),
                              L("Quantités à produire", "Integral 7", 2500)]}
    assert parse_production(contenu) == {"s3": 50, "i7": 2500}


def test_champs_fallback_when_no_lignes():
    contenu = {"__champs__": [
        {"section": "Prix", "ligne": "Shorty 3", "index": 0, "label": "", "value": 115},
        {"section": "Prix", "ligne": "Integral 3", "index": 0, "label": "", "value": 190},
    ]}
    assert parse_marketing(contenu) == {"Prix_S3": 115, "Prix_I3": 190}


def test_parse_decisions_reports_unrecognised_format():
    res = parse_decisions({"marketing": {"Truc": {"Machin": [1, 2, 3]}}, "rh": {}})
    assert res["marketing"] == {} and any("calibrer" in r for r in res["report"])


def test_real_empty_extraction_is_not_useful():
    """Lignes réelles scrapées en v2.4.0 : seule la barre de recherche + le sélecteur de période."""
    import json
    from pathlib import Path
    from decisions_parser import decisions_utiles
    fx = json.loads((Path(__file__).parent / "fixtures" / "decisions_page_vide_reelle.json").read_text(encoding="utf-8"))
    assert not decisions_utiles(fx["contenu"])
    res = parse_decisions({"marketing": fx["contenu"]})
    assert res["marketing"] == {} and any("VIDE" in r for r in res["report"])


def test_real_content_is_useful_in_lignes_or_champs():
    from decisions_parser import decisions_utiles
    assert decisions_utiles({"__lignes__": [L("Prix", "Shorty 3", 115)], "__champs__": []})
    assert decisions_utiles({"Onglet": {"__champs__": [{"section": "", "ligne": "", "control": "prix", "value": 3}]}})
    assert not decisions_utiles({"__lignes__": [L("Prix", "Shorty 3")], "__champs__": []})   # ligne sans valeur
