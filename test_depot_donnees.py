"""Accès aux données partagé : lecture tolérante et signatures de cache fiables."""
import sqlite3
import types

import depot_donnees as depot


class _Conn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        curseur = self.db.execute(str(sql), params or {})
        lignes = [dict(r) for r in curseur.fetchall()]
        return types.SimpleNamespace(mappings=lambda: types.SimpleNamespace(all=lambda: lignes))


class _Engine:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row

    def connect(self):
        return _Conn(self.db)


def _engine_avec_etudes():
    e = _Engine()
    e.db.executescript("""
        CREATE TABLE erp_etudes (id INTEGER PRIMARY KEY, periode TEXT, study_id TEXT, read_allowed INT,
                                 payload TEXT, fetched_at TEXT);
        INSERT INTO erp_etudes VALUES (1, 'Année 1 - Mars', 'monitoring', 1, '{"marketShares": 12}', '2026-11-01 10:00');
        CREATE TABLE erp_donnees (id INTEGER PRIMARY KEY, periode TEXT, type_donnee TEXT, module TEXT, contenu TEXT);
    """)
    return e


def test_decoder_json_tolere_les_formats_du_stockage():
    assert depot.decoder_json('{"a": 1}') == {"a": 1}
    assert depot.decoder_json(b'"{\\"a\\": 2}"') == {"a": 2}          # JSON doublement encodé
    assert depot.decoder_json("pas du json") == {}
    assert depot.decoder_json(None) == {}


def test_une_table_absente_renvoie_une_erreur_et_non_une_exception():
    lignes, erreur = depot.lire_lignes(_Engine(), "SELECT * FROM table_inexistante")
    assert lignes == [] and erreur == "OperationalError"
    assert depot.lire_lignes(None, "SELECT 1") == ([], "base indisponible")


def test_la_signature_change_quand_une_etude_est_resynchronisee():
    """L'upsert du scraper conserve l'identifiant : seule la date de synchronisation
    révèle un contenu modifié. Sans elle, le cache servirait l'ancien payload."""
    e = _engine_avec_etudes()
    avant = depot.signature_etudes(e)
    e.db.execute("UPDATE erp_etudes SET payload = '{\"marketShares\": 15}', fetched_at = '2026-11-02 09:00'")
    assert depot.signature_etudes(e) != avant


def test_signature_sans_colonne_de_date():
    e = _engine_avec_etudes()
    e.db.execute("INSERT INTO erp_donnees VALUES (1, 'Année 1 - Mars', 'etat_actuel', 'banque_assurance', '{}')")
    assert depot.signature_donnees(e) == (("1", "Année 1 - Mars", "banque_assurance"),)


def test_charger_sources_rapport_isole_les_tables_absentes():
    sources = depot.charger_sources_rapport(_engine_avec_etudes())
    assert len(sources["erp_etudes"]) == 1
    assert sources["tables"]["Decisions_Marketing"] == []
    assert "Decisions_Marketing" in sources["erreurs"] and "erp_etudes" not in sources["erreurs"]
