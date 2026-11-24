"""Surveillance en fond : tourne toutes les 15 secondes, tant que l'appli tourne.

Chaque tour lance certaines vérifications ; les plus lourdes moins souvent.

Sommaire :
  watch_power : secteur coupé, batterie (du portable ou de l'onduleur) faible
  watch_space : disques presque pleins
  heartbeat.watch : signal de vie au service extérieur qui prévient si le serveur se tait (toutes les 5 minutes)
  heat_watch.watch : processeur trop chaud pendant 10 minutes, puis refroidi (chaque minute)
  disk_health.watch_disk_health : santé SMART des disques (une fois par jour)
  disk_maintenance.watch_fragmentation : fragmentation des disques à plateaux (une fois par mois)
  antivirus.watch_deposits : nouveaux dépôts des invités analysés (chaque minute)
  antivirus_disks.watch : analyse des disques (en cours, de la semaine, nouveaux fichiers chaque heure)
  antivirus.install_by_default : antivirus installé de base, une fois, si la mémoire suffit
  workshop.watch_reminders : rappels d'entretien de la mémoire d'atelier arrivés à échéance
  dns_update.watch : l'entrée DNS du domaine suit l'adresse de la box (chaque heure, chez Cloudflare ou OVH)
  mass_deletion.watch_mass_deletion : beaucoup de fichiers supprimés d'un coup
  tool_updates.watch_tool_updates : nouvelle version d'un outil (une fois par jour)
  public_mode.watch_inactive_accounts : comptes inactifs, en mode public (une fois par mois)
  watch_tools : outil en panne
  watch_system_updates : mises à jour du système, redémarrage en attente
  domain_access.watch_protection : Nextcloud ouvert sur Internet freine bien les essais en rafale (chaque heure)
  nextcloud_settings.keep_deleted_files : corbeille de Nextcloud gardée 30 jours au moins (chaque heure, sauf choix de l'admin)
  tool_addresses.watch : adresses à part qui manquent, créées si la clé Tailscale est là (chaque heure)
  SCHEDULE : ce qui tourne, et à quelle fréquence
  steps_for : ce qui tourne à un tour donné
  watch_forever : la boucle, un tour toutes les 15 secondes
  interne : _follow_app_update, _watch_inactive_accounts
"""

import time

import config
from host import commands
from services import (
    antivirus,
    antivirus_disks,
    app_update,
    auto_disks,
    background_tasks,
    backups,
    disk_browser,
    disk_health,
    disk_maintenance,
    dns_update,
    domain_access,
    files_source,
    guest_network,
    heartbeat,
    heat_watch,
    mass_deletion,
    measurements,
    nextcloud_accounts,
    nextcloud_settings,
    notifications,
    power,
    public_mode,
    restart_policy,
    tool_addresses,
    tool_updates,
    tools,
    video_player,
    workshop,
)
from version import VERSION

WATCH_STATE = {
    "full_disks": set(),
    "failures": {},
    "restart_reported": False,
    "update_reported": None,
    "last_purge": 0,
    "last_nc_link": 0,
}


def watch_power():
    on_mains, battery, state, source = power.read_power()
    previous_value = power.POWER_STATE["mains"]
    power.POWER_STATE.update(battery=battery, state=state, source=source)
    if on_mains is None:
        return
    power.POWER_STATE["mains"] = on_mains
    name = measurements.server_name()
    on_ups = source == "onduleur"
    if previous_value is True and not on_mains:
        where = "sur l'onduleur" if on_ups else "sur batterie"
        notifications.send(
            "courant",
            "Coupure de courant",
            f"{name} tourne {where} ({battery} %). Il s'éteindra proprement avant qu'elle soit vide.",
            "/#/systeme",
            family="courant",
        )
    elif previous_value is False and on_mains:
        if power.POWER_STATE["shutdown_planned"]:
            commands.run_action("annuler_arret_batterie")
            power.POWER_STATE["shutdown_planned"] = False
        notifications.send(
            "courant",
            "Courant revenu",
            f"{name} est de nouveau sur secteur ({battery} %).",
            "/#/systeme",
            family="courant",
        )
    if (
        power.low_battery(on_mains, battery, state, source, config.BATTERY_THRESHOLD)
        and not power.POWER_STATE["shutdown_planned"]
    ):
        power.POWER_STATE["shutdown_planned"] = True
        after = (
            "Il se rallumera tout seul quand le courant reviendra."
            if on_ups
            else "Rallume-le avec son bouton quand le courant est revenu."
        )
        notifications.send(
            "courant",
            "Batterie faible : arrêt dans 1 minute",
            f"{name} s'éteint proprement pour protéger les disques. {after}",
            "/#/systeme",
            family="courant",
        )
        # Avec un onduleur, l'arrêt passe par NUT : l'onduleur coupe ensuite sa prise, puis la rallume au
        # retour du courant, ce qui rallume le serveur (BIOS : « After Power Loss → Power On »).
        commands.run_action("arret_onduleur" if on_ups else "arret_batterie")


def watch_space():
    paths = {"/": "Disque système", "/srv/data": "Disque de données"}
    for name, path in files_source.known_disks().items():
        if files_source.is_mounted(path):
            paths[path] = name
    for path, usage_entry in measurements.disk_space(list(paths)).items():
        percent = 100 * usage_entry["used"] / max(1, usage_entry["total"])
        if percent >= 90 and path not in WATCH_STATE["full_disks"]:
            WATCH_STATE["full_disks"].add(path)
            notifications.send(
                "disque_plein",
                f"{paths.get(path, path)} presque plein",
                f"Rempli à {round(percent)} %. Fais de la place, ou vide la corbeille.",
                "/#/disques",
                family="disque",
            )
        elif percent < 85:
            WATCH_STATE["full_disks"].discard(path)


def watch_tools():
    running = {task.get("outil") for task in background_tasks.TASKS.values() if task["state"] == "en cours"}
    for tool in tools.list_tools():
        pid = tool["id"]
        problem = tool["state"] in ("en panne", "partiel") and pid not in running
        failures = WATCH_STATE["failures"].get(pid, 0)
        if problem:
            WATCH_STATE["failures"][pid] = failures + 1
            if failures + 1 == 2:  # deux passages de suite (2 minutes) : ce n'est pas un simple redémarrage
                notifications.send(
                    "outil_panne",
                    f"{tool['name']} en panne",
                    "Ouvre l'Établi et essaie « Redémarrer ».",
                    "/#/outils",
                    family="outil",
                )
        else:
            if failures >= 2 and tool["state"] == "actif":
                notifications.send("outil_panne", f"{tool['name']} est reparti", "", "/#/outils", family="outil")
            WATCH_STATE["failures"].pop(pid, None)


def watch_system_updates():
    if commands.run_action("redemarrage_requis")[0] == 0:
        if not WATCH_STATE["restart_reported"]:
            WATCH_STATE["restart_reported"] = True
            auto = restart_policy.current_mode()["mode"] == "auto"
            notifications.send(
                "mises_a_jour", "Redémarrage en attente", restart_policy.message(auto), "/#/systeme", family="systeme"
            )
    else:
        WATCH_STATE["restart_reported"] = False
    if config.REPOSITORY:
        latest = app_update.latest_release(config.REPOSITORY)
        if (
            latest
            and app_update.is_newer(VERSION, latest["version"])
            and WATCH_STATE["update_reported"] != latest["version"]
        ):
            WATCH_STATE["update_reported"] = latest["version"]
            notifications.send(
                "mises_a_jour",
                f"Appli Jabba {latest['version']} disponible",
                "Installe-la depuis l'écran Système.",
                "/#/systeme",
                family="appli",
            )


def _follow_app_update():
    """La mise à jour de l'appli en cours : son résultat, signalé une fois."""
    current = app_update.CURRENT_UPDATE
    if current["unit"] and not current["reported"]:
        app_update.update_state()


def _watch_inactive_accounts():
    public_mode.watch_inactive_accounts(commands.occ, nextcloud_accounts.accounts)


# Ce qui tourne, et tous les combien de tours (un tour = 15 secondes) : 1 = à chaque tour,
# 4 = chaque minute, 8 = toutes les 2 minutes, 40 = toutes les 10 minutes, 240 = chaque heure.
SCHEDULE = {
    1: [auto_disks.scan_disks],
    4: [
        backups.watch_backups,
        video_player.clean_up,
        _follow_app_update,
        watch_power,
        watch_space,
        watch_tools,
        heat_watch.watch,
        heartbeat.watch,
        antivirus.watch_deposits,
        antivirus_disks.watch,
        guest_network.sync_guest_tools,
        guest_network.apply_firewall,
    ],
    8: [nextcloud_accounts.watch_signups],
    40: [
        auto_disks.link_disks_to_nextcloud,
        disk_health.watch_disk_health,
        antivirus.install_by_default,
        workshop.watch_reminders,
        dns_update.watch,
        disk_maintenance.watch_fragmentation,
        mass_deletion.watch_mass_deletion,
        tool_updates.watch_tool_updates,
        _watch_inactive_accounts,
    ],
    240: [
        watch_system_updates,
        tool_addresses.watch,
        domain_access.watch_protection,
        nextcloud_settings.keep_deleted_files,
    ],
}


def steps_for(tick, now):
    """Ce qui tourne à ce tour-ci (le premier tour fait tout), plus la purge des corbeilles une fois par jour."""
    steps = [step for every, todo in SCHEDULE.items() if tick % every == 0 for step in todo]
    if now - WATCH_STATE["last_purge"] > 86400:
        WATCH_STATE["last_purge"] = now
        steps.append(disk_browser.purge_old_trash)
    return steps


def watch_forever():
    if config.DEMO:
        return
    tick = 0
    while True:
        for step in steps_for(tick, time.time()):
            try:
                step()
            except Exception as error:  # noqa: BLE001 — la surveillance ne s'arrête jamais
                print("surveillance:", step.__name__, error, flush=True)
        tick += 1
        time.sleep(15)
