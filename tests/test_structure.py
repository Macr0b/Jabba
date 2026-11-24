"""Structure du code : chaque import mène à un module qui existe, même dans une fonction rarement appelée."""

import ast
import glob
import importlib.util
import os
import sys

from conftest import APP


def _exists(module):
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


def test_every_import_exists():
    missing_ones = []
    for path in glob.glob(os.path.join(APP, "**", "*.py"), recursive=True):
        for line, module, name in _imports(path):
            if not _exists(module):
                missing_ones.append(f"{os.path.relpath(path, APP)}:{line} {module}")
                continue
            # « from module import nom » : nom est un sous-module ou un objet du module.
            if name and not _exists(f"{module}.{name}") and not hasattr(importlib.import_module(module), name):
                missing_ones.append(f"{os.path.relpath(path, APP)}:{line} {module}.{name}")
    assert not missing_ones, missing_ones


def _project_modules(tree):
    """Noms locaux -> modules de l'appli importés dans ce fichier (« from services import backups »)."""
    project = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if _exists(full) and os.path.exists(os.path.join(APP, *full.split(".")) + ".py"):
                    project[alias.asname or alias.name] = full
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if os.path.exists(os.path.join(APP, *alias.name.split(".")) + ".py"):
                    project[alias.asname or alias.name] = alias.name
    return project


def test_every_module_dot_name_exists():
    """« backups.lire(...) » : la fonction lire existe bien dans le module backups (même hors démo)."""
    missing = []
    for path in glob.glob(os.path.join(APP, "**", "*.py"), recursive=True):
        tree = ast.parse(open(path, encoding="utf-8").read())
        project = _project_modules(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in project:
                module = importlib.import_module(project[node.value.id])
                if not hasattr(module, node.attr):
                    missing.append(f"{os.path.relpath(path, APP)}:{node.lineno} {node.value.id}.{node.attr}")
    assert not missing, missing


def test_keyword_parameters_exist():
    """« guard.require_admin(request, strong=True) » : le paramètre strong existe bien dans la fonction appelée."""
    errors = []
    for path in glob.glob(os.path.join(APP, "**", "*.py"), recursive=True):
        tree = ast.parse(open(path, encoding="utf-8").read())
        project = _project_modules(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):  # fonction du même fichier
                module_tree, name = tree, node.func.id
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in project
            ):
                module_path = os.path.join(APP, *project[node.func.value.id].split(".")) + ".py"
                module_tree, name = ast.parse(open(module_path, encoding="utf-8").read()), node.func.attr
            else:
                continue
            function = next(
                (
                    f
                    for f in module_tree.body
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == name
                ),
                None,
            )
            if function is None or function.args.kwarg:
                continue
            names_ = {a.arg for a in function.args.args + function.args.kwonlyargs}
            for keyword in node.keywords:
                if keyword.arg and keyword.arg not in names_:
                    errors.append(f"{os.path.relpath(path, APP)}:{node.lineno} {name}({keyword.arg}=…)")
    assert not errors, errors
