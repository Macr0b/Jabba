# Jabba

Un serveur maison simple, sur un vieux PC sous Debian, et l'appli qui va avec.

Jabba, c'est une méthode en trois couches et une appli pour la piloter :

- **Le socle** : identique pour tout le monde. Debian, SSH par clé, mises à jour automatiques, Tailscale, Docker.
- **Le profil** : ce que le serveur partage, et avec qui.
  - **Perso** : le serveur d'une personne et de ses proches, joignable de partout par Tailscale.
  - **Groupe** : le serveur d'un lieu (asso, fablab, bureau), partagé sur place comme un NAS. Seuls les admins y accèdent à distance.
- **L'établi** : des outils au choix (Jellyfin, Nextcloud, Dockge…), tous installés, chacun activé ou désactivé d'un interrupteur.

Ce dépôt contient **l'appli Jabba**.

## L'appli

Une appli web installable (écran d'accueil de l'iPhone, Dock du Mac) qui sert de tableau de bord :

- **Accueil** : état du serveur, accès rapide aux dossiers, favoris.
- **Établi** : activer, ouvrir, redémarrer et mettre à jour les outils.
- **Fichiers** : par Nextcloud s'il est activé, sinon directement sur les disques du serveur. Recherche, aperçus, visionneuse, glisser-déposer, corbeille de 30 jours.
- **Disques** : un disque USB branché est ajouté tout seul, en lecture seule. Réglage lecture seule ou écriture, éjection.
- **Invités** : sur le Wi-Fi, une page sans compte ni appli (code à 4 chiffres ou QR code) donne accès aux dossiers et aux outils choisis par l'admin.
- **Utilisateurs** : comptes admin et super admin, mot de passe oublié validé par le super admin, qui se connecte, appareils connectés.
- **Système** :
  - alertes sur le téléphone, réglables par type ;
  - surveillance du courant, avec arrêt propre sur batterie faible ;
  - journal d'activité ;
  - mise à jour de l'appli en un clic ;
  - historique des versions ;
  - terminal du serveur (super admin, mot de passe redemandé).

Tout est en français. Aucune dépendance externe dans l'interface.

## Sécurité

- L'appli d'administration écoute **seulement sur le serveur lui-même** (`127.0.0.1:8090`). On n'y entre que par **Tailscale** (`tailscale serve`), avec un compte Tailscale autorisé (`.env` ou relié à un compte admin).
- Sans connexion admin, l'appli ne montre que la page invités. La connexion admin (identifiant + mot de passe) est gardée 30 jours dans un cookie protégé ; les actions les plus sensibles redemandent le mot de passe.
- Le super admin principal est le compte du serveur : son mot de passe est vérifié sur le serveur et n'est jamais enregistré. Les autres comptes n'ont qu'une empreinte (scrypt). Un code de secours de 6 mots permet de choisir un nouveau mot de passe serveur.
- La page invités du Wi-Fi est un serveur à part (port 80, réseau local seulement) qui ne connaît **que** les pages invités : aucune connexion admin n'y est possible. Les outils non ouverts au Wi-Fi sont bloqués sur le réseau local par un filtre réseau.
- **Ce dépôt ne contient aucun secret** : ni adresse, ni nom de serveur, ni mot de passe. Chaque serveur garde ses réglages dans son propre `.env` et ses données dans `donnees/`, jamais publiés.
- **Le dépôt ne peut rien envoyer à un serveur.** C'est le serveur qui regarde s'il existe une nouvelle version, et rien ne s'installe sans le clic d'un admin.
- Le terminal de l'appli ouvre un shell sur le serveur avec le compte du serveur (`sudo` y redemande le mot de passe). Il est réservé au super admin, demande un mot de passe saisi depuis moins de 10 minutes, passe par un ticket à usage unique valable 30 secondes, chaque ouverture envoie une alerte, et chaque commande tapée est notée au Journal au nom du compte Jabba qui l'a tapée (le compte Linux, lui, est partagé). Un super admin peut retirer le terminal à un autre.
- Pour piloter le serveur (Docker, disques, courant), le conteneur de l'appli a des droits étendus sur la machine (`privileged`, `pid: host`). C'est pour ça qu'il n'est joignable que par Tailscale.

## Installation

**Automatique (recommandé).** Sur un Debian 13 tout juste installé (avec le serveur SSH), depuis le compte créé à l'installation :

```bash
curl -fsSL https://github.com/Macr0b/Jabba/raw/main/installer.sh -o installer.sh && sudo bash installer.sh
```

Le script fait tout le socle de la méthode, puis installe l'appli : boîte noire, mises à jour automatiques, réseau qui se rétablit tout seul, réglages du portable (capot, écran, batterie), disque de données, Tailscale, Docker, SSH par clé seulement, Dockge et Jabba. Il ne demande que deux choses : le disque de données (rien n'est effacé sans taper `EFFACER`) et la connexion à Tailscale. On peut le relancer sans risque. Tout est noté dans `/var/log/jabba-installation.log`.

Avant de le lancer, ajoute ta clé SSH depuis ton ordinateur (`ssh-copy-id moi@<adresse du serveur>`) : le script ferme alors la connexion par mot de passe.

**À la main.** Prérequis : un serveur Debian avec le socle Jabba (Tailscale, Docker, `git`).

```bash
sudo mkdir -p /opt/stacks /srv/disques && sudo chown "$USER": /opt/stacks
git clone https://github.com/Macr0b/jabba.git /opt/stacks/jabba-app
cd /opt/stacks/jabba-app
git checkout "$(git describe --tags --abbrev=0)"
cp .env.exemple .env && chmod 600 .env
nano .env        # remplacer les <…>
docker compose up -d --build
sudo tailscale serve --bg --https=9000 http://127.0.0.1:8090
```

L'appli est ensuite à l'adresse `https://<nom-du-serveur>.<ton-tailnet>.ts.net:9000`.

## Mises à jour

Avec `JABBA_DEPOT=Macr0b/jabba` dans `.env`, l'écran **Système** annonce chaque nouvelle version publiée et l'installe en un clic. `.env` et `donnees/` ne sont pas touchés.

## Licence

[AGPL-3.0-or-later](LICENSE) : libre d'utilisation, de modification et de partage, à condition que toute version modifiée reste libre elle aussi, y compris quand elle est proposée en service.
