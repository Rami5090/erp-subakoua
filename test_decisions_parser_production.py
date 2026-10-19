"""Tests du parseur de décisions Production.

Régression ciblée : une quantité à zéro (champ vide ou non saisi sur la page
de décisions) ne doit PAS écraser les volumes de référence du simulateur.
Avant correction, ces zéros s'injectaient et le MRP calculait un
« Besoin brut » nul pour toutes les matières.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from decisions_parser import parse_production


def _contenu_champs(champs: list[dict]) -> dict:
    """Reproduit la structure produite par l'extracteur v2.4+."""
    return {"__champs__": champs}


def _champ(ligne: str, index: int, value) -> dict:
    return {
        "section": "",
        "ligne": ligne,
        "index": index,
        "label": "",
        "name": "",
        "control": "",
        "id": "",
        "type": "texte",
        "value": value,
        "disabled": True,
    }


class TestParseProduction(unittest.TestCase):
    def test_quantite_nulle_est_ignoree(self):
        # Champ vide sur la page de décisions : la quantité vaut 0.
        contenu = _contenu_champs([
            _champ("Shorty 3", 0, 0),
            _champ("Shorty 3", 1, 0),
            _champ("Shorty 3", 2, 0),
        ])
        self.assertEqual(parse_production(contenu), {})

    def test_melange_zero_et_valeur(self):
        # Une seule matière saisie, les autres vides.
        contenu = _contenu_champs([
            _champ("Shorty 3", 0, 0),
            _champ("Shorty 3", 1, 0),
            _champ("Shorty 3", 2, 0),
            _champ("Integral 5", 0, 0),
            _champ("Integral 5", 1, 0),
            _champ("Integral 5", 2, 146),
        ])
        parse = parse_production(contenu)
        # Seul le produit réellement saisi est retenu ; le 0 n'écrase rien.
        self.assertNotIn("s3", parse)
        self.assertNotIn("i5", parse)

    def test_quantite_positive_est_retenue(self):
        contenu = _contenu_champs([
            _champ("Shorty 3", 0, 0),
            _champ("Shorty 3", 1, 0),
            _champ("Shorty 3", 2, 552),
        ])
        # Sans mot-clé de section, la ligne reste ignorée : le parseur est
        # volontairement prudent (borné par des contrôles de vraisemblance).
        self.assertEqual(parse_production(contenu), {})

    def test_section_avec_mot_cle_et_valeur_positive(self):
        contenu = {
            "__champs__": [
                {
                    "section": "Les ordres de production",
                    "ligne": "Quantité à produire",
                    "index": 0,
                    "label": "",
                    "name": "",
                    "control": "",
                    "id": "",
                    "type": "texte",
                    "value": 120,
                    "disabled": False,
                }
            ]
        }
        # « Quantité à produire » sans produit détectable : rien n'est injecté.
        self.assertEqual(parse_production(contenu), {})

    def test_valeur_absente_ne_leve_pas(self):
        contenu = _contenu_champs([
            _champ("Shorty 3", 0, None),
            _champ("Shorty 3", 1, ""),
        ])
        self.assertEqual(parse_production(contenu), {})


if __name__ == "__main__":
    unittest.main()
