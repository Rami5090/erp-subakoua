"""Tests de l'orchestrateur d'acquisition (lecture -> achat -> relecture).

Aucun réseau, aucune base : client API et connexion factices.
"""
import smart_scraper as scraper
from subakoua_api import StudyAvailability, StudyFetchResult


class FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((str(sql), params))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection:
    def __init__(self):
        self.cursors = []

    def cursor(self):
        cur = FakeCursor()
        self.cursors.append(cur)
        return cur

    def commit(self):
        pass

    def inserts_into(self, table):
        return [p for c in self.cursors for sql, p in c.statements
                if f"INSERT INTO {table}" in sql]


class FakeAPI:
    """Catalogue, lectures et achats scriptés par (periode, study_id)."""

    def __init__(self, *, bought=None, readable=None, prices=None, unlock_on_purchase=None):
        self.bought = dict(bought or {})
        self.readable = dict(readable or {})
        self.prices = dict(prices or {})
        self.unlock_on_purchase = set(unlock_on_purchase or [])
        self.purchases = []

    def catalog(self, period, study_ids=None):
        return [StudyAvailability(study_id=sid,
                                  price=float(self.prices.get((period, sid),
                                                              self.prices.get(sid, 50.0))),
                                  bought_by_team=bool(self.bought.get((period, sid), False)),
                                  period_code="0101")
                for sid in (study_ids or [])]

    def fetch_study(self, study_id, period, require_read_allowed=True):
        ok = bool(self.readable.get((period, study_id), False))
        if not ok and study_id in self.unlock_on_purchase and self.bought.get((period, study_id)):
            ok = True
        return StudyFetchResult(study_id, period, "0101", "http://x", ok,
                                {"studyId": study_id, "readAllowed": ok, "price": 50.0},
                                ok,
                                "" if ok else "Document non lisible : readAllowed=false.")

    def purchase_study(self, purchase_period, study_id, label="", expected_price=None):
        self.purchases.append((purchase_period, study_id))
        self.bought[(purchase_period, study_id)] = True
        return {"price": expected_price}


def test_deja_lisible_synchronise_sans_achat():
    api = FakeAPI(bought={("P", "peeedccg"): True}, readable={("P", "peeedccg"): True})
    conn = FakeConnection()
    out = scraper.acquire_studies(api, conn, target_periods=["P"], study_ids=["peeedccg"],
                                  budget=600.0, allow_real_purchases=True)
    assert out[0]["status"] == "OK"
    assert api.purchases == []
    assert conn.inserts_into("erp_etudes")


def test_non_achete_sans_autorisation_ne_achete_pas():
    api = FakeAPI()
    conn = FakeConnection()
    out = scraper.acquire_studies(api, conn, target_periods=["P"],
                                  study_ids=["productionCostDetailStudy"],
                                  budget=600.0, allow_real_purchases=False)
    assert out[0]["status"] == "A_ACHETER"
    assert api.purchases == []


def test_achete_puis_synchronise():
    api = FakeAPI(prices={("P", "peeedccg"): 100.0}, unlock_on_purchase={"peeedccg"})
    conn = FakeConnection()
    out = scraper.acquire_studies(api, conn, target_periods=["P"], study_ids=["peeedccg"],
                                  budget=600.0, allow_real_purchases=True)
    assert out[0]["status"] == "ACHETEE_ET_SYNCHRONISEE"
    assert api.purchases == [("P", "peeedccg")]
    assert conn.inserts_into("erp_etudes")


def test_strategique_sans_optin_refusee():
    api = FakeAPI(prices={("P", "peeumreusreprv"): 5000.0})
    conn = FakeConnection()
    out = scraper.acquire_studies(api, conn, target_periods=["P"], study_ids=["peeumreusreprv"],
                                  budget=60000.0,
                                  allow_real_purchases=True, allow_5000=False)
    assert out[0]["status"] == "REFUSE_STRATEGIQUE"
    assert api.purchases == []


def test_budget_depasse_refuse():
    api = FakeAPI(readable={("P", "peeedccg"): True},
                  prices={("P", "peeedccg"): 100.0, ("P", "peeedscrdl"): 100.0})
    conn = FakeConnection()
    out = scraper.acquire_studies(api, conn, target_periods=["P"],
                                  study_ids=["peeedccg", "peeedscrdl"],
                                  budget=50.0, allow_real_purchases=True)
    # peeedccg lisible directement (gratuit en budget) ; peeedscrdl à 100 € > budget 50 €.
    statuses = {r["study_id"]: r["status"] for r in out}
    assert statuses["peeedccg"] == "OK"
    assert statuses["peeedscrdl"] == "REFUSE_BUDGET"
    assert api.purchases == []


def test_achete_mais_passe_illisible_trace():
    api = FakeAPI(prices={("P", "peeedscrdl"): 100.0})  # jamais lisible sur P
    conn = FakeConnection()
    out = scraper.acquire_studies(api, conn, target_periods=["P"], study_ids=["peeedscrdl"],
                                  budget=600.0, allow_real_purchases=True)
    assert out[0]["status"] == "ACHETEE"
    assert "Resynchroniser" in out[0]["reason"]
    assert api.purchases == [("P", "peeedscrdl")]
