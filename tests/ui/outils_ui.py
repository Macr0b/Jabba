"""Petits outils communs aux tests de l'interface."""

import time


def attendre(page, expression, timeout=30000):
    """Attend qu'une expression JavaScript soit vraie (sans eval dans la page : la politique de sécurité l'interdit)."""
    fin = time.time() + timeout / 1000
    while time.time() < fin:
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"Toujours faux après {timeout} ms : {expression}")
