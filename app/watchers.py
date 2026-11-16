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


SURV = {"disques_pleins": set(), "pannes": {}, "redemarrage_signale": False, "maj_signalee": None,
        "derniere_purge": 0, "dernier_lien_nc": 0}


def surveiller_courant():
    secteur, batterie, etat = power.lire_courant()
    avant = power.COURANT["secteur"]
    power.COURANT.update(batterie=batterie, etat=etat)
    if secteur is None:
        return
    power.COURANT["secteur"] = secteur
    n = measurements.nom_serveur()
    if avant is True and not secteur:
        notifications.envoyer("courant", "Coupure de courant", f"{n} tourne sur batterie ({batterie} %). Il s'éteindra proprement sous {config.SEUIL_BATTERIE} %.", "/#/systeme", famille="courant")
    elif avant is False and secteur:
        if power.COURANT["arret_prevu"]:
            commands.hote(["systemctl", "stop", "jabba-arret.timer"])
            power.COURANT["arret_prevu"] = False
        notifications.envoyer("courant", "Courant revenu", f"{n} est de nouveau sur secteur ({batterie} %).", "/#/systeme", famille="courant")
    if not secteur and batterie is not None and batterie <= config.SEUIL_BATTERIE and not power.COURANT["arret_prevu"]:
        power.COURANT["arret_prevu"] = True
        notifications.envoyer("courant", "Batterie faible : arrêt dans 1 minute",
                       f"{n} s'éteint proprement pour protéger les disques. Rallume-le avec son bouton quand le courant est revenu.", "/#/systeme", famille="courant")
        commands.hote(["systemd-run", "--unit=jabba-arret", "--collect", "--on-active=60", "systemctl", "poweroff"])


def surveiller_espace():
    chemins = {"/": "Disque système", "/srv/data": "Disque de données"}
    for nom, chemin in files_source.disques_connus().items():
        if commands.hote(["mountpoint", "-q", chemin], timeout=10)[0] == 0:
            chemins[chemin] = nom
    for chemin, u in measurements.espace(list(chemins)).items():
        p = 100 * u["utilise"] / max(1, u["total"])
        if p >= 90 and chemin not in SURV["disques_pleins"]:
            SURV["disques_pleins"].add(chemin)
            notifications.envoyer("disque_plein", f"{chemins.get(chemin, chemin)} presque plein", f"Rempli à {round(p)} %. Fais de la place, ou vide la corbeille.", "/#/disques", famille="disque")
        elif p < 85:
            SURV["disques_pleins"].discard(chemin)


def surveiller_outils():
    en_cours = {t.get("outil") for t in background_tasks.TACHES.values() if t["etat"] == "en cours"}
    for o in tools.lister_outils():
        pid = o["id"]
        souci = o["etat"] in ("en panne", "partiel") and pid not in en_cours
        vu = SURV["pannes"].get(pid, 0)
        if souci:
            SURV["pannes"][pid] = vu + 1
            if vu + 1 == 2:  # deux passages de suite (2 minutes) : ce n'est pas un simple redémarrage
                notifications.envoyer("outil_panne", f"{o['nom']} en panne", "Ouvre l'Établi et essaie « Redémarrer ».", "/#/outils", famille="outil")
        else:
            if vu >= 2 and o["etat"] == "actif":
                notifications.envoyer("outil_panne", f"{o['nom']} est reparti", "", "/#/outils", famille="outil")
            SURV["pannes"].pop(pid, None)


def surveiller_maj():
    if commands.hote(["test", "-f", "/var/run/reboot-required"])[0] == 0:
        if not SURV["redemarrage_signale"]:
            SURV["redemarrage_signale"] = True
            auto = restart_policy.mode_redemarrage()["mode"] == "auto"
            notifications.envoyer("mises_a_jour", "Redémarrage en attente", restart_policy.message(auto), "/#/systeme", famille="systeme")
    else:
        SURV["redemarrage_signale"] = False
    if config.DEPOT:
        der = app_update.derniere(config.DEPOT)
        if der and app_update.plus_recente(VERSION, der["version"]) and SURV["maj_signalee"] != der["version"]:
            SURV["maj_signalee"] = der["version"]
            notifications.envoyer("mises_a_jour", f"Appli Jabba {der['version']} disponible", "Installe-la depuis l'écran Système.", "/#/systeme", famille="appli")


def surveiller():
    if config.DEMO:
        return
    tour = 0
    while True:
        etapes = [auto_disks.scruter_disques]
        if tour % 4 == 0:   # toutes les minutes
            etapes += [backups.surveiller_sauvegardes, video_player.nettoyer, lambda: app_update.MAJ["unite"] and not app_update.MAJ["signale"] and app_update.etat_maj(), surveiller_courant, surveiller_espace, surveiller_outils, guest_network.synchroniser_outils_invites, guest_network.appliquer_pare_feu]
        if tour % 8 == 0:   # toutes les 2 minutes
            etapes += [nextcloud_accounts.surveiller_inscriptions]
        if tour % 40 == 0:  # toutes les 10 minutes
            etapes += [auto_disks.relier_disques_nextcloud]
        if tour % 240 == 0:  # toutes les heures
            etapes += [surveiller_maj]
        if time.time() - SURV["derniere_purge"] > 86400:
            SURV["derniere_purge"] = time.time()
            etapes += [disk_browser.purger]
        for etape in etapes:
            try:
                etape()
            except Exception as e:  # noqa: BLE001 — la surveillance ne s'arrête jamais
                print("surveillance:", etape.__name__, e, flush=True)
        tour += 1
        time.sleep(15)
