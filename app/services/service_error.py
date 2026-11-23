"""L'erreur commune des services : un code (comme en HTTP) et une phrase pour l'usager.

Les services ne dépendent pas du serveur web : ils lèvent ServiceError, et main.py la change en
réponse {"detail": phrase} avec le code donné, comme le ferait une HTTPException.

Sommaire :
  ServiceError : l'erreur, avec .status (code) et .message (phrase)
"""


class ServiceError(Exception):
    """Ce que le service n'a pas pu faire : code (400, 404, 409…) et phrase pour l'usager."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message
