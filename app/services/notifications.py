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

from services import activity_log, json_files

DATA_DIR = os.environ.get("JABBA_DATA", "/data")
KEY_FILE = os.path.join(DATA_DIR, "vapid.pem")
SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "notifs.json")
_LOCK = threading.Lock()

TYPES = {
    "courant": ("Coupure de courant", "Coupure, retour du courant, batterie faible et arrêt propre."),
    "disque_plein": ("Disque presque plein", "Quand un disque dépasse 90 %."),
    "outil_panne": ("Outil en panne", "Quand un outil plante, puis quand il repart."),
    "sante_disques": ("Santé des disques", "Quand un disque montre des signes de fatigue (lecture SMART chaque jour)."),
    "nouveau_disque": ("Nouveau disque", "Quand un disque branché est ajouté tout seul, ou s'il est illisible."),
    "mises_a_jour": ("Mises à jour", "Nouvelle version de l'appli, ou redémarrage en attente."),
    "securite": (
        "Sécurité",
        "Mot de passe raté 5 fois, mot de passe changé, accès refusé à un inconnu, suppression massive de fichiers.",
    ),
    "sauvegardes": ("Sauvegardes", "Sauvegarde échouée, incomplète, ou en attente du disque."),
    "comptes": ("Comptes", "Demande de mot de passe oublié, compte ajouté ou retiré, rang changé."),
    "atelier": ("Entretien des machines", "Quand un rappel d'entretien de la mémoire d'atelier arrive à échéance."),
}


def _read():
    data = json_files.load(SUBSCRIPTIONS_FILE)
    data.setdefault("abonnements", [])
    data.setdefault("types", {alert_type: True for alert_type in TYPES})
    for alert_type in TYPES:
        data["types"].setdefault(alert_type, True)
    return data


def _write(data):
    json_files.save(SUBSCRIPTIONS_FILE, data)


def _b64(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode()


def public_key():
    """Clé publique à donner au navigateur. La clé privée est créée au premier appel."""
    with _LOCK:
        if not os.path.exists(KEY_FILE):
            os.makedirs(DATA_DIR, exist_ok=True)
            private_key = ec.generate_private_key(ec.SECP256R1())
            pem = private_key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
            descriptor = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(pem)
        with open(KEY_FILE, "rb") as file:
            private_key = serialization.load_pem_private_key(file.read(), None)
    public = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64(public)


def settings():
    data = _read()
    return {
        "cle": public_key(),
        "appareils": len(data["abonnements"]),
        "endpoints": [subscription["endpoint"] for subscription in data["abonnements"]],
        "types": [
            {"id": alert_type, "nom": name, "detail": entry, "actif": data["types"][alert_type]}
            for alert_type, (name, entry) in TYPES.items()
        ],
    }


def subscribe(new_subscription, contact, device="", who=None):
    if not isinstance(new_subscription, dict) or not str(new_subscription.get("endpoint", "")).startswith("https://"):
        raise ValueError("Abonnement invalide.")
    keys = new_subscription.get("keys") or {}
    if not (keys.get("p256dh") and keys.get("auth")):
        raise ValueError("Abonnement invalide.")
    with _LOCK:
        data = _read()
        data["abonnements"] = [
            subscription
            for subscription in data["abonnements"]
            if subscription["endpoint"] != new_subscription["endpoint"]
        ]
        data["abonnements"].append(
            {
                "endpoint": new_subscription["endpoint"],
                "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
                "contact": contact,
                "appareil": device[:80],
                "qui": who,
                "depuis": int(time.time()),
            }
        )
        _write(data)


def unsubscribe(endpoint):
    with _LOCK:
        data = _read()
        data["abonnements"] = [
            subscription for subscription in data["abonnements"] if subscription["endpoint"] != endpoint
        ]
        _write(data)


def set_types(media_type, active):
    if media_type not in TYPES:
        raise ValueError("Type inconnu.")
    with _LOCK:
        data = _read()
        data["types"][media_type] = bool(active)
        _write(data)


def send(media_type, title, text, url="/", force=False, family="alerte", recipients=None, log=True):
    """Note l'événement au journal, puis l'envoie aux appareils si ce type est activé.

    `pour` : ensemble de comptes Tailscale ; seuls leurs appareils reçoivent l'alerte.
    """
    if log:
        activity_log.record(family, f"{title} — {text}" if text else title)
    data = _read()
    if not force and not data["types"].get(media_type, True):
        return 0
    targets = [
        subscription
        for subscription in data["abonnements"]
        if recipients is None or subscription.get("qui") in recipients
    ]
    if not targets:
        return 0
    threading.Thread(target=_push, args=(targets, title, text, url), daemon=True).start()
    return len(targets)


def valid_contact(contact):
    """Adresse de contact acceptée par la signature des alertes : https://hôte (sans port) ou mailto:."""
    from urllib.parse import urlparse

    if contact and contact.startswith("https://"):
        host_runner = urlparse(contact).hostname
        if host_runner:
            return "https://" + host_runner
    if contact and contact.startswith("mailto:") and "@" in contact:
        return contact
    return "mailto:jabba@example.org"


def _push(subscriptions, title, text, url):
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return
    public_key()
    message = json.dumps({"titre": title, "texte": text, "url": url})
    expired = []
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={"endpoint": subscription["endpoint"], "keys": subscription["keys"]},
                data=message,
                vapid_private_key=KEY_FILE,
                vapid_claims={"sub": valid_contact(subscription.get("contact"))},
                ttl=24 * 3600,
                timeout=15,
            )
        except WebPushException as error:
            code = getattr(error.response, "status_code", None)
            if code in (404, 410):
                expired.append(subscription["endpoint"])
            else:
                print("alerte refusée :", code, getattr(error.response, "text", error), flush=True)
        except Exception as error:  # noqa: BLE001 — une alerte ratée ne doit jamais faire tomber l'appli
            print("alerte non envoyée :", type(error).__name__, error, flush=True)
    for expired_list in expired:
        unsubscribe(expired_list)
