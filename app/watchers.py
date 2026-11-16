"""Surveillance en fond : tourne toutes les 15 secondes, tant que l'appli tourne.

Chaque tour lance certaines vérifications ; les plus lourdes moins souvent.

Sommaire :
  watch_power : secteur coupé, batterie faible
  watch_space : disques presque pleins
  watch_tools : outil en panne
  watch_system_updates : mises à jour du système, redémarrage en attente
  watch_forever : la boucle (voir le détail des fréquences dedans)
"""

import time

import config
from host import commands
from services import (
    app_update,
    auto_disks,
    background_tasks,
    backups,
    disk_browser,
    files_source,
    guest_network,
    measurements,
    nextcloud_accounts,
    notifications,
    power,
    restart_policy,
    tools,
    video_player,
)
from version import VERSION

WATCH_STATE = {
    "disques_pleins": set(),
    "pannes": {},
    "redemarrage_signale": False,
    "maj_signalee": None,
    "derniere_purge": 0,
    "dernier_lien_nc": 0,
}


def watch_power():
    on_mains, battery, state = power.read_power()
    previous_value = power.POWER_STATE["secteur"]
    power.POWER_STATE.update(batterie=battery, etat=state)
    if on_mains is None:
        return
    power.POWER_STATE["secteur"] = on_mains
    name = measurements.server_name()
    if previous_value is True and not on_mains:
        notifications.send(
            "courant",
            "Coupure de courant",
            f"{name} tourne sur batterie ({battery} %). Il s'éteindra proprement sous {config.BATTERY_THRESHOLD} %.",
            "/#/systeme",
            family="courant",
        )
    elif previous_value is False and on_mains:
        if power.POWER_STATE["arret_prevu"]:
            commands.run_on_host(["systemctl", "stop", "jabba-arret.timer"])
            power.POWER_STATE["arret_prevu"] = False
        notifications.send(
            "courant",
            "Courant revenu",
            f"{name} est de nouveau sur secteur ({battery} %).",
            "/#/systeme",
            family="courant",
        )
    if (
        not on_mains
        and battery is not None
        and battery <= config.BATTERY_THRESHOLD
        and not power.POWER_STATE["arret_prevu"]
    ):
        power.POWER_STATE["arret_prevu"] = True
        notifications.send(
            "courant",
            "Batterie faible : arrêt dans 1 minute",
            f"{name} s'éteint proprement pour protéger les disques. Rallume-le avec son bouton quand le courant est revenu.",
            "/#/systeme",
            family="courant",
        )
        commands.run_on_host(
            ["systemd-run", "--unit=jabba-arret", "--collect", "--on-active=60", "systemctl", "poweroff"]
        )


def watch_space():
    paths = {"/": "Disque système", "/srv/data": "Disque de données"}
    for name, path in files_source.known_disks().items():
        if commands.run_on_host(["mountpoint", "-q", path], timeout=10)[0] == 0:
            paths[path] = name
    for path, usage_entry in measurements.disk_space(list(paths)).items():
        percent = 100 * usage_entry["utilise"] / max(1, usage_entry["total"])
        if percent >= 90 and path not in WATCH_STATE["disques_pleins"]:
            WATCH_STATE["disques_pleins"].add(path)
            notifications.send(
                "disque_plein",
                f"{paths.get(path, path)} presque plein",
                f"Rempli à {round(percent)} %. Fais de la place, ou vide la corbeille.",
                "/#/disques",
                family="disque",
            )
        elif percent < 85:
            WATCH_STATE["disques_pleins"].discard(path)


def watch_tools():
    running = {task.get("outil") for task in background_tasks.TASKS.values() if task["etat"] == "en cours"}
    for tool in tools.list_tools():
        pid = tool["id"]
        problem = tool["etat"] in ("en panne", "partiel") and pid not in running
        failures = WATCH_STATE["pannes"].get(pid, 0)
        if problem:
            WATCH_STATE["pannes"][pid] = failures + 1
            if failures + 1 == 2:  # deux passages de suite (2 minutes) : ce n'est pas un simple redémarrage
                notifications.send(
                    "outil_panne",
                    f"{tool['nom']} en panne",
                    "Ouvre l'Établi et essaie « Redémarrer ».",
                    "/#/outils",
                    family="outil",
                )
        else:
            if failures >= 2 and tool["etat"] == "actif":
                notifications.send("outil_panne", f"{tool['nom']} est reparti", "", "/#/outils", family="outil")
            WATCH_STATE["pannes"].pop(pid, None)


def watch_system_updates():
    if commands.run_on_host(["test", "-f", "/var/run/reboot-required"])[0] == 0:
        if not WATCH_STATE["redemarrage_signale"]:
            WATCH_STATE["redemarrage_signale"] = True
            auto = restart_policy.current_mode()["mode"] == "auto"
            notifications.send(
                "mises_a_jour", "Redémarrage en attente", restart_policy.message(auto), "/#/systeme", family="systeme"
            )
    else:
        WATCH_STATE["redemarrage_signale"] = False
    if config.REPOSITORY:
        latest = app_update.latest_release(config.REPOSITORY)
        if (
            latest
            and app_update.is_newer(VERSION, latest["version"])
            and WATCH_STATE["maj_signalee"] != latest["version"]
        ):
            WATCH_STATE["maj_signalee"] = latest["version"]
            notifications.send(
                "mises_a_jour",
                f"Appli Jabba {latest['version']} disponible",
                "Installe-la depuis l'écran Système.",
                "/#/systeme",
                family="appli",
            )


def watch_forever():
    if config.DEMO:
        return
    tick = 0
    while True:
        steps = [auto_disks.scan_disks]
        if tick % 4 == 0:  # toutes les minutes
            steps += [
                backups.watch_backups,
                video_player.clean_up,
                lambda: (
                    app_update.CURRENT_UPDATE["unite"]
                    and not app_update.CURRENT_UPDATE["signale"]
                    and app_update.update_state()
                ),
                watch_power,
                watch_space,
                watch_tools,
                guest_network.sync_guest_tools,
                guest_network.apply_firewall,
            ]
        if tick % 8 == 0:  # toutes les 2 minutes
            steps += [nextcloud_accounts.watch_signups]
        if tick % 40 == 0:  # toutes les 10 minutes
            steps += [auto_disks.link_disks_to_nextcloud]
        if tick % 240 == 0:  # toutes les heures
            steps += [watch_system_updates]
        if time.time() - WATCH_STATE["derniere_purge"] > 86400:
            WATCH_STATE["derniere_purge"] = time.time()
            steps += [disk_browser.purge_old_trash]
        for step in steps:
            try:
                step()
            except Exception as e:  # noqa: BLE001 — la surveillance ne s'arrête jamais
                print("surveillance:", step.__name__, e, flush=True)
        tick += 1
        time.sleep(15)
