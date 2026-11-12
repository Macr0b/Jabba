"""Alerte « serveur muet » : un battement de cœur vers un service extérieur.

Quand le serveur est figé, éteint ou coupé d'Internet, il ne peut plus prévenir
personne. Il envoie donc un petit signal toutes les 5 minutes à un service de
surveillance extérieur (Healthchecks.io, par exemple, gratuit) : c'est ce service
qui alerte (e-mail, notification) quand les signaux cessent.

Le signal part du serveur lui-même (minuteur systemd), pas de l'appli : il continue
même si l'appli est arrêtée, et s'arrête vraiment quand le serveur ne va pas bien.
L'adresse est rangée dans /etc/jabba/battement.url (lisible par root seulement).
"""
import re
import shlex

FICHIER = "/etc/jabba/battement.url"
SCRIPT = "/usr/local/sbin/jabba-battement"
UNITE = "jabba-battement"
RE_URL = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{2,5})?(/[A-Za-z0-9._~%/-]*)?$")


def valide(url):
    return bool(url) and len(url) <= 300 and bool(RE_URL.match(url))


def masquer(url):
    """L'adresse sans sa partie secrète (on ne garde que le site et le début du chemin)."""
    if not url:
        return None
    m = re.match(r"^(https://[^/]+)(/.*)?$", url)
    return (m.group(1) + "/…" + (m.group(2) or "")[-4:]) if m else "…"


def installer(url):
    """Script shell (root) qui pose l'adresse, le programme et le minuteur, puis envoie un premier signal."""
    if not valide(url):
        raise ValueError("Adresse invalide : elle doit commencer par https:// (sans espace ni guillemet).")
    q = shlex.quote
    return f"""set -e
umask 077
mkdir -p /etc/jabba
printf '%s\\n' {q(url)} > {FICHIER}
cat > {SCRIPT} <<'FIN'
#!/bin/sh
# Battement de cœur de Jabba : « je suis vivant », toutes les 5 minutes.
url=$(cat {FICHIER} 2>/dev/null) || exit 0
[ -n "$url" ] || exit 0
exec curl -fsS -m 10 --retry 2 -o /dev/null "$url"
FIN
chmod 755 {SCRIPT}
cat > /etc/systemd/system/{UNITE}.service <<'FIN'
[Unit]
Description=Jabba : battement de cœur (alerte « serveur muet »)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart={SCRIPT}
FIN
cat > /etc/systemd/system/{UNITE}.timer <<'FIN'
[Unit]
Description=Jabba : battement de cœur toutes les 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
FIN
systemctl daemon-reload
systemctl enable --now {UNITE}.timer
systemctl start {UNITE}.service
"""


def retirer():
    return f"""systemctl disable --now {UNITE}.timer 2>/dev/null || true
rm -f {FICHIER} /etc/systemd/system/{UNITE}.service /etc/systemd/system/{UNITE}.timer {SCRIPT}
systemctl daemon-reload
"""
