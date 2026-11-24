"""L'écran Comptes (Nextcloud) d'un coup : l'état, les comptes, les groupes, les rangs et les dossiers
communs, lus en parallèle (chaque appel à occ prend une à deux secondes).

Sommaire :
  overview(session)  : tout ce que l'écran affiche
  interne : _team_folders, _read_nextcloud, _forget_handled_signups, _ranks
"""

from concurrent.futures import ThreadPoolExecutor

import config
from host import commands
from security import guard
from services import measurements, nextcloud_accounts
from services.service_error import ServiceError


def _team_folders(occ):
    enabled = occ(["app:list", "--enabled", "--output=json"])[1]
    return nextcloud_accounts.team_folders(occ) if "groupfolders" in enabled else []


def _read_nextcloud():
    """(état, comptes, groupes, dossiers) ; comptes, groupes et dossiers sont None si Nextcloud est absent."""
    occ = commands.occ
    with ThreadPoolExecutor(4) as executor:
        state = executor.submit(nextcloud_accounts.overview, occ)
        accounts = executor.submit(nextcloud_accounts.accounts, occ)
        groups = executor.submit(nextcloud_accounts.groups, occ)
        folders = executor.submit(_team_folders, occ)
        if not state.result()["present"]:
            return state.result(), None, None, None
        try:
            return state.result(), accounts.result(), groups.result(), folders.result()
        except nextcloud_accounts.CloudError as error:
            raise ServiceError(500, str(error))


def _forget_handled_signups(accounts):
    """Les inscriptions validées ou refusées entre-temps sortent de la liste des déjà signalées (les
    nouvelles, c'est la surveillance qui les signale)."""
    pending = sorted(account["id"] for account in accounts if account["state"] == "attente")
    with nextcloud_accounts._lock:
        cloud_state = nextcloud_accounts.read()
        seen = cloud_state["pending_seen"]
        if seen != pending and set(pending) <= set(seen):
            cloud_state["pending_seen"] = pending
            nextcloud_accounts.write(cloud_state)
    return cloud_state


def _ranks(cloud_state, groups):
    """Chaque rang : son nom, combien de personnes l'ont, combien voient ses dossiers."""
    members = {group["id"]: group["members"] for group in groups}
    rank_of = cloud_state.get("rank_of", {})
    return [
        {
            "id": rank["id"],
            "name": rank["name"],
            "members": sum(1 for value in rank_of.values() if value == rank["id"]),
            "member_count": len(members.get(rank["id"], [])),
        }
        for rank in nextcloud_accounts.ranks_of(cloud_state)
    ]


def overview(session):
    state, accounts, groups, folders = _read_nextcloud()
    if accounts is None:
        return {"state": state, "rank": session["rank"]}
    nextcloud_accounts.refresh_usage_in_background()
    cloud_state = _forget_handled_signups(accounts)
    ranks_on = bool(cloud_state.get("ranks_enabled"))
    for account in accounts:
        account["cloud_rank"] = cloud_state.get("rank_of", {}).get(account["id"]) if ranks_on else None
    return {
        "state": state,
        "rank": session["rank"],
        "cap_gb": cloud_state["cap_gb"],
        "accounts": accounts,
        "groups": [group for group in groups if not group["id"].startswith(nextcloud_accounts.RANK_PREFIX)],
        "ranks_enabled": ranks_on,
        "ranks": _ranks(cloud_state, groups),
        "creator": session["username"] == guard.hostname(),
        "charter": nextcloud_accounts.charter(commands.occ),
        "default_charter": nextcloud_accounts.DEFAULT_CHARTER,
        "charter_max": nextcloud_accounts.CHARTER_MAX,
        "folders": folders,
        "modes": {mode: {"name": value[1], "detail": value[2]} for mode, value in nextcloud_accounts.MODES.items()},
        "all": nextcloud_accounts.MEMBERS_GROUP,
        "promised": sum(account["quota"] or 0 for account in accounts if account["state"] == "actif"),
        "space": None if config.DEMO else measurements.disk_space(["/srv/data"]).get("/srv/data"),
    }
