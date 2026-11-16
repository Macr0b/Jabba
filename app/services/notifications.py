"""Alertes sur l'iPhone et le Mac (Web Push), réglables type par type.

Les clés (VAPID) sont créées sur le serveur au premier lancement et ne le
quittent jamais. Les appareils abonnés et les réglages restent dans /data.
"""
import base64
import json
import os
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from services import activity_log

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
KEY_FILE = os.path.join(DATA_DIR, "vapid.pem")
SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "notifs.json")
_LOCK = threading.Lock()

TYPES = {
    "courant": ("Coupure de courant", "Coupure, retour du courant, batterie faible et arrêt propre."),
    "disque_plein": ("Disque presque plein", "Quand un disque dépasse 90 %."),
    "outil_panne": ("Outil en panne", "Quand un outil plante, puis quand il repart."),
    "nouveau_disque": ("Nouveau disque", "Quand un disque branché est ajouté tout seul, ou s'il est illisible."),
    "mises_a_jour": ("Mises à jour", "Nouvelle version de l'appli, ou redémarrage en attente."),
    "securite": ("Sécurité", "Mot de passe raté 5 fois, mot de passe changé, accès refusé à un inconnu."),
    "sauvegardes": ("Sauvegardes", "Sauvegarde échouée, incomplète, ou en attente du disque."),
    "comptes": ("Comptes", "Demande de mot de passe oublié, compte ajouté ou retiré, rang changé."),
}


def _read():
    try:
        with open(SUBSCRIPTIONS_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("abonnements", [])
    d.setdefault("types", {t: True for t in TYPES})
    for t in TYPES:
        d["types"].setdefault(t, True)
    return d


def _write(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUBSCRIPTIONS_FILE + ".tmp", "w") as f:
        json.dump(d, f)
    os.replace(SUBSCRIPTIONS_FILE + ".tmp", SUBSCRIPTIONS_FILE)


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def public_key():
    """Clé publique à donner au navigateur. La clé privée est créée au premier appel."""
    with _LOCK:
        if not os.path.exists(KEY_FILE):
            os.makedirs(DATA_DIR, exist_ok=True)
            k = ec.generate_private_key(ec.SECP256R1())
            pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
            fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
        with open(KEY_FILE, "rb") as f:
            k = serialization.load_pem_private_key(f.read(), None)
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(pub)


def settings():
    d = _read()
    return {
        "cle": public_key(),
        "appareils": len(d["abonnements"]),
        "endpoints": [a["endpoint"] for a in d["abonnements"]],
        "types": [{"id": t, "nom": n, "detail": x, "actif": d["types"][t]} for t, (n, x) in TYPES.items()],
    }


def subscribe(abonnement, contact, appareil="", qui=None):
    if not isinstance(abonnement, dict) or not str(abonnement.get("endpoint", "")).startswith("https://"):
        raise ValueError("Abonnement invalide.")
    cles = abonnement.get("keys") or {}
    if not (cles.get("p256dh") and cles.get("auth")):
        raise ValueError("Abonnement invalide.")
    with _LOCK:
        d = _read()
        d["abonnements"] = [a for a in d["abonnements"] if a["endpoint"] != abonnement["endpoint"]]
        d["abonnements"].append({"endpoint": abonnement["endpoint"], "keys": {"p256dh": cles["p256dh"], "auth": cles["auth"]},
                                 "contact": contact, "appareil": appareil[:80], "qui": qui, "depuis": int(time.time())})
        _write(d)


def unsubscribe(endpoint):
    with _LOCK:
        d = _read()
        d["abonnements"] = [a for a in d["abonnements"] if a["endpoint"] != endpoint]
        _write(d)


def set_types(type_, actif):
    if type_ not in TYPES:
        raise ValueError("Type inconnu.")
    with _LOCK:
        d = _read()
        d["types"][type_] = bool(actif)
        _write(d)


def send(type_, titre, texte, url="/", forcer=False, famille="alerte", pour=None, noter=True):
    """Note l'événement au journal, puis l'envoie aux appareils si ce type est activé.

    `pour` : ensemble de comptes Tailscale ; seuls leurs appareils reçoivent l'alerte.
    """
    if noter:
        activity_log.record(famille, f"{titre} — {texte}" if texte else titre)
    d = _read()
    if not forcer and not d["types"].get(type_, True):
        return 0
    cibles = [a for a in d["abonnements"] if pour is None or a.get("qui") in pour]
    if not cibles:
        return 0
    threading.Thread(target=_push, args=(cibles, titre, texte, url), daemon=True).start()
    return len(cibles)


def valid_contact(contact):
    """Adresse de contact acceptée par la signature des alertes : https://hôte (sans port) ou mailto:."""
    from urllib.parse import urlparse
    if contact and contact.startswith("https://"):
        hote = urlparse(contact).hostname
        if hote:
            return "https://" + hote
    if contact and contact.startswith("mailto:") and "@" in contact:
        return contact
    return "mailto:jabba@example.org"


def _push(abonnements, titre, texte, url):
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return
    public_key()
    message = json.dumps({"titre": titre, "texte": texte, "url": url})
    perimes = []
    for a in abonnements:
        try:
            webpush(subscription_info={"endpoint": a["endpoint"], "keys": a["keys"]}, data=message,
                    vapid_private_key=KEY_FILE, vapid_claims={"sub": valid_contact(a.get("contact"))},
                    ttl=24 * 3600, timeout=15)
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):
                perimes.append(a["endpoint"])
            else:
                print("alerte refusée :", code, getattr(e.response, "text", e), flush=True)
        except Exception as e:  # noqa: BLE001 — une alerte ratée ne doit jamais faire tomber l'appli
            print("alerte non envoyée :", type(e).__name__, e, flush=True)
    for p in perimes:
        unsubscribe(p)
