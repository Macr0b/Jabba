"""Mises à jour : rien ne s'installe sans une signature faite avec la clé attendue (revue, point 5)."""
import os
import shutil
import subprocess

import pytest

import maj
from conftest import exige_root, outil

pytestmark = [outil("git"), outil("ssh-keygen")]


def sh(*cmd, cwd=None, entree=None):
    return subprocess.run(cmd, cwd=cwd, input=entree, capture_output=True, text=True, check=True).stdout.strip()


def cle(dossier, nom):
    chemin = os.path.join(dossier, nom)
    sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", nom, "-f", chemin)
    return chemin, open(chemin + ".pub").read().strip()


def signer(cle_privee, tag, sha):
    return sh("ssh-keygen", "-Y", "sign", "-f", cle_privee, "-n", "jabba", entree=maj.texte_signe(tag, sha))


@pytest.fixture
def depots(tmp_path):
    """Un dépôt « GitHub » avec une version signée, et une installation (clone) sur une version plus ancienne."""
    origine = tmp_path / "origine"
    origine.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
    sh("git", "init", "-q", "-b", "main", cwd=origine)
    (origine / "app.txt").write_text("v1\n")
    sh(*git, "add", ".", cwd=origine)
    sh(*git, "commit", "-qm", "v1", cwd=origine)
    sh(*git, "tag", "-a", "v1.0.0", "-m", "v1", cwd=origine)
    install = tmp_path / "install"
    sh("git", "clone", "-q", str(origine), str(install))
    sh("git", "-c", "advice.detachedHead=false", "checkout", "-q", "v1.0.0", cwd=install)
    (origine / "app.txt").write_text("v2\n")
    sh(*git, "commit", "-qam", "v2", cwd=origine)
    sha = sh("git", "rev-parse", "HEAD", cwd=origine)
    bonne, pub = cle(tmp_path, "mainteneur")
    mauvaise, _ = cle(tmp_path, "pirate")
    # Faux docker : on note qu'il a été appelé (la reconstruction).
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path}/docker.log\n")
    os.chmod(bin_ / "docker", 0o755)
    return {"origine": origine, "install": install, "sha": sha, "bonne": bonne, "mauvaise": mauvaise,
            "pub": pub, "git": git, "tmp": tmp_path, "path": f"{bin_}:{os.environ['PATH']}"}


def publier(d, tag, signature=None, sur=None):
    msg = f"Jabba {tag}\n\n{signature}\n" if signature else f"Jabba {tag}\n"
    sh(*d["git"], "tag", "-a", "-f", tag, "-m", msg, *( [sur] if sur else []), cwd=d["origine"])


def installer(d, tag):
    script = maj.script(str(d["install"]), tag, d["pub"])
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env={**os.environ, "PATH": d["path"]})
    contenu = (d["install"] / "app.txt").read_text().strip()
    reconstruit = os.path.exists(d["tmp"] / "docker.log")
    return r, contenu, reconstruit


@exige_root
def test_version_signee_installee(depots):
    d = depots
    publier(d, "v2.0.0", signer(d["bonne"], "v2.0.0", d["sha"]), d["sha"])
    r, contenu, reconstruit = installer(d, "v2.0.0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Signature valable" in r.stdout
    assert contenu == "v2" and reconstruit


@exige_root
def test_version_sans_signature_refusee(depots):
    d = depots
    publier(d, "v2.0.0")
    r, contenu, reconstruit = installer(d, "v2.0.0")
    assert r.returncode != 0 and maj.MARQUE_REFUS in r.stdout
    assert contenu == "v1" and not reconstruit


@exige_root
def test_signature_d_une_autre_cle_refusee(depots):
    d = depots
    publier(d, "v2.0.0", signer(d["mauvaise"], "v2.0.0", d["sha"]), d["sha"])
    r, contenu, reconstruit = installer(d, "v2.0.0")
    assert r.returncode != 0 and maj.MARQUE_REFUS in r.stdout
    assert contenu == "v1" and not reconstruit


@exige_root
def test_signature_d_un_autre_commit_refusee(depots):
    """Un pirate réutilise une vraie signature, mais déplace l'étiquette sur son propre code."""
    d = depots
    signature = signer(d["bonne"], "v2.0.0", d["sha"])
    (d["origine"] / "app.txt").write_text("piège\n")
    sh(*d["git"], "commit", "-qam", "piège", cwd=d["origine"])
    publier(d, "v2.0.0", signature)
    r, contenu, reconstruit = installer(d, "v2.0.0")
    assert r.returncode != 0 and maj.MARQUE_REFUS in r.stdout
    assert contenu == "v1" and not reconstruit


@exige_root
def test_signature_d_une_autre_version_refusee(depots):
    """Une signature valable pour v2.0.0 ne vaut pas pour v9.0.0."""
    d = depots
    publier(d, "v9.0.0", signer(d["bonne"], "v2.0.0", d["sha"]), d["sha"])
    r, contenu, _ = installer(d, "v9.0.0")
    assert r.returncode != 0 and contenu == "v1"


@exige_root
def test_signature_pour_un_autre_usage_refusee(depots):
    """Une signature SSH faite pour autre chose (autre « namespace ») ne vaut rien."""
    d = depots
    autre = sh("ssh-keygen", "-Y", "sign", "-f", d["bonne"], "-n", "git", entree=maj.texte_signe("v2.0.0", d["sha"]))
    publier(d, "v2.0.0", autre, d["sha"])
    r, contenu, _ = installer(d, "v2.0.0")
    assert r.returncode != 0 and contenu == "v1"


def test_parametres_verifies():
    pub = "ssh-ed25519 " + "A" * 68 + " test"
    with pytest.raises(ValueError):
        maj.script("/opt/x; rm -rf /", "v1.0.0", pub)
    with pytest.raises(ValueError):
        maj.script("/opt/x", "v1.0.0; reboot", pub)
    with pytest.raises(ValueError):
        maj.script("/opt/x", "v1.0.0", None)
    with pytest.raises(ValueError):
        maj.script("/opt/x", "v1.0.0", "ssh-ed25519 AAAA' ; reboot ; '")


def test_cle_publication(tmp_path, monkeypatch):
    f = tmp_path / "cle.pub"
    monkeypatch.setattr(maj, "FICHIER_CLE", str(f))
    assert maj.cle_publication() is None
    f.write_text("# commentaire\n")
    assert maj.cle_publication() is None
    f.write_text("pas une clé\n")
    assert maj.cle_publication() is None
    bonne = "ssh-ed25519 " + "A" * 68 + " signature Jabba"
    f.write_text(f"# clé de publication\n{bonne}\n")
    assert maj.cle_publication() == bonne


def test_cle_livree_valide():
    """La clé livrée avec l'appli doit être lisible : sans elle, aucune mise à jour possible."""
    assert maj.cle_publication(), "app/cle_publication.pub manque ou est illisible"


def test_versions():
    assert maj.plus_recente("0.9.0", "0.10.0")
    assert not maj.plus_recente("0.10.0", "0.9.9")
    assert not maj.plus_recente("0.10.0", "pas-une-version")
