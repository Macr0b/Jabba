"""Structure du code : chaque import mène à un module qui existe, même dans une fonction rarement appelée."""
import ast
import glob
import importlib.util
import os

from conftest import APP


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
            if importlib.util.find_spec(module.split(".")[0]) is None:
                manquants.append(f"{os.path.relpath(path, APP)}:{line} {module}")
                continue
            if name and importlib.util.find_spec(module) is not None:
                spec = importlib.util.find_spec(module)
                # « from paquet import module » : le module doit exister dans le paquet.
                if spec.submodule_search_locations and importlib.util.find_spec(f"{module}.{name}") is None:
                    mod = importlib.import_module(module)
                    if not hasattr(mod, name):
                        manquants.append(f"{os.path.relpath(path, APP)}:{line} {module}.{name}")
    assert not manquants, manquants
