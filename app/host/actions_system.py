"""Les actions sur le système : version, mises à jour, redémarrage, alimentation, mise à jour de
l'appli, tâches de fond ; puis le pare-feu du réseau invités, les sauvegardes (restic) et le compte
du serveur.

Chaque action : un nom, des paramètres vérifiés, une commande construite ici (host/actions.py
les rassemble). Sommaire : ACTIONS, le nom de chaque action et sa fonction.
  interne : _app_folder, _os_release, _kernel, _reboot_required, _updates_log, _tailscale_devices, _power, _reboot, _restart_policy_read, _restart_policy_timer, _restart_policy_apply, _app_folder_action, _app_update_ready, _app_update_install, _task_state, _task_log, _network_route, _wifi_firewall, _data_folder, _snapshot_path, _restic_env, _restic_installed, _install_restic, _start_backup, _server_user, _first_account, _check_server_password, _change_server_password
"""

import re

from host.action_common import ActionError, background, folder_path, task_unit

# ---------------------------------------------------------------- système
APP_FOLDER = re.compile(r"/[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}")
POWER_SCRIPT = (
    'for f in /sys/class/power_supply/*; do [ -e "$f/type" ] || continue; '
    'echo "$(cat $f/type)|$(cat $f/online 2>/dev/null)|$(cat $f/capacity 2>/dev/null)|$(cat $f/status 2>/dev/null)'
    '|$(cat $f/power_now 2>/dev/null)|$(cat $f/current_now 2>/dev/null)|$(cat $f/voltage_now 2>/dev/null)"; done'
)
REBOOT = "[ -x /usr/local/sbin/redemarrer ] && exec /usr/local/sbin/redemarrer; exec systemctl reboot"


def _app_folder(value):
    folder = str(value or "")
    if not APP_FOLDER.fullmatch(folder) or ".." in folder:
        raise ActionError(f"Dossier de l'appli refusé : {folder!r}")
    return folder


def _os_release(args):
    return ["cat", "/etc/os-release"], 10


def _kernel(args):
    return ["uname", "-r"], 10


def _reboot_required(args):
    return ["test", "-f", "/var/run/reboot-required"], 10


def _updates_log(args):
    return ["tail", "-n", "200", "/var/log/unattended-upgrades/unattended-upgrades.log"], 10


def _tailscale_devices(args):
    return ["tailscale", "status", "--json"], 10


def _power(args):
    return ["sh", "-c", POWER_SCRIPT], 10


def _reboot(args):
    return ["systemd-run", "--on-active=3", "sh", "-c", REBOOT], 10


def _restart_policy_read(args):
    from services import restart_policy

    return ["cat", restart_policy.CHOICE_FILE], 10


def _restart_policy_timer(args):
    from services import restart_policy

    return ["systemctl", "is-enabled", "--quiet", f"{restart_policy.UNIT}.timer"], 10


def _restart_policy_apply(args):
    from services import restart_policy

    mode = args.get("mode")
    if mode not in restart_policy.MODES:
        raise ActionError(f"Mode refusé : {mode!r}")
    return ["sh", "-c", restart_policy.apply_script(mode)], 60


def _app_folder_action(args):
    template = '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
    return ["docker", "inspect", "-f", template, "jabba-app"], 10


def _app_update_ready(args):
    return ["sh", "-c", 'test -d "$1/.git" && command -v git', "pret", _app_folder(args.get("dossier"))], 10


def _app_update_install(args):
    """La clé qui vérifie la signature est lue ICI, dans les fichiers du service : l'appli ne la fournit pas."""
    from services import app_update

    try:
        script = app_update.install_script(
            _app_folder(args.get("dossier")), str(args.get("tag") or ""), app_update.publication_key()
        )
    except ValueError as error:
        raise ActionError(str(error))
    return background(task_unit(args.get("unite")), script)


def _task_state(args):
    return ["systemctl", "show", "-p", "ActiveState", "--value", task_unit(args.get("unite"))], 10


def _task_log(args):
    return ["journalctl", "-u", task_unit(args.get("unite")), "--no-pager", "-o", "cat", "-n", "300"], 10


# ---------------------------------------------------------------- réseau invités (pare-feu)
INTERFACE = re.compile(r"[A-Za-z0-9_.-]{1,15}")
# Une règle par port fermé au Wi-Fi ; la chaîne n'est refaite que si elle a changé.
FIREWALL = """I="$1"; shift
status=0
for T in iptables ip6tables; do
  "$T" -S DOCKER-USER >/dev/null 2>&1 || continue
  want=$(for p in "$@"; do echo "-A JABBA-WIFI -i $I -p tcp -m conntrack --ctorigdstport $p --ctdir ORIGINAL -j DROP"; done)
  have=$("$T" -S JABBA-WIFI 2>/dev/null | grep '^-A ')
  if [ "$want" = "$have" ] && "$T" -C DOCKER-USER -j JABBA-WIFI 2>/dev/null; then continue; fi
  "$T" -N JABBA-WIFI 2>/dev/null
  "$T" -F JABBA-WIFI || { status=1; continue; }
  for p in "$@"; do
    "$T" -A JABBA-WIFI -i "$I" -p tcp -m conntrack --ctorigdstport "$p" --ctdir ORIGINAL -j DROP || status=1
  done
  "$T" -C DOCKER-USER -j JABBA-WIFI 2>/dev/null || "$T" -I DOCKER-USER -j JABBA-WIFI || status=1
done
exit $status
"""


def _network_route(args):
    return ["ip", "-o", "-4", "route", "get", "1.1.1.1"], 10


def _wifi_firewall(args):
    interface = str(args.get("interface") or "")
    if not INTERFACE.fullmatch(interface):
        raise ActionError(f"Carte réseau refusée : {interface!r}")
    ports = args.get("ports")
    if not isinstance(ports, list) or len(ports) > 64:
        raise ActionError("Ports refusés")
    for port in ports:
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ActionError(f"Port refusé : {port!r}")
    return ["sh", "-c", FIREWALL, "pare-feu", interface, *(str(port) for port in sorted(ports))], 60


# ---------------------------------------------------------------- sauvegardes (restic)
SNAPSHOT_ID = re.compile(r"[0-9a-f]{8,64}")
RESTORE_STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}h\d{2}")
RESTORE_ROOT = "/srv/data/dossiers/Restaurations"
RESTORE = 'restic "$@" && chown -R 1000:1000 "$0"'
BACKUP_PROPERTIES = ("Nice=10", "IOSchedulingClass=idle")


def _data_folder(value):
    """Le dossier des données de l'appli (la clé des sauvegardes y est)."""
    folder = _app_folder(value)
    if not folder.endswith("/donnees"):
        raise ActionError(f"Dossier des données refusé : {folder!r}")
    return folder


def _snapshot_path(value):
    path = str(value or "")
    if not path.startswith("/") or len(path) > 4096 or "\x00" in path or "\n" in path:
        raise ActionError(f"Chemin dans la copie refusé : {path!r}"[:200])
    return path


def _restic_env(args):
    from services import backups

    disk = folder_path(args.get("disque"))
    return [
        "env",
        f"RESTIC_REPOSITORY={disk}/{backups.REPOSITORY_FOLDER}",
        f"RESTIC_PASSWORD_FILE={_data_folder(args.get('donnees'))}/sauvegardes.cle",
        "RESTIC_CACHE_DIR=/var/cache/restic-jabba",
    ]


def _restic_installed(args):
    return ["sh", "-c", "command -v restic"], 10


def _install_restic(args):
    return ["apt-get", "install", "-y", "restic"], 600


def _restic(args):
    """Lire les copies, en parcourir une, ou en remettre un morceau dans Restaurations/ (jamais par-dessus)."""
    gesture = args.get("geste")
    env = _restic_env(args)
    if gesture == "copies":
        return [*env, "restic", "--no-lock", "snapshots", "--tag", "jabba", "--json"], 120
    snapshot = str(args.get("copie") or "")
    if not SNAPSHOT_ID.fullmatch(snapshot):
        raise ActionError(f"Copie refusée : {snapshot!r}")
    path = _snapshot_path(args.get("chemin"))
    if gesture == "parcourir":
        return [*env, "restic", "--no-lock", "ls", "--json", snapshot, path], 120
    if gesture != "restaurer":
        raise ActionError(f"Geste refusé : {gesture!r}")
    stamp = str(args.get("horodatage") or "")
    if not RESTORE_STAMP.fullmatch(stamp) or path == "/":
        raise ActionError("Restauration refusée")
    parent, name = path.rsplit("/", 1)
    target = f"{RESTORE_ROOT}/{stamp} ({snapshot})"
    restic_args = ["--no-lock", "restore", f"{snapshot}:{parent or '/'}", "--target", target, "--include", "/" + name]
    return [*env, "sh", "-c", RESTORE, RESTORE_ROOT, *restic_args], 3600


def _start_backup(args):
    """Le script de la sauvegarde est écrit ICI, à partir des réglages : l'appli ne fournit pas de script."""
    from services import backups

    sources = args.get("sources")
    if not isinstance(sources, list) or not sources or not set(sources) <= set(backups.PATHS):
        raise ActionError(f"Sources refusées : {sources!r}")
    settings = {"chemin": folder_path(args.get("disque")), "sources": {key: True for key in sources}}
    script = backups.backup_script(settings, _data_folder(args.get("donnees")))
    return background(task_unit(args.get("unite")), script, "bash", BACKUP_PROPERTIES)


# ---------------------------------------------------------------- compte du serveur
SERVER_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
# Seulement un compte de personne (numéro 1000 à 59999) : jamais root ni un compte de service.
HUMAN_ACCOUNT = (
    'u=$(id -u "$1" 2>/dev/null) && [ "$u" -ge 1000 ] && [ "$u" -lt 60000 ] || { echo "Compte refusé" >&2; exit 5; }\n'
)


def _server_user(value):
    user = str(value or "")
    if not SERVER_USER.fullmatch(user):
        raise ActionError(f"Compte refusé : {user!r}")
    return user


def _first_account(args):
    return ["getent", "passwd", "1000"], 10


def _check_server_password(args):
    return [
        "sh",
        "-c",
        HUMAN_ACCOUNT + 'exec getent shadow "$1"',
        "verifier",
        _server_user(args.get("utilisateur")),
    ], 10


def _change_server_password(args):
    return ["sh", "-c", HUMAN_ACCOUNT + "exec chpasswd", "changer", _server_user(args.get("utilisateur"))], 30


ACTIONS = {
    "version_systeme": _os_release,
    "noyau": _kernel,
    "redemarrage_requis": _reboot_required,
    "journal_mises_a_jour": _updates_log,
    "appareils_tailscale": _tailscale_devices,
    "alimentation": _power,
    "redemarrer_serveur": _reboot,
    "lire_regle_redemarrage": _restart_policy_read,
    "minuteur_redemarrage": _restart_policy_timer,
    "regle_redemarrage": _restart_policy_apply,
    "dossier_appli": _app_folder_action,
    "appli_prete": _app_update_ready,
    "installer_appli": _app_update_install,
    "etat_tache": _task_state,
    "journal_tache": _task_log,
    "route_reseau": _network_route,
    "pare_feu_wifi": _wifi_firewall,
    "restic_installe": _restic_installed,
    "installer_restic": _install_restic,
    "restic": _restic,
    "lancer_sauvegarde": _start_backup,
    "compte_serveur": _first_account,
    "verifier_mot_de_passe_serveur": _check_server_password,
    "changer_mot_de_passe_serveur": _change_server_password,
}
