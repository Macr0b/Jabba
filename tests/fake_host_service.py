"""Le service privilégié pour les tests, sans prise (voir faux_service)."""

import json


def fake_service(monkeypatch, runner):
    """Le service privilégié sans prise : chaque demande passe par host_service.handle (les vraies
    vérifications), puis `runner(commande, délai, entrée)` joue la commande à la place du serveur."""
    from host import commands, host_service

    monkeypatch.setattr(
        commands, "_ask_service", lambda request, timeout: host_service.handle(json.dumps(request), runner)
    )
