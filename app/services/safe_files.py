"""Écriture sûre d'un fichier envoyé : jamais par-dessus un fichier existant, jamais à travers un raccourci.

Sommaire :
  open_new_file : crée le fichier, ou échoue s'il existe déjà (même un raccourci)
  free_space : place libre sur le disque d'un dossier
  copy_with_limit : recopie un envoi en s'arrêtant au-delà d'une taille ou d'une place libre minimale
  TooLarge  : l'envoi dépasse la taille permise
  DiskFull  : l'envoi entamerait la place libre qu'on garde toujours sur le disque
"""

import os
import shutil

CHUNK_SIZE = 1024 * 1024


class TooLarge(Exception):
    """L'envoi dépasse la taille permise."""


class DiskFull(Exception):
    """L'envoi entamerait la place libre qu'on garde toujours sur le disque."""


def open_new_file(path):
    """Ouvre `path` en écriture, seulement s'il n'existe pas encore (O_EXCL) et sans suivre de raccourci."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    return os.fdopen(descriptor, "wb")


def free_space(folder):
    return shutil.disk_usage(folder).free


def copy_with_limit(source, output, folder, max_bytes, keep_free_bytes):
    """Recopie `source` dans `output` ; lève TooLarge ou DiskFull avant d'en écrire trop."""
    written = 0
    while chunk := source.read(CHUNK_SIZE):
        written += len(chunk)
        if max_bytes is not None and written > max_bytes:
            raise TooLarge()
        if free_space(folder) - len(chunk) < keep_free_bytes:
            raise DiskFull()
        output.write(chunk)
    return written
