"""Clés d'accès (passkeys : Face ID, Touch ID, clé USB), par la bibliothèque « webauthn ».

Sommaire
- relying_party(host_header) : le domaine et les origines acceptées, d'après l'adresse ouverte.
- registration_options(...) / verify_registration(...) : ajouter une clé.
- authentication_options(...) / verify_authentication(...) : se connecter avec une clé.

Le serveur ne garde que la clé publique : l'empreinte du visage ou du doigt ne
quitte jamais le téléphone. Une clé est liée à l'adresse de l'appli (par exemple
le nom Tailscale du serveur) : si l'adresse change, on se connecte avec l'appli
d'authentification ou un code de secours, puis on ajoute une nouvelle clé.
"""

import json

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

RP_NAME = "Jabba"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "testserver")


def relying_party(host_header):
    """(rp_id, origines acceptées) pour l'adresse tapée dans le navigateur."""
    host = (host_header or "").strip().lower()
    rp_id = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    origins = ["https://" + host]
    if rp_id in LOCAL_HOSTS:
        origins.append("http://" + host)
    return rp_id, origins


def _descriptors(credential_ids):
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential_id)) for credential_id in credential_ids]


def registration_options(rp_id, user_handle, user_name, display_name, existing_ids):
    """(options pour le navigateur, défi à garder côté serveur)."""
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=user_handle.encode(),
        user_name=user_name,
        user_display_name=display_name or user_name,
        exclude_credentials=_descriptors(existing_ids),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return json.loads(options_to_json(options)), options.challenge


def verify_registration(credential, challenge, rp_id, origins):
    """La clé à enregistrer : {id, cle_publique, compteur}. Lève une exception si la réponse est fausse."""
    verified = verify_registration_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=origins,
        require_user_verification=True,
    )
    return {
        "id": bytes_to_base64url(verified.credential_id),
        "cle_publique": bytes_to_base64url(verified.credential_public_key),
        "compteur": verified.sign_count,
    }


def authentication_options(rp_id, credential_ids):
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=_descriptors(credential_ids),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return json.loads(options_to_json(options)), options.challenge


def verify_authentication(credential, challenge, rp_id, origins, stored_key):
    """Le nouveau compteur de la clé. Lève une exception si la signature est fausse."""
    verified = verify_authentication_response(
        credential=credential,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=origins,
        credential_public_key=base64url_to_bytes(stored_key["cle_publique"]),
        credential_current_sign_count=stored_key.get("compteur", 0),
        require_user_verification=True,
    )
    return verified.new_sign_count
