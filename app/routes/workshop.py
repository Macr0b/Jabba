"""API de la mémoire d'atelier (outil de l'appli, à activer dans l'Établi).

Sommaire :
  GET    /api/atelier                                  workshop_list (machines ; 404 si l'outil n'est pas activé)
  POST   /api/atelier/machines                         add_machine
  GET    /api/atelier/machines/{id}                    show_machine (journal et rappels)
  PATCH  /api/atelier/machines/{id}                    edit_machine
  DELETE /api/atelier/machines/{id}                    remove_machine
  POST   /api/atelier/machines/{id}/journal            add_entry
  PATCH  /api/atelier/machines/{id}/journal/{entree}   edit_entry (corriger une note)
  DELETE /api/atelier/machines/{id}/journal/{entree}   remove_entry
  POST   /api/atelier/machines/{id}/rappels            add_reminder
  POST   /api/atelier/rappels/{rappel}/fait            reminder_done
  DELETE /api/atelier/rappels/{rappel}                 remove_reminder
  interne : _ready, _run
"""

from fastapi import APIRouter, HTTPException, Request

from security import guard
from services import activity_log, workshop

router = APIRouter()


def _ready(request):
    guard.require_admin(request)
    if not workshop.is_enabled():
        raise HTTPException(404, "La mémoire d'atelier n'est pas activée (Établi).")


def _run(action, *args):
    try:
        return action(*args)
    except ValueError as error:
        raise HTTPException(400, str(error))
    except KeyError as error:
        raise HTTPException(404, f"Introuvable : {error.args[0]}.")


@router.get("/api/atelier")
def workshop_list(request: Request):
    _ready(request)
    return {"machines": workshop.machines(), "types": workshop.ENTRY_TYPES}


@router.post("/api/atelier/machines")
async def add_machine(request: Request):
    _ready(request)
    item = _run(workshop.add_machine, await request.json(), request.state.nom)
    activity_log.record("atelier", f"Machine ajoutée : {item['name']}", request.state.nom)
    return item


@router.get("/api/atelier/machines/{machine_id}")
def show_machine(machine_id: str, request: Request):
    _ready(request)
    return _run(workshop.machine, machine_id)


@router.patch("/api/atelier/machines/{machine_id}")
async def edit_machine(machine_id: str, request: Request):
    _ready(request)
    return _run(workshop.update_machine, machine_id, await request.json())


@router.delete("/api/atelier/machines/{machine_id}")
def remove_machine(machine_id: str, request: Request):
    _ready(request)
    name = _run(workshop.machine, machine_id)["name"]
    _run(workshop.delete_machine, machine_id)
    activity_log.record("atelier", f"Machine retirée : {name}", request.state.nom)
    return {"ok": True}


@router.post("/api/atelier/machines/{machine_id}/journal")
async def add_entry(machine_id: str, request: Request):
    _ready(request)
    return _run(workshop.add_entry, machine_id, await request.json(), request.state.nom)


@router.patch("/api/atelier/machines/{machine_id}/journal/{entry_id}")
async def edit_entry(machine_id: str, entry_id: str, request: Request):
    _ready(request)
    return _run(workshop.update_entry, machine_id, entry_id, await request.json())


@router.delete("/api/atelier/machines/{machine_id}/journal/{entry_id}")
def remove_entry(machine_id: str, entry_id: str, request: Request):
    _ready(request)
    _run(workshop.delete_entry, machine_id, entry_id)
    return {"ok": True}


@router.post("/api/atelier/machines/{machine_id}/rappels")
async def add_reminder(machine_id: str, request: Request):
    _ready(request)
    return _run(workshop.add_reminder, machine_id, await request.json())


@router.post("/api/atelier/rappels/{reminder_id}/fait")
def reminder_done(reminder_id: str, request: Request):
    _ready(request)
    return _run(workshop.reminder_done, reminder_id, request.state.nom)


@router.delete("/api/atelier/rappels/{reminder_id}")
def remove_reminder(reminder_id: str, request: Request):
    _ready(request)
    _run(workshop.delete_reminder, reminder_id)
    return {"ok": True}
