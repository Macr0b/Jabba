"""Structure du code : chaque import mène à un module qui existe, même dans une fonction rarement appelée."""
import ast
import glob
import importlib.util
import os
import sys

from conftest import APP


def _existe(module):
    """Le module existe-t-il ? (un module déjà chargé sans __spec__ compte comme existant)."""
    if module in sys.modules:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ValueError, ModuleNotFoundError):
        return False


def _imports(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, None
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                yield node.lineno, node.module, alias.name


def test_tous_les_imports_existent():
    manquants = []
    for path in glob.glob(os.path.join(APP, "**", "*.py"), recursive=True):
        for line, module, name in _imports(path):
            if not _existe(module):
                manquants.append(f"{os.path.relpath(path, APP)}:{line} {module}")
                continue
            # « from module import nom » : nom est un sous-module ou un objet du module.
            if name and not _existe(f"{module}.{name}") and not hasattr(importlib.import_module(module), name):
                manquants.append(f"{os.path.relpath(path, APP)}:{line} {module}.{name}")
    assert not manquants, manquants
