"""Surveillance en fond : tourne toutes les 15 secondes, tant que l'appli tourne.

Chaque tour lance certaines vérifications ; les plus lourdes moins souvent.

Sommaire :
  watch_power : secteur coupé, batterie (du portable ou de l'onduleur) faible
  watch_space : disques presque pleins
  heat_watch.watch : processeur trop chaud pendant 10 minutes, puis refroidi (chaque minute)
  disk_health.watch_disk_health : santé SMART des disques (une fois par jour)
  disk_maintenance.watch_fragmentation : fragmentation des disques à plateaux (une fois par mois)
  antivirus.watch_deposits : nouveaux dépôts des invités analysés (chaque minute)
  antivirus_disks.watch : analyse des disques (en cours, de la semaine, nouveaux fichiers chaque heure)
  antivirus.install_by_default : antivirus installé de base, une fois, si la mémoire suffit
  workshop.watch_reminders : rappels d'entretien de la mémoire d'atelier arrivés à échéance
  ovh_dns.watch : l'entrée DNS du domaine suit l'adresse de la box (chaque heure, si une clé OVH est là)
  mass_deletion.watch_mass_deletion : beaucoup de fichiers supprimés d'un coup
  tool_updates.watch_tool_updates : nouvelle version d'un outil (une fois par jour)
  public_mode.watch_inactive_accounts : comptes inactifs, en mode public (une fois par mois)
  watch_tools : outil en panne
  watch_system_updates : mises à jour du système, redémarrage en attente
  watch_forever : la boucle (voir le détail des fréquences dedans)
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
    files_source,
    guest_network,
    heat_watch,
    mass_deletion,
    measurements,
    nextcloud_accounts,
    notifications,
    ovh_dns,
    power,
    public_mode,
    restart_policy,
    tool_updates,
    tools,
    video_player,
    workshop,
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
    on_mains, battery, state, source = power.read_power()
    previous_value = power.POWER_STATE["secteur"]
    power.POWER_STATE.update(batterie=battery, etat=state, source=source)
    if on_mains is None:
        return
    power.POWER_STATE["secteur"] = on_mains
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
        if power.POWER_STATE["arret_prevu"]:
            commands.run_action("annuler_arret_batterie")
            power.POWER_STATE["arret_prevu"] = False
        notifications.send(
            "courant",
            "Courant revenu",
            f"{name} est de nouveau sur secteur ({battery} %).",
            "/#/systeme",
            family="courant",
        )
    if (
        power.low_battery(on_mains, battery, state, source, config.BATTERY_THRESHOLD)
        and not power.POWER_STATE["arret_prevu"]
    ):
        power.POWER_STATE["arret_prevu"] = True
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
    if commands.run_action("redemarrage_requis")[0] == 0:
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
                heat_watch.watch,
                antivirus.watch_deposits,
                antivirus_disks.watch,
                guest_network.sync_guest_tools,
                guest_network.apply_firewall,
            ]
        if tick % 8 == 0:  # toutes les 2 minutes
            steps += [nextcloud_accounts.watch_signups]
        if tick % 40 == 0:  # toutes les 10 minutes
            steps += [
                auto_disks.link_disks_to_nextcloud,
                disk_health.watch_disk_health,
                antivirus.install_by_default,
                workshop.watch_reminders,
                ovh_dns.watch,
                disk_maintenance.watch_fragmentation,
                mass_deletion.watch_mass_deletion,
                tool_updates.watch_tool_updates,
                lambda: public_mode.watch_inactive_accounts(commands.occ, nextcloud_accounts.accounts),
            ]
        if tick % 240 == 0:  # toutes les heures
            steps += [watch_system_updates]
        if time.time() - WATCH_STATE["derniere_purge"] > 86400:
            WATCH_STATE["derniere_purge"] = time.time()
            steps += [disk_browser.purge_old_trash]
        for step in steps:
            try:
                step()
            except Exception as error:  # noqa: BLE001 — la surveillance ne s'arrête jamais
                print("surveillance:", step.__name__, error, flush=True)
        tick += 1
        time.sleep(15)
