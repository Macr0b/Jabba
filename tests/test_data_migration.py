"""Les données d'avant la 0.17 passent en anglais au démarrage, une seule fois, avec une copie avant."""

import json
import os

import data_migration


def write(path, data, lines=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as file:
        if lines:
            file.write("".join(json.dumps(line) + "\n" for line in data))
        else:
            json.dump(data, file)


def read(path):
    with open(path) as file:
        return json.load(file)


def test_keys_renamed_at_every_depth():
    old = {"comptes": [{"identifiant": "rob", "nom": "Rob", "rang": "super", "cree": 1}], "demandes": []}
    assert data_migration.convert(old) == {
        "accounts": [{"username": "rob", "name": "Rob", "rank": "super", "created": 1}],
        "requests": [],
    }


def test_values_never_change():
    old = {"etat": "actif", "famille": "disque", "texte": "nom"}
    assert data_migration.convert(old) == {"state": "actif", "family": "disque", "text": "nom"}


def test_new_name_wins_when_both_are_there():
    assert data_migration.convert({"etat": "ancien", "state": "neuf"}) == {"state": "neuf"}


def test_names_of_choices_are_kept():
    """Types d'alertes et sources des sauvegardes : leurs clés sont des noms de choix, pas des clés à traduire."""
    old = {"types": {"comptes": True, "atelier": False}, "sources": {"fichiers": True, "systeme": False}}
    assert data_migration.convert(old) == old


def test_files_keyed_by_names(tmp_path):
    """Écrans, comptes, outils : le premier niveau, ce sont des noms ; on convertit seulement ce qu'il y a dessous."""
    write(tmp_path / "rangement.json", {"outils": {"ordre": ["a"], "masques": []}, "systeme": {"ordre": []}})
    data_migration.run(str(tmp_path))
    assert read(tmp_path / "rangement.json") == {
        "outils": {"order": ["a"], "hidden_items": []},
        "systeme": {"order": []},
    }


def test_run_converts_once_and_keeps_a_copy(tmp_path):
    write(tmp_path / "atelier.json", {"machines": [{"nom": "Laser", "modele": "Speedy 360"}]})
    write(tmp_path / "porte-wifi" / "journal.jsonl", [{"t": 1, "famille": "invite", "texte": "entré"}], lines=True)
    write(tmp_path / "vignettes" / "x.json", {"nom": "pas touché"})
    os.chmod(tmp_path / "atelier.json", 0o600)
    assert sorted(data_migration.run(str(tmp_path))) == ["atelier.json", "porte-wifi/journal.jsonl"]
    assert read(tmp_path / "atelier.json") == {"machines": [{"name": "Laser", "model": "Speedy 360"}]}
    assert oct(os.stat(tmp_path / "atelier.json").st_mode & 0o777) == "0o600"
    log = (tmp_path / "porte-wifi" / "journal.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in log] == [{"t": 1, "family": "invite", "text": "entré"}]
    copy = tmp_path / data_migration.COPY_FOLDER
    assert read(copy / "atelier.json") == {"machines": [{"nom": "Laser", "modele": "Speedy 360"}]}
    assert (copy / "porte-wifi" / "journal.jsonl").exists()
    assert read(tmp_path / "vignettes" / "x.json") == {"nom": "pas touché"}
    assert data_migration.run(str(tmp_path)) == []  # la deuxième fois : rien à faire


def test_unreadable_file_left_alone(tmp_path):
    (tmp_path / "abime.json").write_text("{pas du json")
    assert data_migration.run(str(tmp_path)) == []
    assert (tmp_path / "abime.json").read_text() == "{pas du json"


def test_every_renamed_key_is_unique_and_final():
    """Aucune nouvelle clé n'est elle-même renommée : convertir deux fois ne change plus rien."""
    renamed = data_migration.RENAMED_KEYS
    assert not set(renamed.values()) & set(renamed)
