"""watchers"""
import time
from services import restart_policy
from services import video_player
from services import backups
from services import disk_browser
from services import auto_disks
from services import app_update
from services import notifications
from services import nextcloud_accounts
from version import VERSION
import config
from host import commands
from services import background_tasks
from services import files_source
from services import guest_network
from services import measurements
from services import power
from services import tools


WATCH_STATE = {"disques_pleins": set(), "pannes": {}, "redemarrage_signale": False, "maj_signalee": None,
        "derniere_purge": 0, "dernier_lien_nc": 0}


def watch_power():
    secteur, batterie, etat = power.read_power()
    avant = power.POWER_STATE["secteur"]
    power.POWER_STATE.update(batterie=batterie, etat=etat)
    if secteur is None:
        return
    power.POWER_STATE["secteur"] = secteur
    n = measurements.server_name()
    if avant is True and not secteur:
        notifications.send("courant", "Coupure de courant", f"{n} tourne sur batterie ({batterie} %). Il s'éteindra proprement sous {config.BATTERY_THRESHOLD} %.", "/#/systeme", famille="courant")
    elif avant is False and secteur:
        if power.POWER_STATE["arret_prevu"]:
            commands.run_on_host(["systemctl", "stop", "jabba-arret.timer"])
            power.POWER_STATE["arret_prevu"] = False
        notifications.send("courant", "Courant revenu", f"{n} est de nouveau sur secteur ({batterie} %).", "/#/systeme", famille="courant")
    if not secteur and batterie is not None and batterie <= config.BATTERY_THRESHOLD and not power.POWER_STATE["arret_prevu"]:
        power.POWER_STATE["arret_prevu"] = True
        notifications.send("courant", "Batterie faible : arrêt dans 1 minute",
                       f"{n} s'éteint proprement pour protéger les disques. Rallume-le avec son bouton quand le courant est revenu.", "/#/systeme", famille="courant")
        commands.run_on_host(["systemd-run", "--unit=jabba-arret", "--collect", "--on-active=60", "systemctl", "poweroff"])


def watch_space():
    chemins = {"/": "Disque système", "/srv/data": "Disque de données"}
    for nom, chemin in files_source.known_disks().items():
        if commands.run_on_host(["mountpoint", "-q", chemin], timeout=10)[0] == 0:
            chemins[chemin] = nom
    for chemin, u in measurements.disk_space(list(chemins)).items():
        p = 100 * u["utilise"] / max(1, u["total"])
        if p >= 90 and chemin not in WATCH_STATE["disques_pleins"]:
            WATCH_STATE["disques_pleins"].add(chemin)
            notifications.send("disque_plein", f"{chemins.get(chemin, chemin)} presque plein", f"Rempli à {round(p)} %. Fais de la place, ou vide la corbeille.", "/#/disques", famille="disque")
        elif p < 85:
            WATCH_STATE["disques_pleins"].discard(chemin)


def watch_tools():
    en_cours = {t.get("outil") for t in background_tasks.TASKS.values() if t["etat"] == "en cours"}
    for o in tools.list_tools():
        pid = o["id"]
        souci = o["etat"] in ("en panne", "partiel") and pid not in en_cours
        vu = WATCH_STATE["pannes"].get(pid, 0)
        if souci:
            WATCH_STATE["pannes"][pid] = vu + 1
            if vu + 1 == 2:  # deux passages de suite (2 minutes) : ce n'est pas un simple redémarrage
                notifications.send("outil_panne", f"{o['nom']} en panne", "Ouvre l'Établi et essaie « Redémarrer ».", "/#/outils", famille="outil")
        else:
            if vu >= 2 and o["etat"] == "actif":
                notifications.send("outil_panne", f"{o['nom']} est reparti", "", "/#/outils", famille="outil")
            WATCH_STATE["pannes"].pop(pid, None)


def watch_system_updates():
    if commands.run_on_host(["test", "-f", "/var/run/reboot-required"])[0] == 0:
        if not WATCH_STATE["redemarrage_signale"]:
            WATCH_STATE["redemarrage_signale"] = True
            auto = restart_policy.current_mode()["mode"] == "auto"
            notifications.send("mises_a_jour", "Redémarrage en attente", restart_policy.message(auto), "/#/systeme", famille="systeme")
    else:
        WATCH_STATE["redemarrage_signale"] = False
    if config.REPOSITORY:
        der = app_update.latest_release(config.REPOSITORY)
        if der and app_update.is_newer(VERSION, der["version"]) and WATCH_STATE["maj_signalee"] != der["version"]:
            WATCH_STATE["maj_signalee"] = der["version"]
            notifications.send("mises_a_jour", f"Appli Jabba {der['version']} disponible", "Installe-la depuis l'écran Système.", "/#/systeme", famille="appli")


def watch_forever():
    if config.DEMO:
        return
    tour = 0
    while True:
        etapes = [auto_disks.scan_disks]
        if tour % 4 == 0:   # toutes les minutes
            etapes += [backups.watch_backups, video_player.clean_up, lambda: app_update.CURRENT_UPDATE["unite"] and not app_update.CURRENT_UPDATE["signale"] and app_update.update_state(), watch_power, watch_space, watch_tools, guest_network.sync_guest_tools, guest_network.apply_firewall]
        if tour % 8 == 0:   # toutes les 2 minutes
            etapes += [nextcloud_accounts.watch_signups]
        if tour % 40 == 0:  # toutes les 10 minutes
            etapes += [auto_disks.link_disks_to_nextcloud]
        if tour % 240 == 0:  # toutes les heures
            etapes += [watch_system_updates]
        if time.time() - WATCH_STATE["derniere_purge"] > 86400:
            WATCH_STATE["derniere_purge"] = time.time()
            etapes += [disk_browser.purge_old_trash]
        for etape in etapes:
            try:
                etape()
            except Exception as e:  # noqa: BLE001 — la surveillance ne s'arrête jamais
                print("surveillance:", etape.__name__, e, flush=True)
        tour += 1
        time.sleep(15)
