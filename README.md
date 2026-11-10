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
- **Fichiers** : par Nextcloud s'il est activé, sinon directement sur les disques du serveur. Recherche, aperçus, visionneuse, glisser-déposer, corbeille de 30 jours. Le lecteur vidéo lit tous les formats : ce que le navigateur ne sait pas lire est converti par le serveur pendant la lecture (ffmpeg, avec la puce graphique si possible). Bouton « Regarder dans Jellyfin ».
- **Disques** : un disque USB branché est ajouté tout seul, en lecture seule. Réglage lecture seule ou écriture, éjection.
- **Invités** : sur le Wi-Fi, une page sans compte ni appli (code à 4 chiffres ou QR code) donne accès aux dossiers et aux outils choisis par l'admin.
- **Utilisateurs** : comptes admin et super admin, mot de passe oublié validé par le super admin, qui se connecte, appareils connectés.
- **Système** :
  - alertes sur le téléphone, réglables par type ;
  - surveillance du courant, avec arrêt propre sur batterie faible ;
  - journal d'activité ;
  - mise à jour de l'appli en un clic ;
  - historique des versions ;
  - terminal du serveur (compte du serveur seulement, mot de passe redemandé) ;
  - sauvegardes.
- **Comptes Nextcloud** (façon Dropbox) : inscription libre validée par un super admin, 5 Go par défaut, espace changé par les admins sous un plafond fixé par le super admin, groupes, dossiers communs (lecture seule, dépôt ou accès complet) pour tous les membres, un groupe ou un rang (rangs en option, nommés librement : un dossier donné à un rang est vu par ce rang et ceux au-dessus). Tout passe par les applis officielles de Nextcloud (Registration, Team folders) et est noté au Journal.
- **Sauvegardes** : chaque nuit, une copie chiffrée ([restic](https://restic.net)) des fichiers, des réglages des outils (avec une copie propre de leurs bases de données) et de ceux du serveur part sur le disque choisi, dans un dossier caché. Seules les nouveautés prennent de la place ; on garde 7 jours, 4 semaines et 6 mois. Une sauvegarde manquée (disque absent, serveur éteint) part dès que possible. Pour récupérer un fichier : choisir une date, puis le fichier ; il revient à côté, dans « Mes dossiers › Restaurations », sans rien écraser. La phrase de passe est montrée une seule fois : sans elle, les sauvegardes sont illisibles.

Tout est en français. Aucune dépendance externe dans l'interface : xterm.js (terminal) et hls.js (lecteur vidéo) sont livrés avec l'appli.

## Sécurité

- L'appli d'administration écoute **seulement sur le serveur lui-même** (`127.0.0.1:8090`). On n'y entre que par **Tailscale** (`tailscale serve`), avec un compte Tailscale autorisé (`.env` ou relié à un compte admin).
- Sans connexion admin, l'appli ne montre que la page invités. La connexion admin (identifiant + mot de passe) est gardée 30 jours dans un cookie protégé ; les actions les plus sensibles redemandent le mot de passe.
- Le super admin principal est le compte du serveur : son mot de passe est vérifié sur le serveur et n'est jamais enregistré. Les autres comptes n'ont qu'une empreinte (scrypt). Un code de secours de 6 mots permet de choisir un nouveau mot de passe serveur.
- La page invités du Wi-Fi est un serveur à part (port 80, réseau local seulement) qui ne connaît **que** les pages invités : aucune connexion admin n'y est possible. Il tourne dans **son propre conteneur, sans aucun pouvoir** : compte du serveur (pas root), aucune capacité Linux, fichiers en lecture seule. Il ne voit que `/srv` (les dossiers partagés) et son petit dossier d'état `donnees/porte-wifi/`, jamais les comptes, clés ou sessions de l'appli. Ce qu'il note au Journal est toujours affiché comme venant d'un invité. Les outils non ouverts au Wi-Fi sont bloqués sur le réseau local par un filtre réseau ; Dockge ne peut jamais l'être. Les dossiers partagés aux invités doivent être lisibles par le compte du serveur.
- **ffmpeg et Pillow tournent sans aucun droit** (compte `nobody`, aucune capacité, `no_new_privs`) : ils ne reçoivent que le fichier à lire, déjà ouvert par l'appli, et ne peuvent rien ouvrir d'autre. Un fichier vidéo ou une image piégés (déposés par un invité, par exemple) ne peuvent pas atteindre le serveur.
- **Ce dépôt ne contient aucun secret** : ni adresse, ni nom de serveur, ni mot de passe. Chaque serveur garde ses réglages dans son propre `.env` et ses données dans `donnees/`, jamais publiés.
- **Le dépôt ne peut rien envoyer à un serveur.** C'est le serveur qui regarde s'il existe une nouvelle version, et rien ne s'installe sans le clic d'un admin.
- **Les mises à jour sont signées.** Chaque version a sa signature SSH (`signatures/`), faite avec une clé qui ne quitte pas l'ordinateur du mainteneur. Avant d'installer quoi que ce soit, le serveur la vérifie avec la clé de la version **qu'il a déjà** (`app/cle_publication.pub`) : un compte GitHub piraté ne suffit pas à faire installer du code. Sans signature valable, rien ne s'installe et une alerte part. (Limite : le script d'installation, lui, se télécharge sans vérification ; lis-le avant de le lancer.)
- Le terminal de l'appli ouvre un shell sur le serveur avec le compte du serveur : **il donne tous les pouvoirs sur la machine**. Il est donc réservé au compte du serveur (son propriétaire), demande un mot de passe saisi depuis moins de 10 minutes, passe par un ticket à usage unique valable 30 secondes, et chaque ouverture envoie une alerte. Les commandes tapées sont notées au Journal, à titre indicatif seulement (un shell lancé à la main n'est plus noté). Le compte du serveur ne fait pas partie du groupe `docker` : `sudo docker …` redemande son mot de passe.
- Dockge pilote Docker, donc tout le serveur : il n'écoute que sur le serveur lui-même et n'est joignable que par Tailscale, jamais depuis le Wi-Fi.
- Pour piloter le serveur (Docker, disques, courant), le conteneur de l'appli a des droits étendus sur la machine (`privileged`, `pid: host`). C'est pour ça qu'il n'est joignable que par Tailscale, et qu'il ne traite plus rien qui vienne d'inconnus (la page invités et le décodage des fichiers sont ailleurs, sans droits). Étape suivante prévue : un petit service privilégié n'acceptant qu'une liste fermée d'actions, et l'appli elle-même sans droits.
- Des tests automatiques (`tests/`, lancés par GitHub à chaque envoi et avant chaque release) vérifient ces protections : signature des mises à jour, mot de passe oublié, terminal, porte du Wi-Fi, bac à sable, Dockge.

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

Avec `JABBA_DEPOT=Macr0b/jabba` dans `.env`, l'écran **Système** annonce chaque nouvelle version publiée et l'installe en un clic, **après avoir vérifié sa signature**. `.env` et `donnees/` ne sont pas touchés.

**Publier une version (mainteneur).** Une fois pour toutes, crée ta clé de publication sur ton ordinateur et mets sa partie publique dans `app/cle_publication.pub` :

```bash
ssh-keygen -t ed25519 -f ~/.ssh/jabba_publication -C "publication Jabba"
```

Puis, pour chaque version : envoie le code sur `main` (les tests tournent), signe « jabba vX.Y.Z <commit> » sur ton ordinateur, et ajoute sur `main` le fichier `signatures/vX.Y.Z.sig` (cette ligne, puis la signature). GitHub vérifie la signature et les tests, puis crée l'étiquette et la release sur le commit signé.

```bash
printf 'jabba v0.11.0 <commit complet>' | ssh-keygen -Y sign -f ~/.ssh/jabba_publication -n jabba
```

Pour publier ton propre Jabba (une copie du dépôt), remplace `app/cle_publication.pub` par ta propre clé.

## Licence

[AGPL-3.0-or-later](LICENSE) : libre d'utilisation, de modification et de partage, à condition que toute version modifiée reste libre elle aussi, y compris quand elle est proposée en service.
