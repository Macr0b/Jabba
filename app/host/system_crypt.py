"""Vérification d'un mot de passe Linux (empreinte de /etc/shadow), sans le module « crypt ».

Le module Python « crypt » disparaît avec Python 3.13. On appelle directement la
bibliothèque du système (libcrypt, présente sur toute Debian), qui connaît tous les
formats d'empreinte : yescrypt ($y$, Debian 11 et après), SHA-512 ($6$), etc.
"""

import ctypes
import ctypes.util
import threading

_lock = threading.Lock()  # crypt() n'est pas réentrant : un appel à la fois
_library = None


def _crypt_function():
    """La fonction crypt() de libcrypt, chargée une seule fois."""
    global _library
    if _library is None:
        name = ctypes.util.find_library("crypt") or "libcrypt.so.1"
        _library = ctypes.CDLL(name)
        _library.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        _library.crypt.restype = ctypes.c_char_p
    return _library.crypt


def hash_with_setting(password, setting):
    """Empreinte de `password` avec le même sel et le même format que `setting` ; None si impossible."""
    try:
        crypt = _crypt_function()
    except OSError:
        return None
    with _lock:
        result = crypt(password.encode(), setting.encode())
    if not result or result.startswith(b"*"):
        return None
    return result.decode()
