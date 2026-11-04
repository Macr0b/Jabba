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
- **Système** :
  - alertes sur le téléphone, réglables par type ;
  - surveillance du courant, avec arrêt propre sur batterie faible ;
  - journal d'activité ;
  - mise à jour de l'appli en un clic ;
  - historique des versions.

Tout est en français. Aucune dépendance externe dans l'interface.

## Sécurité

- L'appli écoute **seulement sur le serveur lui-même** (`127.0.0.1:8090`). On n'y entre que par **Tailscale** (`tailscale serve`), et seuls les comptes Tailscale listés dans `.env` sont acceptés.
- Les actions sensibles (activer un outil, régler un disque, redémarrer, effacer la corbeille…) demandent le **mot de passe du serveur**. Il est vérifié sur le serveur et n'est jamais enregistré. Un code de secours de 6 mots permet d'en choisir un nouveau.
- **Ce dépôt ne contient aucun secret** : ni adresse, ni nom de serveur, ni mot de passe. Chaque serveur garde ses réglages dans son propre `.env` et ses données dans `donnees/`, jamais publiés.
- **Le dépôt ne peut rien envoyer à un serveur.** C'est le serveur qui regarde s'il existe une nouvelle version, et rien ne s'installe sans le clic d'un admin.
- Pour piloter le serveur (Docker, disques, courant), le conteneur de l'appli a des droits étendus sur la machine (`privileged`, `pid: host`). C'est pour ça qu'il n'est joignable que par Tailscale.

## Installation

Prérequis : un serveur Debian avec le socle Jabba (Tailscale, Docker, `git`).

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
