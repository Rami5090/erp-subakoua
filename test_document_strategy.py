from document_strategy import DocumentCandidate, make_candidate, optimize_document_plan, parse_price


def c(module, title, price, owned=False):
    return make_candidate(module, module, {"title": title, "url": f"https://x/{module}/{title}", "card_text": f"{title} {price} €", "price": price, "owned": owned, "locked": not owned})


def test_parse_price_fr():
    assert parse_price("Prix du document : 12,50 €") == 12.50
    assert parse_price("1 234,56 €") == 1234.56


def test_global_plan_prefers_multi_coverage():
    candidates = [
        c("banque_assurance", "Banque", 5),
        c("expert_comptable", "Synthèse", 8),
        c("expert_comptable", "TVA et impôt sur les sociétés", 4),
        c("donnees_internes", "Tableau de bord", 20),
        c("donnees_internes", "Stocks", 6),
        c("production", "Les ordres de production", 7),
    ]
    plan = optimize_document_plan(candidates, "Pilotage global", budget=30)
    titles = {x["title"] for x in plan["selected"]}
    assert "Synthèse" in titles
    assert plan["spent_estimate"] <= 30
    assert plan["coverage_ratio"] > 0


def test_owned_document_counts_as_free_coverage():
    owned = c("banque_assurance", "Banque", 5, owned=True)
    plan = optimize_document_plan([owned], "Finance & trésorerie", budget=1)
    assert any(x["title"] == "Banque" for x in plan["selected"])
    assert plan["spent_estimate"] == 0


def test_unknown_price_is_not_auto_selected_for_purchase():
    candidate = make_candidate("Finance", "finance", {"title": "Emprunts", "url": "https://x/e", "card_text": "Emprunts Acheter", "price": None, "owned": False, "locked": True})
    plan = optimize_document_plan([candidate], "Finance & trésorerie", budget=100)
    assert plan["selected"] == []
    assert plan["requires_manual_price_check"]
