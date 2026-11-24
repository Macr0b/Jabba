"""Mises à jour : rien ne s'installe sans une signature faite avec la clé attendue (revue, point 5)."""

import json
import os
import subprocess

import pytest
from conftest import needs_root, outil

import version
from services import app_update

pytestmark = [outil("git"), outil("ssh-keygen")]


def sh(*cmd, cwd=None, entry=None):
    return subprocess.run(cmd, cwd=cwd, input=entry, capture_output=True, text=True, check=True).stdout.strip()


def key(dossier, nom):
    chemin = os.path.join(dossier, nom)
    sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", nom, "-f", chemin)
    return chemin, open(chemin + ".pub").read().strip()


def sign_with(private_key, tag, sha):
    return sh("ssh-keygen", "-Y", "sign", "-f", private_key, "-n", "jabba", entry=app_update.signed_text(tag, sha))


@pytest.fixture
def drops(tmp_path):
    """Un dépôt « GitHub » avec une version signée, et une installation (clone) sur une version plus ancienne."""
    origin = tmp_path / "origine"
    origin.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
    sh("git", "init", "-q", "-b", "main", cwd=origin)
    (origin / "app.txt").write_text("v1\n")
    sh(*git, "add", ".", cwd=origin)
    sh(*git, "commit", "-qm", "v1", cwd=origin)
    sh(*git, "tag", "-a", "v1.0.0", "-m", "v1", cwd=origin)
    install = tmp_path / "install"
    sh("git", "clone", "-q", str(origin), str(install))
    sh("git", "-c", "advice.detachedHead=false", "checkout", "-q", "v1.0.0", cwd=install)
    (origin / "app.txt").write_text("v2\n")
    sh(*git, "commit", "-qam", "v2", cwd=origin)
    sha = sh("git", "rev-parse", "HEAD", cwd=origin)
    right, pub = key(tmp_path, "mainteneur")
    wrong, _ = key(tmp_path, "pirate")
    # Faux docker : on note qu'il a été appelé (la reconstruction).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/docker.log\n')
    os.chmod(bin_dir / "docker", 0o755)
    return {
        "origin": origin,
        "install": install,
        "sha": sha,
        "bonne": right,
        "mauvaise": wrong,
        "pub": pub,
        "git": git,
        "tmp": tmp_path,
        "path": f"{bin_dir}:{os.environ['PATH']}",
    }


def publish(d, tag, signature=None, over=None, line=None):
    """Publie `tag` : l'étiquette, et sur main le fichier signatures/<tag>.sig (ligne signée + signature)."""
    over = over or d["sha"]
    sh(*d["git"], "tag", "-f", tag, over, cwd=d["origin"])
    if signature is not None:
        dossier = d["origin"] / "signatures"
        dossier.mkdir(exist_ok=True)
        (dossier / f"{tag}.sig").write_text((line or app_update.signed_text(tag, over)) + "\n" + signature + "\n")
        sh(*d["git"], "add", ".", cwd=d["origin"])
        sh(*d["git"], "commit", "-qm", f"Signature {tag}", cwd=d["origin"])


def install_tool(d, tag):
    script = app_update.install_script(str(d["install"]), tag, d["pub"])
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, env={**os.environ, "PATH": d["path"]})
    content = (d["install"] / "app.txt").read_text().strip()
    rebuilt = os.path.exists(d["tmp"] / "docker.log")
    return r, content, rebuilt


@needs_root
def test_signed_version_installed(drops):
    d = drops
    publish(d, "v2.0.0", sign_with(d["bonne"], "v2.0.0", d["sha"]), d["sha"])
    r, content, rebuilt = install_tool(d, "v2.0.0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Signature valable" in r.stdout
    assert content == "v2" and rebuilt


@needs_root
def test_unsigned_version_refused(drops):
    d = drops
    publish(d, "v2.0.0")
    r, content, rebuilt = install_tool(d, "v2.0.0")
    assert r.returncode != 0 and app_update.REFUSAL_MARK in r.stdout
    assert content == "v1" and not rebuilt


@needs_root
def test_signature_from_another_key_refused(drops):
    d = drops
    publish(d, "v2.0.0", sign_with(d["mauvaise"], "v2.0.0", d["sha"]), d["sha"])
    r, content, rebuilt = install_tool(d, "v2.0.0")
    assert r.returncode != 0 and app_update.REFUSAL_MARK in r.stdout
    assert content == "v1" and not rebuilt


@needs_root
def test_modified_signed_line_refused(drops):
    """Un pirate garde la vraie signature, mais change le commit dans la ligne : la signature ne colle plus."""
    d = drops
    signature = sign_with(d["bonne"], "v2.0.0", d["sha"])
    (d["origin"] / "app.txt").write_text("piège\n")
    sh(*d["git"], "commit", "-qam", "piège", cwd=d["origin"])
    trap = sh("git", "rev-parse", "HEAD", cwd=d["origin"])
    publish(d, "v2.0.0", signature, trap)
    r, content, rebuilt = install_tool(d, "v2.0.0")
    assert r.returncode != 0 and app_update.REFUSAL_MARK in r.stdout
    assert content == "v1" and not rebuilt


@needs_root
def test_moved_tag_has_no_effect(drops):
    """L'étiquette pointe sur un autre code : on installe quand même exactement le commit signé."""
    d = drops
    signature = sign_with(d["bonne"], "v2.0.0", d["sha"])
    (d["origin"] / "app.txt").write_text("piège\n")
    sh(*d["git"], "commit", "-qam", "piège", cwd=d["origin"])
    trap = sh("git", "rev-parse", "HEAD", cwd=d["origin"])
    publish(d, "v2.0.0", signature)  # fichier signé : le bon commit
    sh(*d["git"], "tag", "-f", "v2.0.0", trap, cwd=d["origin"])
    r, content, _ = install_tool(d, "v2.0.0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert content == "v2"


@needs_root
def test_signature_of_another_version_refused(drops):
    """Une signature valable pour v2.0.0 ne vaut pas pour v9.0.0."""
    d = drops
    publish(
        d,
        "v9.0.0",
        sign_with(d["bonne"], "v2.0.0", d["sha"]),
        d["sha"],
        line=app_update.signed_text("v2.0.0", d["sha"]),
    )
    r, content, _ = install_tool(d, "v9.0.0")
    assert r.returncode != 0 and content == "v1"


@needs_root
def test_signature_for_another_purpose_refused(drops):
    """Une signature SSH faite pour autre chose (autre « namespace ») ne vaut rien."""
    d = drops
    other = sh(
        "ssh-keygen", "-Y", "sign", "-f", d["bonne"], "-n", "git", entry=app_update.signed_text("v2.0.0", d["sha"])
    )
    publish(d, "v2.0.0", other, d["sha"])
    r, content, _ = install_tool(d, "v2.0.0")
    assert r.returncode != 0 and content == "v1"


def test_parameters_checked():
    pub = "ssh-ed25519 " + "A" * 68 + " test"
    with pytest.raises(ValueError):
        app_update.install_script("/opt/x; rm -rf /", "v1.0.0", pub)
    with pytest.raises(ValueError):
        app_update.install_script("/opt/x", "v1.0.0; reboot", pub)
    with pytest.raises(ValueError):
        app_update.install_script("/opt/x", "v1.0.0", None)
    with pytest.raises(ValueError):
        app_update.install_script("/opt/x", "v1.0.0", "ssh-ed25519 AAAA' ; reboot ; '")


def test_publishing_key(tmp_path, monkeypatch):
    f = tmp_path / "cle.pub"
    monkeypatch.setattr(app_update, "KEY_FILE", str(f))
    assert app_update.publication_key() is None
    f.write_text("# commentaire\n")
    assert app_update.publication_key() is None
    f.write_text("pas une clé\n")
    assert app_update.publication_key() is None
    right = "ssh-ed25519 " + "A" * 68 + " signature Jabba"
    f.write_text(f"# clé de publication\n{right}\n")
    assert app_update.publication_key() == right


def test_shipped_key_is_valid():
    """La clé livrée avec l'appli doit être lisible : sans elle, aucune mise à jour possible."""
    assert app_update.publication_key(), "app/cle_publication.pub manque ou est illisible"


def test_versions():
    assert app_update.is_newer("0.9.0", "0.10.0")
    assert not app_update.is_newer("0.10.0", "0.9.9")
    assert not app_update.is_newer("0.10.0", "pas-une-version")


def test_version_journal_matches_the_screen():
    """Le journal des versions (Système) lit « version », « date » et « changes » ; la publication aussi."""
    with open(os.path.join(os.path.dirname(__file__), "..", "app", "versions.json")) as file:
        entries = json.load(file)
    assert all(set(entry) == {"version", "date", "changes"} and entry["changes"] for entry in entries)
    assert entries[0]["version"] == version.VERSION
