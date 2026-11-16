"""Une fausse clé d'accès (comme Face ID), pour tester la double vérification sans téléphone.

Elle fabrique les réponses que le navigateur renvoie (format « none », algorithme ES256).
"""

import base64
import hashlib
import json
import secrets
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class CleLogicielle:
    def __init__(self, rp_id):
        self.rp_id = rp_id
        self.cle_privee = ec.generate_private_key(ec.SECP256R1())
        self.identifiant = secrets.token_bytes(16)
        self.compteur = 0

    def _client_data(self, sorte, defi, origine):
        return json.dumps({"type": sorte, "challenge": defi, "origin": origine}).encode()

    def creer(self, options, origine):
        numeros = self.cle_privee.public_key().public_numbers()
        cle_cose = cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: numeros.x.to_bytes(32, "big"), -3: numeros.y.to_bytes(32, "big")}
        )
        donnees = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([0x45])  # présence + vérification de la personne + clé jointe
            + struct.pack(">I", self.compteur)
            + bytes(16)
            + struct.pack(">H", len(self.identifiant))
            + self.identifiant
            + cle_cose
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": donnees})
        return {
            "id": b64url(self.identifiant),
            "rawId": b64url(self.identifiant),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(self._client_data("webauthn.create", options["challenge"], origine)),
                "attestationObject": b64url(attestation),
            },
        }

    def signer(self, options, origine, rp_id=None):
        self.compteur += 1
        donnees = (
            hashlib.sha256((rp_id or self.rp_id).encode()).digest() + bytes([0x05]) + struct.pack(">I", self.compteur)
        )
        client = self._client_data("webauthn.get", options["challenge"], origine)
        signature = self.cle_privee.sign(donnees + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64url(self.identifiant),
            "rawId": b64url(self.identifiant),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client),
                "authenticatorData": b64url(donnees),
                "signature": b64url(signature),
            },
        }
