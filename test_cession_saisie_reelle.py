"""Saisie des données réelles : cession de machines et impact comptable.

Invariants protégés ici :
  * le produit de cession additionne quantité x prix, atelier par atelier, et
    ignore les quantités négatives ou les prix manquants ;
  * l'impact d'une cession n'invente rien : sans valeur nette comptable, ni le
    résultat de cession ni le retrait d'immobilisation ne sont calculés ;
  * la migration de schéma est idempotente et ne perd aucune donnée.
"""
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from constants import PRIX_CESSION_MACHINES
from db_schema import (
    COLONNES_CESSION_FINANCES,
    COLONNES_CESSION_PARC,
    ajouter_colonnes_manquantes,
    borner_attente_verrou,
    colonnes_existantes,
)
from finance_engine import (
    calculate_disposal_product,
    calculate_machine_disposal_impact,
)


# --------------------------------------------------------------------------- produit de cession
def test_disposal_product_multiplies_quantities_by_unit_prices():
    produit = calculate_disposal_product(
        {"Decoupe": 2, "Assemblage": 1, "Conditionnement": 1},
        PRIX_CESSION_MACHINES,
    )

    assert produit == Decimal("440000.00")


def test_disposal_product_ignores_negative_quantities_and_missing_prices():
    produit = calculate_disposal_product(
        {"Decoupe": -3, "Assemblage": 1, "Conditionnement": 0, "Inconnu": 5},
        {"Assemblage": 150000.0},
    )

    assert produit == Decimal("150000.00")


def test_disposal_product_is_zero_without_disposal():
    assert calculate_disposal_product({"Decoupe": 0, "Assemblage": 0}, PRIX_CESSION_MACHINES) == Decimal("0.00")


# --------------------------------------------------------------------------- impact comptable
def test_impact_with_known_book_value_computes_the_full_accounting():
    impact = calculate_machine_disposal_impact(150000.0, valeur_nette_ht=400000.0)

    assert impact.produit_ht == Decimal("150000.00")
    assert impact.tva_collectee == Decimal("30000.00")
    assert impact.produit_ttc == Decimal("180000.00")
    assert impact.plus_moins_value == Decimal("-250000.00")
    assert impact.delta_tresorerie == Decimal("180000.00")
    assert impact.delta_actif_immobilise == Decimal("-400000.00")
    assert impact.delta_resultat == Decimal("-250000.00")
    assert impact.vnc_connue is True
    assert impact.warnings == ()


def test_impact_without_book_value_does_not_invent_accounting():
    impact = calculate_machine_disposal_impact(150000.0)

    assert impact.produit_ttc == Decimal("180000.00")
    assert impact.delta_tresorerie == Decimal("180000.00")
    assert impact.plus_moins_value == Decimal("0.00")
    assert impact.delta_actif_immobilise == Decimal("0.00")
    assert impact.vnc_connue is False
    assert any("Valeur nette comptable" in w for w in impact.warnings)


def test_impact_clamps_a_negative_book_value():
    impact = calculate_machine_disposal_impact(150000.0, valeur_nette_ht=-1000.0)

    assert impact.valeur_nette_ht == Decimal("0.00")
    assert impact.vnc_connue is False
    assert any("négative" in w for w in impact.warnings)


def test_impact_without_disposal_is_silent():
    impact = calculate_machine_disposal_impact(0.0)

    assert impact.produit_ttc == Decimal("0.00")
    assert impact.warnings == ()


# --------------------------------------------------------------------------- migration de schéma
@pytest.fixture()
def sqlite_engine():
    return create_engine("sqlite://")


def test_migration_adds_the_missing_columns(sqlite_engine):
    with sqlite_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE Parc_Machines_Mensuel (Tour_ID INT PRIMARY KEY, Mois TEXT, "
            "Nb_Machines_Decoupe INT, Nb_Machines_Assemblage INT, Nb_Machines_Cond INT, "
            "Acquisition_Faite INT, Acq_Decoupe INT, Acq_Assemblage INT, Acq_Cond INT)"
        ))

        ajoutees = ajouter_colonnes_manquantes(conn, "Parc_Machines_Mensuel", COLONNES_CESSION_PARC)

        assert set(ajoutees) == set(COLONNES_CESSION_PARC)
        colonnes = colonnes_existantes(conn, "Parc_Machines_Mensuel")
        assert "cession_faite" in colonnes
        assert "px_cession_assemblage" in colonnes


def test_migration_is_idempotent_and_keeps_the_rows(sqlite_engine):
    with sqlite_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE Parc_Machines_Mensuel (Tour_ID INT PRIMARY KEY, Mois TEXT, "
            "Nb_Machines_Decoupe INT, Nb_Machines_Assemblage INT, Nb_Machines_Cond INT)"
        ))
        conn.execute(text(
            "INSERT INTO Parc_Machines_Mensuel (Tour_ID, Mois, Nb_Machines_Decoupe) VALUES (1, 'Janvier N+1', 10)"
        ))

        ajouter_colonnes_manquantes(conn, "Parc_Machines_Mensuel", COLONNES_CESSION_PARC)
        assert ajouter_colonnes_manquantes(conn, "Parc_Machines_Mensuel", COLONNES_CESSION_PARC) == []

        row = conn.execute(text(
            "SELECT Mois, Nb_Machines_Decoupe, Cession_Faite FROM Parc_Machines_Mensuel WHERE Tour_ID = 1"
        )).fetchone()
        assert row[0] == "Janvier N+1"
        assert row[1] == 10
        assert row[2] is None  # colonne ajoutée, valeur par défaut


def test_finances_table_migration_adds_the_cession_columns(sqlite_engine):
    with sqlite_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE Finances_Mensuelles (Tour_ID INT PRIMARY KEY, Mois VARCHAR(50), "
            "Resultat_Net FLOAT, Disponibilites_Banque FLOAT)"
        ))

        ajoutees = ajouter_colonnes_manquantes(conn, "Finances_Mensuelles", COLONNES_CESSION_FINANCES)

        assert set(ajoutees) == set(COLONNES_CESSION_FINANCES)
        colonnes = colonnes_existantes(conn, "Finances_Mensuelles")
        assert {"vnc_cession_ht", "tva_cession", "pmv_cession"} <= colonnes


def test_migration_respects_its_time_budget(sqlite_engine):
    """Budget épuisé : la migration s'arrête au lieu de bloquer l'application."""
    with sqlite_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE Parc_Machines_Mensuel (Tour_ID INT PRIMARY KEY, Mois TEXT, "
            "Nb_Machines_Decoupe INT)"
        ))

        ajoutees = ajouter_colonnes_manquantes(
            conn, "Parc_Machines_Mensuel", COLONNES_CESSION_PARC, budget_s=0.0
        )

        assert ajoutees == []  # budget nul : rien n'est tenté
        assert "cession_faite" not in colonnes_existantes(conn, "Parc_Machines_Mensuel")


def test_lock_bound_is_harmless_on_sqlite(sqlite_engine):
    """`SET SESSION lock_wait_timeout` n'existe pas sur SQLite : l'appel doit
    être ignoré silencieusement, pas faire échouer la migration."""
    with sqlite_engine.begin() as conn:
        conn.execute(text("CREATE TABLE T (id INT PRIMARY KEY)"))
        borner_attente_verrou(conn)  # ne doit pas lever
        ajoutees = ajouter_colonnes_manquantes(conn, "T", {"Col": "INT"})

        assert ajoutees == ["Col"]
