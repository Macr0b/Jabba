"use strict";
/* Appli Jabba — interface. Aucune dépendance externe. */

const $ = (s, r = document) => r.querySelector(s);
const contenu = $("#contenu");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ------------------------------------------------------------ outils
// La connexion admin est un cookie protégé, posé par le serveur et gardé 30 jours sur l'appareil.
async function api(chemin, options = {}, deuxiemeEssai = false) {
  const o = { credentials: "same-origin", ...options, headers: { "X-Jabba": "1", ...(options.headers || {}) } };
  if (o.json !== undefined) {
    o.body = JSON.stringify(o.json);
    o.headers["Content-Type"] = "application/json";
    delete o.json;
  }
  let r;
  try { r = await fetch(chemin, o); }
  catch (_) { throw Object.assign(new Error("Le serveur ne répond pas."), { injoignable: "reseau" }); }
  if (r.status >= 502 && r.status <= 504) throw Object.assign(new Error("L'appli Jabba ne répond pas sur le serveur."), { injoignable: "appli" });
  let corps = null;
  try { corps = await r.json(); } catch (_) { /* vide */ }
  if (r.status === 401 && corps && corps.detail && corps.detail.admin && !deuxiemeEssai) {
    const d = corps.detail;
    const ok = d.changer ? await choisirMonMotDePasse(true) : d.confirmer ? await confirmerMotDePasse() : await connexionAdmin();
    if (ok) return api(chemin, options, true);
    throw new Error("Action annulée : mot de passe non saisi.");
  }
  if (!r.ok) {
    const d = corps && (corps.detail || corps.erreur);
    throw new Error((d && (d.message || (typeof d === "string" ? d : null))) || `Erreur ${r.status}`);
  }
  return corps;
}

// Formulaire dans une fenêtre : renvoie les valeurs, null (annulé) ou le nom du bouton secondaire.
function formulaire({ titre, texte = "", champs, valider = "Valider", secondaire = null }) {
  return new Promise((resoudre) => {
    dform.innerHTML = `<h2>${esc(titre)}</h2>${texte ? `<p>${esc(texte)}</p>` : ""}
      ${champs.map((c) => c.cache
        ? `<input type="text" name="${c.nom}" value="${esc(c.valeur || "")}" autocomplete="${c.autocomplete || "off"}" hidden>`
        : c.options
          ? `<label class="champ"><span>${esc(c.label)}</span><select name="${c.nom}">${c.options.map((o) => `<option value="${esc(o[0])}"${o[0] === c.valeur ? " selected" : ""}>${esc(o[1])}</option>`).join("")}</select></label>`
          : `<label class="champ"><span>${esc(c.label)}</span><input type="${c.type || "text"}" name="${c.nom}" value="${esc(c.valeur || "")}" placeholder="${esc(c.indice || "")}" autocomplete="${c.autocomplete || "off"}" autocapitalize="off" spellcheck="false"></label>`).join("")}
      ${secondaire ? `<button class="lien" value="secondaire" type="submit">${esc(secondaire)}</button>` : ""}
      <div class="actions"><button class="btn" value="non" type="submit" formnovalidate>Annuler</button>
      <button class="btn btn-p" value="oui" type="submit">${esc(valider)}</button></div>`;
    dialogue.onclose = () => {
      dform.onkeydown = null;
      if (dialogue.returnValue === "secondaire") return resoudre("secondaire");
      if (dialogue.returnValue !== "oui") return resoudre(null);
      const v = {};
      champs.forEach((c) => (v[c.nom] = dform[c.nom].value));
      resoudre(v);
    };
    // Entrée dans un champ = le bouton principal (sinon le navigateur prend le premier bouton : « Mot de passe oublié ? »).
    dform.onkeydown = (e) => {
      if (e.key === "Enter" && !e.isComposing && e.target.tagName === "INPUT") { e.preventDefault(); dialogue.close("oui"); }
    };
    dialogue.returnValue = "";
    dialogue.showModal();
    const premier = [...dform.querySelectorAll("input:not([hidden])")].find((i) => !i.value) || dform.querySelector("input:not([hidden])");
    if (premier) premier.focus();
  });
}

let utilisateurAdmin = "serveur";   // nom du serveur = identifiant du super admin principal
let adminConnecte = null;           // {identifiant, rang, compte_serveur}
const lireIdentifiant = () => { try { return localStorage.getItem("jabba-identifiant") || utilisateurAdmin; } catch (_) { return utilisateurAdmin; } };
const garderIdentifiant = (i) => { try { localStorage.setItem("jabba-identifiant", i); } catch (_) { /* rien */ } };

function retenirJeton(r) {
  const avant = adminConnecte;
  adminConnecte = { identifiant: r.identifiant, rang: r.rang, compte_serveur: r.identifiant === utilisateurAdmin };
  // On passait du mode invité au mode admin : on recharge l'interface complète.
  if (!avant && moi && moi.mode === "invite" && !r.changer) setTimeout(() => location.reload(), 300);
}

// Redemander le mot de passe avant une action sensible (déjà connecté).
async function confirmerMotDePasse() {
  const v = await formulaire({
    titre: "Confirme ton mot de passe",
    texte: "Cette action est sensible : ton mot de passe est redemandé (valable ensuite 10 minutes).",
    champs: [
      { nom: "identifiant", cache: true, valeur: (adminConnecte && adminConnecte.identifiant) || lireIdentifiant(), autocomplete: "username" },
      { nom: "mdp", type: "password", label: "Mot de passe", autocomplete: "current-password" },
    ],
    valider: "Confirmer",
  });
  if (!v || v === "secondaire") return false;
  try { await api("/api/admin/confirmer", { method: "POST", json: { mot_de_passe: v.mdp } }, true); return true; }
  catch (e) { toast(e.message, true); return false; }
}

async function deconnexion() {
  try { await api("/api/admin/deconnexion", { method: "POST" }, true); } catch (_) { /* déjà déconnecté */ }
  location.hash = "#/accueil";
  location.reload();
}

async function connexionAdmin() {
  let identifiant = lireIdentifiant();
  for (let essai = 0; essai < 3; essai++) {
    const v = await formulaire({
      titre: "Connexion admin",
      texte: `Identifiant « ${utilisateurAdmin} » = compte du serveur, avec son mot de passe. La connexion reste mémorisée 30 jours sur cet appareil.`,
      champs: [
        { nom: "identifiant", label: "Identifiant", valeur: identifiant, autocomplete: "username" },
        { nom: "mdp", type: "password", label: "Mot de passe", autocomplete: "current-password" },
      ],
      valider: "Continuer", secondaire: "Mot de passe oublié ?",
    });
    if (v === null) return false;
    if (v === "secondaire") { await motDePasseOublie(identifiant); continue; }
    identifiant = v.identifiant.trim().toLowerCase();
    try {
      const r = await api("/api/admin/connexion", { method: "POST", json: { identifiant, mot_de_passe: v.mdp } }, true);
      garderIdentifiant(identifiant);
      retenirJeton(r);
      if (r.changer) return choisirMonMotDePasse(true);
      return true;
    } catch (e) { toast(e.message, true); }
  }
  return false;
}

// Un admin (compte de l'appli) choisit son mot de passe : obligatoire après un mot de passe provisoire.
async function choisirMonMotDePasse(force = false) {
  const v = await formulaire({
    titre: force ? "Choisis ton mot de passe" : "Changer mon mot de passe",
    texte: force ? "Ton mot de passe actuel est provisoire. Choisis le tien (10 caractères minimum) : l'app Mots de passe proposera de l'enregistrer." : "",
    champs: [
      { nom: "identifiant", cache: true, valeur: (adminConnecte && adminConnecte.identifiant) || lireIdentifiant(), autocomplete: "username" },
      ...(force ? [] : [{ nom: "ancien", type: "password", label: "Mot de passe actuel", autocomplete: "current-password" }]),
      { nom: "nouveau", type: "password", label: "Nouveau mot de passe", autocomplete: "new-password" },
      { nom: "confirmation", type: "password", label: "Nouveau mot de passe, encore une fois", autocomplete: "new-password" },
    ],
    valider: "Enregistrer",
  });
  if (!v || v === "secondaire") return false;
  if (v.nouveau !== v.confirmation) { toast("Les deux mots de passe ne sont pas identiques.", true); return choisirMonMotDePasse(force); }
  try {
    const r = await api("/api/comptes-moi/mot-de-passe", { method: "POST", json: { ancien: v.ancien || "", nouveau: v.nouveau } }, true);
    retenirJeton(r);
    toast("Mot de passe enregistré");
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

async function motDePasseOublie(identifiant) {
  const v = await formulaire({
    titre: "Mot de passe oublié",
    texte: `Pour le compte du serveur (« ${utilisateurAdmin} »), tu utiliseras ton code de secours. Pour un autre compte, la demande part chez le super admin.`,
    champs: [{ nom: "identifiant", label: "Ton identifiant", valeur: identifiant, autocomplete: "username" }],
    valider: "Continuer",
  });
  if (!v || v === "secondaire") return;
  try {
    const r = await api("/api/comptes-oubli", { method: "POST", json: { identifiant: v.identifiant } }, true);
    if (r.serveur) return recupererMotDePasse();
    await demander({ titre: "Demande envoyée", texte: "Si cet identifiant existe, le super admin est prévenu. Dès qu'il accepte, tu reçois une alerte et ton mot de passe provisoire s'affiche ici, sur cet appareil.", valider: "OK" });
  } catch (e) { toast(e.message, true); }
}

// Mot de passe provisoire remis à la personne concernée, une seule fois.
async function afficherRemise() {
  let r;
  try { r = await api("/api/comptes-remise", { method: "POST" }); } catch (_) { return; }
  await new Promise((resoudre) => {
    dform.innerHTML = `<h2>Ton mot de passe provisoire</h2>
      <p>Identifiant : <strong>${esc(r.identifiant)}</strong>. Ce mot de passe ne sera <strong>plus affiché</strong> : il sert une fois, dans les 24 h, puis tu choisis le tien.</p>
      <p class="code-secours">${esc(r.mot_de_passe)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-code">Copier</button><button class="btn btn-p" value="ok" type="submit">Me connecter</button></div>`;
    dialogue.onclose = () => resoudre();
    dialogue.returnValue = "";
    dialogue.showModal();
    $("#copier-code").onclick = async () => { try { await navigator.clipboard.writeText(r.mot_de_passe); toast("Copié"); } catch (_) { toast("Copie impossible : sélectionne-le à la main.", true); } };
  });
  garderIdentifiant(r.identifiant);
  await connexionAdmin();
}

async function montrerCode(code) {
  await new Promise((resoudre) => {
    dform.innerHTML = `<h2>Ton code de secours</h2>
      <p>Il sert si tu oublies le mot de passe du serveur. Il ne sera <strong>plus jamais affiché</strong> : enregistre-le maintenant dans l'app Mots de passe (dans les notes de l'entrée « ${esc(utilisateurAdmin)} ») et note-le sur papier.</p>
      <p class="code-secours">${esc(code)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-code">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogue.onclose = () => resoudre();
    dialogue.showModal();
    $("#copier-code").onclick = async () => { try { await navigator.clipboard.writeText(code); toast("Code copié"); } catch (_) { toast("Copie impossible : sélectionne le code à la main.", true); } };
  });
}

async function recupererMotDePasse() {
  const v = await formulaire({
    titre: "Mot de passe oublié",
    texte: "Tape ton code de secours (les 6 mots), puis choisis un nouveau mot de passe pour le serveur.",
    champs: [
      { nom: "code", label: "Code de secours" },
      { nom: "identifiant", cache: true, valeur: utilisateurAdmin, autocomplete: "username" },
      { nom: "nouveau", type: "password", label: "Nouveau mot de passe (10 caractères minimum)", autocomplete: "new-password" },
      { nom: "confirmation", type: "password", label: "Nouveau mot de passe, encore une fois", autocomplete: "new-password" },
    ],
    valider: "Changer le mot de passe",
  });
  if (!v || v === "secondaire") return;
  if (v.nouveau !== v.confirmation) return toast("Les deux mots de passe ne sont pas identiques.", true);
  try {
    const r = await api("/api/admin/secours", { method: "POST", json: { code: v.code, nouveau: v.nouveau } }, true);
    toast("Mot de passe changé");
    await montrerCode(r.code);
  } catch (e) { toast(e.message, true); }
}

function octets(n) {
  if (n == null) return "—";
  const u = ["o", "Ko", "Mo", "Go", "To"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1024; i++; }
  return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)).toString().replace(".", ",") + " " + u[i];
}
function pourcent(u) { return u && u.total ? Math.round((100 * u.utilise) / u.total) : 0; }
function duree(s) {
  const j = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
  if (j) return `${j} j ${h} h`;
  return `${h} h ${Math.floor((s % 3600) / 60)} min`;
}
function quand(iso) {
  if (!iso) return "—";
  const d = new Date(iso), maintenant = new Date();
  const jours = Math.floor((new Date(maintenant.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (jours === 0) return "Aujourd'hui " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  if (jours === 1) return "Hier";
  if (jours < 7) return d.toLocaleDateString("fr-FR", { weekday: "long" }).replace(/^./, (c) => c.toUpperCase());
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: d.getFullYear() === maintenant.getFullYear() ? undefined : "numeric" });
}

function toast(texte, erreur = false) {
  const t = document.createElement("div");
  t.className = "toast" + (erreur ? " err" : "");
  t.textContent = texte;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), erreur ? 7000 : 3500);
}

const dialogue = $("#dialogue"), dform = $("#dialogue-form");
function demander({ titre, texte = "", champ = null, valider = "OK", danger = false }) {
  return new Promise((resoudre) => {
    dform.innerHTML = `<h2>${esc(titre)}</h2>${texte ? `<p>${esc(texte)}</p>` : ""}
      ${champ !== null ? `<input type="text" name="champ" value="${esc(champ)}" autocomplete="off" autocapitalize="off">` : ""}
      <div class="actions"><button class="btn" value="non" type="submit">Annuler</button>
      <button class="btn ${danger ? "btn-danger" : "btn-p"}" value="oui" type="submit">${esc(valider)}</button></div>`;
    dialogue.onclose = () => {
      const ok = dialogue.returnValue === "oui";
      const v = champ !== null ? dform.champ.value.trim() : true;
      resoudre(ok ? v : null);
    };
    dialogue.showModal();
    if (champ !== null) { const i = dform.champ; i.focus(); const p = i.value.lastIndexOf("."); i.setSelectionRange(0, p > 0 ? p : i.value.length); }
  });
}
function menu(titre, choix) {
  return new Promise((resoudre) => {
    dform.innerHTML = `<h2>${esc(titre)}</h2><div class="menu-actions">${choix
      .map((c, i) => `<button class="btn ${c.danger ? "btn-danger" : ""}" value="${i}" type="submit">${esc(c.texte)}</button>`)
      .join("")}</div><div class="actions"><button class="btn" value="" type="submit">Fermer</button></div>`;
    dialogue.onclose = () => resoudre(dialogue.returnValue === "" ? null : choix[+dialogue.returnValue]);
    dialogue.returnValue = "";
    dialogue.showModal();
  });
}

async function suivreTache(t) {
  toast(`${t.titre}…`);
  for (;;) {
    await new Promise((r) => setTimeout(r, 2000));
    const e = await api(`/api/taches/${t.id}`);
    if (e.etat !== "en cours") {
      if (e.etat === "terminé") toast(`${e.titre} : terminé`);
      else toast(`${e.titre} : erreur. ${e.journal.slice(-200)}`, true);
      return e;
    }
  }
}

const chargement = (n = 3) => `<div class="carte pad grille">${'<div class="squelette"></div>'.repeat(n)}</div>`;
const erreur = (e) => `<div class="erreur">${esc(e.message || e)}</div>`;

// ------------------------------------------------------------ pages
const pages = {};

pages.accueil = async () => {
  contenu.innerHTML = `<div class="entete"><div><h1>Bonjour</h1><p class="sous-titre">Ton serveur</p></div></div>${chargement()}`;
  const e = await api("/api/etat");
  const ok = !e.soucis.length;
  contenu.innerHTML = `
    <div class="entete">
      <div><h1>${esc(moi && moi.serveur ? moi.serveur[0].toUpperCase() + moi.serveur.slice(1) : "Le serveur")}</h1><p class="sous-titre">Allumé depuis ${duree(e.duree)} · ${e.outils_actifs} outil${e.outils_actifs > 1 ? "s" : ""} sur ${e.outils_total} actif${e.outils_actifs > 1 ? "s" : ""}</p></div>
      <span class="pastille ${ok ? "p-ok" : "p-alerte"}">${ok ? "Tout va bien" : "À vérifier"}</span>
    </div>
    ${e.soucis.length ? `<a class="bandeau" href="#/outils"><span><strong>Arrêté ou en panne :</strong> ${esc(e.soucis.join(", "))}</span><strong>Voir</strong></a>` : ""}
    ${e.redemarrage_requis ? `<a class="bandeau" href="#/systeme"><span>Une mise à jour attend un <strong>redémarrage</strong> (il se fera seul à 4 h).</span><strong>Voir</strong></a>` : ""}
    <div class="grille g4">
      <div class="carte pad mesure"><div class="etiq">Processeur</div><div class="val">${e.cpu} %</div><div class="jauge"><span style="width:${e.cpu}%"></span></div></div>
      <div class="carte pad mesure"><div class="etiq">Mémoire</div><div class="val">${octets(e.memoire.utilise)}</div><div class="detail">sur ${octets(e.memoire.total)}</div></div>
      <div class="carte pad mesure"><div class="etiq">Température</div><div class="val">${e.temperature ?? "—"} °C</div><div class="detail">${e.temperature == null ? "" : e.temperature < 70 ? "Normale" : e.temperature < 85 ? "Chaude" : "Trop chaude"}</div></div>
      <div class="carte pad mesure"><div class="etiq">Disque de données</div><div class="val">${pourcent(e.donnees)} %</div><div class="detail">${octets(e.donnees?.utilise)} sur ${octets(e.donnees?.total)}</div><div class="jauge"><span style="width:${pourcent(e.donnees)}%"></span></div></div>
    </div>
    <section aria-label="Accès rapide">
      <h2 class="titre-section">Accès rapide</h2>
      <div id="suggestions"></div>
      <div class="tuiles" id="tuiles"><div class="squelette" style="height:72px"></div></div>
    </section>
    <section class="carte liste-raccourcis" aria-label="Outils">
      <a href="#/ouvrir?outil=jellyfin"><span>Regarder un film</span><span>Jellyfin</span></a>
      <a href="#/outils"><span>Activer ou gérer les outils</span><span>Établi</span></a>
      <a href="#/disques"><span>Disques et espace</span><span>Disques</span></a>
      <a href="#/systeme"><span>Système et appareils</span><span>Système</span></a>
    </section>`;
  chargerAccesRapide().catch((err) => { const t = $("#tuiles"); if (t) t.innerHTML = `<p class="note">${esc(err.message)}</p>`; });
};

const lienDossier = (c) => "#/fichiers?chemin=" + encodeURIComponent(c);
const ICONES_TUILE = { racine: "dossier", espace: "dossier ext", disque: "disque", favori: "favori" };

async function chargerAccesRapide() {
  const d = await api("/api/accueil/dossiers");
  const tuiles = $("#tuiles"), sugg = $("#suggestions");
  if (!tuiles) return;
  const tuile = (t, type) => `<a class="carte tuile" href="${lienDossier(t.chemin)}"><span class="ic-tuile ${ICONES_TUILE[type]}" aria-hidden="true"></span><span class="nom-tuile">${esc(t.nom)}</span><span class="note">${{ racine: "Nextcloud", espace: "Serveur", disque: "Disque branché", favori: "Favori" }[type]}</span></a>`;
  tuiles.innerHTML = d.essentiels.map((t) => tuile(t, t.type)).join("") + d.favoris.map((t) => tuile(t, "favori")).join("")
    + (d.favoris.length ? "" : `<div class="tuile vide-tuile note">Ajoute un dossier ici avec l'étoile ☆ de l'écran Fichiers.</div>`);
  tuiles.querySelectorAll("a.tuile").forEach((a) => activerDepot(a, decodeURIComponent(a.getAttribute("href").split("chemin=")[1] || ""), () => {}));
  sugg.innerHTML = d.suggestions.map((s) => `
    <div class="bandeau suggestion"><span>Tu ouvres souvent <strong>${esc(s.nom)}</strong>. L'ajouter à l'accès rapide ?</span>
      <span class="actions"><button class="btn btn-petit btn-p" data-fav="${esc(s.chemin)}">Ajouter</button><button class="btn btn-petit" data-ign="${esc(s.chemin)}">Non merci</button></span></div>`).join("");
  sugg.querySelectorAll("[data-fav],[data-ign]").forEach((b) => b.addEventListener("click", async () => {
    const chemin = b.dataset.fav || b.dataset.ign;
    try { await api("/api/favoris", { method: "POST", json: { chemin, action: b.dataset.fav ? "ajouter" : "ignorer" } }); if (b.dataset.fav) toast("Ajouté à l'accès rapide"); }
    catch (e) { toast(e.message, true); }
    chargerAccesRapide().catch(() => {});
  }));
}

async function envoyerVers(chemin, fichiers) {
  fichiers = fichiers.filter((f) => f.size || f.type);
  if (!fichiers.length) return false;
  const fd = new FormData();
  for (const f of fichiers) fd.append("fichiers", f, f.name);
  const nom = chemin.split("/").filter(Boolean).pop() || "Mes fichiers";
  toast(`Envoi de ${fichiers.length} fichier${fichiers.length > 1 ? "s" : ""} dans « ${nom} »…`);
  try {
    const r = await api("/api/fichiers/envoyer?chemin=" + encodeURIComponent(chemin), { method: "POST", body: fd });
    toast(`${r.envoyes.length} fichier${r.envoyes.length > 1 ? "s copiés" : " copié"} dans « ${nom} »`);
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

// Fait d'un élément (tuile, ligne de dossier) une cible de dépôt de fichiers.
function activerDepot(el, chemin, apres) {
  let compteur = 0;
  el.addEventListener("dragenter", (ev) => { if (!ev.dataTransfer.types.includes("Files")) return; ev.preventDefault(); compteur++; el.classList.add("cible"); });
  el.addEventListener("dragover", (ev) => { if (ev.dataTransfer.types.includes("Files")) { ev.preventDefault(); ev.stopPropagation(); } });
  el.addEventListener("dragleave", () => { compteur = Math.max(0, compteur - 1); if (!compteur) el.classList.remove("cible"); });
  el.addEventListener("drop", async (ev) => {
    ev.preventDefault(); ev.stopPropagation(); ev.jabbaPris = true;
    compteur = 0; el.classList.remove("cible");
    if (await envoyerVers(chemin, [...ev.dataTransfer.files])) apres();
  });
}

async function basculerFavori(chemin, favori) {
  await api("/api/favoris", { method: "POST", json: { chemin, action: favori ? "retirer" : "ajouter" } });
  toast(favori ? "Retiré de l'accès rapide" : "Ajouté à l'accès rapide");
}

// Icône d'un outil : le logo servi par l'outil lui-même, sinon son initiale.
function icone(o, taille = 40) {
  const initiale = esc((o.nom || "?")[0].toUpperCase());
  if (!o.icone) return `<span class="icone" style="width:${taille}px;height:${taille}px">${initiale}</span>`;
  return `<span class="icone" style="width:${taille}px;height:${taille}px"><img alt="" src="/api/icone/${encodeURIComponent(o.id)}" onerror="this.parentNode.textContent='${initiale}'"></span>`;
}

const A_VENIR = [
  { nom: "Torrent derrière VPN", role: "Téléchargements, avec un abonnement VPN" },
  { nom: "Sauvegarde croisée", role: "Une copie chiffrée sur un autre serveur Jabba" },
];

pages.outils = async () => {
  contenu.innerHTML = `<div class="entete"><div><h1>Établi</h1></div></div>${chargement()}`;
  const outils = await api("/api/outils");
  const pastille = { actif: "p-ok", partiel: "p-alerte", "en panne": "p-rouge", "arrêté": "p-gris" };
  const outilsEtabli = outils.filter((o) => !o.systeme), systeme = outils.filter((o) => o.systeme);
  const carte = (o) => {
    const actif = o.etat !== "arrêté";
    return `
      <article class="carte pad outil${actif ? "" : " eteint"}">
        <div class="haut">
          <div class="titre-outil">${icone(o)}<div><h2>${esc(o.nom)}</h2><div class="role">${esc(o.role)}</div></div></div>
          ${o.systeme || !o.modifiable
            ? `<span class="pastille ${pastille[o.etat] || "p-gris"}">${esc(o.etat[0].toUpperCase() + o.etat.slice(1))}</span>`
            : `<button class="interrupteur" role="switch" aria-checked="${actif}" aria-label="${actif ? "Désactiver" : "Activer"} ${esc(o.nom)}" data-bascule="${esc(o.id)}"><span></span></button>`}
        </div>
        ${o.etat === "en panne" || o.etat === "partiel" ? `<div class="note" style="color:var(--rouge)">${o.etat === "en panne" ? "En panne : essaie Redémarrer." : "Une partie de l'outil ne tourne pas : essaie Redémarrer."}</div>` : ""}
        ${actif && !o.systeme ? `<div class="actions">
          ${o.lien ? (o.dans_appli
            ? `<a class="btn btn-p" href="#/ouvrir?outil=${encodeURIComponent(o.id)}">Ouvrir</a>`
            : (o.id === "nextcloud"
              ? `<a class="btn btn-p" href="#/fichiers">Mes fichiers</a><a class="btn" href="${esc(o.lien)}" target="_blank" rel="noopener">Nextcloud complet</a>`
              : `<a class="btn btn-p" href="${esc(o.lien)}" target="_blank" rel="noopener">Ouvrir</a>`)) : ""}
          ${o.modifiable ? `<button class="btn" data-action="redemarrer" data-id="${esc(o.id)}">Redémarrer</button>
          <button class="btn" data-action="mettre-a-jour" data-id="${esc(o.id)}">Mettre à jour</button>` : ""}
        </div>` : ""}
        ${o.systeme && o.modifiable ? `<div class="actions"><button class="btn" data-action="mettre-a-jour" data-id="${esc(o.id)}">Mettre à jour</button></div>` : ""}
      </article>`;
  };
  contenu.innerHTML = `
    <div class="entete"><div><h1>Établi</h1><p class="sous-titre">Active les outils dont tu as besoin. Un outil désactivé ne consomme rien et garde ses données.</p></div></div>
    <div class="grille g2">${outilsEtabli.map(carte).join("")}</div>
    <h2 class="titre-section">Bientôt dans l'établi</h2>
    <div class="grille g2">${A_VENIR.map((a) => `<article class="carte pad outil eteint"><div class="haut"><div class="titre-outil">${icone({ nom: a.nom })}<div><h2>${esc(a.nom)}</h2><div class="role">${esc(a.role)}</div></div></div><span class="pastille p-gris">Bientôt</span></div></article>`).join("")}</div>
    <h2 class="titre-section">Système</h2>
    <div class="grille g2">${systeme.map(carte).join("")}</div>
    <p class="note">« Mettre à jour » télécharge la dernière version de l'outil et le relance (1 à 5 minutes). Tes réglages et tes fichiers sont conservés.</p>`;

  const recharger = () => { if (location.hash.startsWith("#/outils")) pages.outils().catch(montrerErreur); };
  contenu.querySelectorAll("[data-bascule]").forEach((b) => b.addEventListener("click", async () => {
    const o = outils.find((x) => x.id === b.dataset.bascule);
    const activer = b.getAttribute("aria-checked") !== "true";
    if (!activer && !(await demander({ titre: `Désactiver ${o.nom} ?`, texte: "Il ne sera plus accessible, sur aucun appareil, jusqu'à ce que tu le réactives. Ses réglages et ses données sont conservés.", valider: "Désactiver", danger: true }))) return;
    b.setAttribute("aria-checked", String(activer));
    contenu.querySelectorAll("[data-bascule],[data-action]").forEach((x) => (x.disabled = true));
    try { await suivreTache(await api(`/api/outils/${encodeURIComponent(o.id)}/${activer ? "demarrer" : "arreter"}`, { method: "POST" })); }
    catch (e) { toast(e.message, true); }
    recharger();
  }));
  contenu.querySelectorAll("[data-action]").forEach((b) => b.addEventListener("click", async () => {
    const o = outils.find((x) => x.id === b.dataset.id);
    const textes = {
      redemarrer: ["Redémarrer " + o.nom + " ?", "Il sera indisponible quelques secondes.", "Redémarrer"],
      "mettre-a-jour": ["Mettre à jour " + o.nom + " ?", "Il sera indisponible pendant la mise à jour (1 à 5 minutes).", "Mettre à jour"],
    }[b.dataset.action];
    if (!(await demander({ titre: textes[0], texte: textes[1], valider: textes[2] }))) return;
    contenu.querySelectorAll("[data-bascule],[data-action]").forEach((x) => (x.disabled = true));
    try { await suivreTache(await api(`/api/outils/${encodeURIComponent(o.id)}/${b.dataset.action}`, { method: "POST" })); }
    catch (e) { toast(e.message, true); }
    recharger();
  }));
};

// Ouvrir un outil dans l'appli, sans passer par le navigateur.
pages.ouvrir = async (params) => {
  const outils = await api("/api/outils");
  const o = outils.find((x) => x.id === params.get("outil"));
  if (!o || !o.lien) throw new Error("Outil introuvable ou désactivé.");
  document.body.classList.add("plein-ecran");
  contenu.innerHTML = `
    <div class="barre-outil">
      <a class="btn btn-petit" href="#/outils">← Établi</a>
      <span class="titre-outil">${icone(o, 24)}<strong>${esc(o.nom)}</strong></span>
      <a class="btn btn-petit" href="${esc(o.lien)}" target="_blank" rel="noopener">Navigateur ↗</a>
    </div>
    <iframe class="cadre-outil" src="${esc(o.lien)}" title="${esc(o.nom)}" allow="fullscreen; autoplay; clipboard-write" allowfullscreen></iframe>
    <p class="note aide-cadre">Page blanche ? Utilise « Navigateur ↗ » en haut à droite.</p>`;
};

let tourDisques = 0;
pages.disques = async () => {
  const tour = ++tourDisques;
  if (!contenu.querySelector(".disque")) contenu.innerHTML = `<div class="entete"><div><h1>Disques</h1></div></div>${chargement()}`;
  const disques = await api("/api/disques");
  // Un disque qu'on branche apparaît tout seul : on regarde toutes les 10 secondes.
  const empreinte = JSON.stringify(disques);
  setTimeout(async function suivre() {
    if (tour !== tourDisques || !location.hash.startsWith("#/disques")) return;
    if (dialogue.open) return setTimeout(suivre, 10000);
    try { if (JSON.stringify(await api("/api/disques")) !== empreinte) return pages.disques().catch(montrerErreur); } catch (_) { /* on réessaie */ }
    setTimeout(suivre, 10000);
  }, 10000);
  contenu.innerHTML = `
    <div class="entete"><div><h1>Disques</h1><p class="sous-titre">Ce qui est branché sur le serveur</p></div>
      <button class="btn" id="rafraichir">Actualiser</button></div>
    <div class="grille g2">${disques.map((d) => `
      <article class="carte pad disque">
        <div class="entete"><div><h2>${esc(d.modele || d.nom)}</h2><div class="note">${octets(d.taille)} · ${d.externe ? "Externe (USB)" : "Interne"} · ${esc(d.nom)}</div></div></div>
        <div class="parts">${d.partitions.length ? d.partitions.map((p) => `
          <div class="part">
            <div><strong>${esc(p.etiquette || p.nom)}</strong> <span class="note">· ${esc(p.type || "?")} · ${octets(p.taille)}</span><div class="note">${esc(p.role)}${p.montage && p.role !== p.montage ? " · " + esc(p.montage) : ""}</div></div>
            <div class="note">${p.usage ? octets(p.usage.utilise) + " utilisés" : ""}</div>
            ${p.usage ? `<div class="jauge"><span style="width:${pourcent(p.usage)}%"></span></div>` : ""}
            ${p.disque_jabba ? `<div class="reglages-disque">
              <label class="ligne-reglage"><span><strong>Écriture autorisée</strong><br><span class="note">${p.disque_jabba.ecriture ? "On peut ajouter et modifier des fichiers. Éjecte avant de débrancher." : "Lecture seule : tu peux débrancher à tout moment."}</span></span>
                <button class="interrupteur" role="switch" aria-checked="${p.disque_jabba.ecriture}" data-mode="${esc(p.disque_jabba.nom)}"><span></span></button></label>
              <div class="actions"><button class="btn" data-ejecter="${esc(p.disque_jabba.nom)}">Éjecter</button>
              ${p.disque_jabba.auto ? `<button class="btn btn-danger" data-oublier="${esc(p.disque_jabba.nom)}">Oublier ce disque</button>` : ""}</div>
            </div>` : ""}
            ${p.oublie ? `<div class="reglages-disque"><span class="note">Disque oublié : il n'est plus ajouté tout seul.</span><button class="btn" data-reprendre="${esc(p.oublie)}">Utiliser ce disque</button></div>` : ""}
            ${p.illisible && !p.montage ? `<div class="reglages-disque"><span class="note">Format ${esc(p.illisible)} : illisible par le serveur. Pour l'utiliser ici, reformate-le en exFAT depuis le Mac (Utilitaire de disque).</span></div>` : ""}
          </div>`).join("") : `<div class="note part">Aucune partition lisible</div>`}
        </div>
      </article>`).join("")}
    </div>
    <p class="note">Branche un disque USB : il est ajouté tout seul en quelques secondes, en lecture seule, et apparaît dans Fichiers, Nextcloud et Jellyfin (dossier /disques). Formats lisibles : exFAT, FAT, NTFS, ext4. Le réglage « Écriture autorisée » et « Oublier » demandent le mot de passe du serveur ; « Éjecter » est toujours libre.</p>`;
  $("#rafraichir").onclick = () => pages.disques().catch(montrerErreur);
  contenu.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", async () => {
    const nom = b.dataset.mode, ecriture = b.getAttribute("aria-checked") !== "true";
    if (ecriture && !(await demander({ titre: `Autoriser l'écriture sur ${nom} ?`, texte: "Tu pourras ajouter et modifier des fichiers depuis l'appli, Nextcloud et le Finder. Avant de débrancher le disque, il faudra cliquer sur « Éjecter ». Jellyfin, lui, reste en lecture seule.", valider: "Autoriser" }))) return;
    b.disabled = true;
    try { await api(`/api/disques/${encodeURIComponent(nom)}/mode`, { method: "POST", json: { ecriture } }); toast(ecriture ? `${nom} : écriture autorisée` : `${nom} : lecture seule`); }
    catch (e) { toast(e.message, true); }
    pages.disques().catch(montrerErreur);
  }));
  contenu.querySelectorAll("[data-oublier]").forEach((b) => b.addEventListener("click", async () => {
    const nom = b.dataset.oublier;
    if (!(await demander({ titre: `Oublier ${nom} ?`, texte: "Il disparaît de l'appli, de Nextcloud et de Jellyfin, et ne sera plus ajouté tout seul quand tu le branches. Son contenu n'est pas touché.", valider: "Oublier", danger: true }))) return;
    try { await api(`/api/disques/${encodeURIComponent(nom)}/oublier`, { method: "POST" }); toast(`${nom} oublié`); } catch (e) { toast(e.message, true); }
    pages.disques().catch(montrerErreur);
  }));
  contenu.querySelectorAll("[data-reprendre]").forEach((b) => b.addEventListener("click", async () => {
    try { await api("/api/disques/reprendre", { method: "POST", json: { uuid: b.dataset.reprendre } }); toast("Le disque sera ajouté dans quelques secondes"); } catch (e) { toast(e.message, true); }
    setTimeout(() => pages.disques().catch(montrerErreur), 4000);
  }));
  contenu.querySelectorAll("[data-ejecter]").forEach((b) => b.addEventListener("click", async () => {
    const nom = b.dataset.ejecter;
    b.disabled = true; b.textContent = "Éjection…";
    try { await api(`/api/disques/${encodeURIComponent(nom)}/ejecter`, { method: "POST" }); await demander({ titre: `Tu peux débrancher ${nom}`, texte: "Toutes les copies sont terminées. Rebranché, il reviendra tout seul.", valider: "OK" }); }
    catch (e) { toast(e.message, true); }
    pages.disques().catch(montrerErreur);
  }));
};

pages.systeme = async () => {
  contenu.innerHTML = `<div class="entete"><div><h1>Système</h1></div></div>${chargement()}`;
  const [s, a] = await Promise.all([api("/api/systeme"), api("/api/admin/etat")]);
  contenu.innerHTML = `
    <div class="entete"><div><h1>Système</h1><p class="sous-titre">${esc(s.nom)} · ${esc(s.os)}</p></div>
      <div class="actions"><a class="btn" href="#/sauvegardes">Sauvegardes</a>${adminConnecte && adminConnecte.rang === "super" ? '<a class="btn" href="#/terminal">Terminal</a>' : ""}<button class="btn btn-danger" id="reboot">Redémarrer le serveur</button></div></div>
    ${s.redemarrage_requis ? `<div class="bandeau"><span>Une mise à jour attend un redémarrage. Il se fera automatiquement à 4 h, ou maintenant avec le bouton.</span></div>` : ""}
    <div class="grille g2">
      <section class="carte pad"><dl class="cles">
        <dt>Allumé depuis</dt><dd>${duree(s.duree)}</dd>
        <dt>Mises à jour auto</dt><dd>${s.derniere_maj_auto ? "Dernier passage le " + esc(s.derniere_maj_auto.replace(" ", " à ")) : "Pas encore de passage"}</dd>
        <dt>Système</dt><dd>${esc(s.os)}</dd>
        <dt>Noyau</dt><dd>${esc(s.noyau)}</dd>
        <dt>Appli Jabba</dt><dd>version ${esc(s.version_appli)}</dd>
        <dt>Courant</dt><dd>${s.courant && s.courant.secteur != null
          ? (s.courant.secteur ? "Sur secteur" : `<strong style="color:var(--rouge)">Sur batterie</strong>`) + (s.courant.batterie != null ? ` · batterie ${s.courant.batterie} %` : "")
          : "Pas encore mesuré"}</dd>
        ${s.courant && s.courant.batterie != null ? `<dt>Consommation</dt><dd>${s.conso && s.conso.derniere
          ? `${String(s.conso.derniere.w).replace(".", ",")} W <span class="note">(${s.courant.secteur ? "dernière mesure sur batterie, " + esc(ilYa(s.conso.derniere.t)) : "en ce moment"})</span>${s.conso.repos ? `<br><span class="note">Au plus bas : ${String(s.conso.repos.w).replace(".", ",")} W, au repos</span>` : ""}`
          : `<span class="note">Se mesure sur batterie : débranche le chargeur 2 minutes pour la voir.</span>`}</dd>` : ""}
      </dl></section>
      <section class="carte pad" id="maj"><h2>Appli Jabba</h2><div class="squelette" style="margin-top:10px"></div></section>
      <section class="carte pad"><div class="entete-carte"><h2>Sécurité</h2><a class="btn btn-petit" href="#/utilisateurs">Utilisateurs</a></div>
        <p class="note" id="connecte-admin">${a.connecte ? `Connecté en admin : <strong>${esc(a.connecte.identifiant)}</strong> · ${a.connecte.rang === "super" ? "super admin" : "admin"}` : "Pas connecté en admin."}</p>
        <p class="note" style="margin:8px 0 14px">Le mot de passe du serveur protège les actions sensibles : activer un outil, mettre à jour, redémarrer, régler ou oublier un disque, supprimer un dossier entier, effacer la corbeille.</p>
        ${a.code_existe ? "" : `<div class="bandeau" style="margin-bottom:12px"><span>Aucun code de secours : crée-le maintenant, il sert si tu oublies le mot de passe.</span></div>`}
        <div class="actions"><button class="btn" id="changer-mdp">${a.connecte && a.connecte.identifiant !== a.serveur ? "Changer mon mot de passe" : "Changer le mot de passe du serveur"}</button>
        <button class="btn" id="code-secours">${a.code_existe ? "Nouveau code de secours" : "Créer le code de secours"}</button></div>
      </section>
      <section class="carte pad" id="alertes"><h2>Alertes</h2><div class="squelette" style="margin-top:10px"></div></section>
      <section class="carte pad"><div class="entete-carte"><h2>Journal</h2><a class="btn btn-petit" href="#/journal">Tout voir</a></div>
        <div class="journal" id="journal-court"><div class="squelette"></div></div></section>
      <section class="carte pad"><h2>Appareils Tailscale</h2>
        <div style="margin-top:10px">${s.appareils.map((a) => `<div class="appareil"><span>${esc(a.nom)} <span class="note">· ${esc(a.os)}</span></span>
          <span class="pastille ${a.en_ligne ? "p-ok" : "p-gris"}">${a.en_ligne ? "En ligne" : "Hors ligne"}</span></div>`).join("") || '<p class="note">Aucun autre appareil.</p>'}</div>
      </section>
    </div>`;
  carteAlertes().catch((e) => { const z = $("#alertes"); if (z) z.innerHTML = `<h2>Alertes</h2><p class="note">${esc(e.message)}</p>`; });
  carteMaj().catch((e) => { const z = $("#maj"); if (z) z.innerHTML = `<h2>Appli Jabba</h2><p class="note">${esc(e.message)}</p>`; });
  api("/api/journal?n=8").then((evs) => { const z = $("#journal-court"); if (z) z.innerHTML = evs.map(ligneJournal).join("") || '<p class="note">Rien pour l\'instant.</p>'; }).catch(() => {});
  $("#changer-mdp").onclick = async () => {
    if (a.connecte && a.connecte.identifiant !== a.serveur) { await choisirMonMotDePasse(false); return; }
    const v = await formulaire({
      titre: "Changer le mot de passe du serveur",
      texte: "C'est aussi le mot de passe de sudo dans le Terminal.",
      champs: [
        { nom: "identifiant", cache: true, valeur: utilisateurAdmin, autocomplete: "username" },
        { nom: "ancien", type: "password", label: "Mot de passe actuel", autocomplete: "current-password" },
        { nom: "nouveau", type: "password", label: "Nouveau mot de passe (10 caractères minimum)", autocomplete: "new-password" },
        { nom: "confirmation", type: "password", label: "Nouveau mot de passe, encore une fois", autocomplete: "new-password" },
      ],
      valider: "Changer",
    });
    if (!v || v === "secondaire") return;
    if (v.nouveau !== v.confirmation) return toast("Les deux mots de passe ne sont pas identiques.", true);
    try { await api("/api/admin/mot-de-passe", { method: "POST", json: { ancien: v.ancien, nouveau: v.nouveau } }); toast("Mot de passe changé : reconnecte-toi"); setTimeout(() => location.reload(), 1500); }
    catch (e) { toast(e.message, true); }
  };
  $("#code-secours").onclick = async () => {
    if (a.code_existe && !(await demander({ titre: "Créer un nouveau code de secours ?", texte: "L'ancien code ne marchera plus.", valider: "Créer" }))) return;
    try { const r = await api("/api/admin/code", { method: "POST" }); await montrerCode(r.code); pages.systeme().catch(montrerErreur); }
    catch (e) { toast(e.message, true); }
  };
  $("#reboot").onclick = async () => {
    if (!(await demander({ titre: "Redémarrer le serveur ?", texte: "Tous les outils seront coupés environ 1 minute.", valider: "Redémarrer", danger: true }))) return;
    try { await api("/api/systeme/redemarrer", { method: "POST" }); toast("Le serveur redémarre. Recharge la page dans une minute."); }
    catch (e) { toast(e.message, true); }
  };
};

pages.plus = async () => {
  contenu.innerHTML = `<div class="entete"><div><h1>Plus</h1></div></div>
    <section class="carte liste-raccourcis">
      <a href="#/disques"><span>Disques</span><span>›</span></a>
      <a href="#/corbeille"><span>Corbeille</span><span>›</span></a>
      <a href="#/systeme"><span>Système et alertes</span><span>›</span></a>
      <a href="#/sauvegardes"><span>Sauvegardes</span><span>›</span></a>
      <a href="#/utilisateurs"><span>Utilisateurs</span><span>›</span></a>
      <a href="#/nuage"><span>Comptes Nextcloud</span><span>›</span></a>
      <a href="#/invites"><span>Invités</span><span>›</span></a>
      <a href="#" id="sortir-mobile"><span>Se déconnecter</span><span>›</span></a>
      <a href="#/journal"><span>Journal</span><span>›</span></a>
      <a href="#/terminal"><span>Terminal</span><span>›</span></a>
      <a href="#/aide"><span>Aide</span><span>›</span></a>
    </section><p class="note" id="qui-mobile"></p>`;
  if (moi && moi.admin) $("#qui-mobile").textContent = `Connecté en ${moi.admin.rang === "super" ? "super admin" : "admin"} : ${moi.admin.identifiant} · version ${moi.version}`;
  $("#sortir-mobile").onclick = (ev) => { ev.preventDefault(); deconnexion(); };
};

// ------------------------------------------------------------ fichiers
const EXT = {
  image: ["jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "svg", "tif", "tiff"],
  video: ["mp4", "m4v", "mov", "webm", "mkv"],
  audio: ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"],
  pdf: ["pdf"],
  texte: ["txt", "md", "csv", "log", "json", "yaml", "yml", "ini", "conf", "srt", "nfo", "py", "sh"],
};
const VIGNETTES = ["jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff"];
const ext = (nom) => (nom.includes(".") ? nom.split(".").pop().toLowerCase() : "");
const genre = (nom) => Object.keys(EXT).find((g) => EXT[g].includes(ext(nom))) || null;
const urlVoir = (c) => "/api/fichiers/voir?chemin=" + encodeURIComponent(c);
const urlTelecharger = (c) => "/api/fichiers/telecharger?chemin=" + encodeURIComponent(c);

function iconeElement(e) {
  if (e.dossier) return `<span class="ic-dossier${e.externe ? " ext" : ""}" aria-hidden="true"></span>`;
  if (VIGNETTES.includes(ext(e.nom))) {
    return `<span class="vignette" aria-hidden="true"><img alt="" loading="lazy" src="/api/fichiers/apercu?chemin=${encodeURIComponent(e.chemin)}" onerror="this.parentNode.className='ic-fichier';this.remove()"></span>`;
  }
  return `<span class="ic-fichier${genre(e.nom) ? " " + genre(e.nom) : ""}" aria-hidden="true"></span>`;
}

// Visionneuse : photos, vidéos, musique, PDF et textes, sans quitter l'appli.
const visionneuse = $("#visionneuse");
async function voir(liste, i) {
  const e = liste[i];
  const g = genre(e.nom);
  const visibles = liste.filter((x) => !x.dossier && genre(x.nom));
  const pos = visibles.indexOf(e);
  let corps;
  if (g === "image") corps = `<img src="${urlVoir(e.chemin)}" alt="${esc(e.nom)}">`;
  else if (g === "video") corps = `<div class="lecteur" id="lecteur"><p class="note">Préparation de la vidéo…</p></div>`;
  else if (g === "audio") corps = `<div class="audio"><span class="ic-fichier audio grand" aria-hidden="true"></span><audio src="${urlVoir(e.chemin)}" controls autoplay></audio></div>`;
  else if (g === "pdf") corps = `<iframe src="${urlVoir(e.chemin)}" title="${esc(e.nom)}"></iframe>`;
  else corps = `<pre class="texte">Chargement…</pre>`;
  visionneuse.innerHTML = `
    <div class="barre-vision">
      <button class="btn btn-petit" data-v="fermer">✕ Fermer</button>
      <strong class="nom-vision">${esc(e.nom)}</strong>
      <span class="actions">
        ${visibles.length > 1 ? `<button class="btn btn-petit" data-v="prec" aria-label="Précédent">‹</button><span class="note">${pos + 1} / ${visibles.length}</span><button class="btn btn-petit" data-v="suiv" aria-label="Suivant">›</button>` : ""}
        ${g === "pdf" ? `<a class="btn btn-petit" href="${urlVoir(e.chemin)}" target="_blank" rel="noopener">Plein écran ↗</a>` : ""}
        ${g === "video" ? `<button class="btn btn-petit" data-v="jellyfin">Regarder dans Jellyfin</button>` : ""}
        <a class="btn btn-petit" href="${urlTelecharger(e.chemin)}">Télécharger</a>
      </span>
    </div>
    <div class="corps-vision">${corps}</div>`;
  if (!visionneuse.open) visionneuse.showModal();
  const aller = (d) => { const j = (pos + d + visibles.length) % visibles.length; voir(liste, liste.indexOf(visibles[j])); };
  visionneuse.querySelector('[data-v="fermer"]').onclick = () => visionneuse.close();
  const p = visionneuse.querySelector('[data-v="prec"]'), n = visionneuse.querySelector('[data-v="suiv"]');
  if (p) p.onclick = () => aller(-1);
  if (n) n.onclick = () => aller(1);
  visionneuse.onkeydown = (ev) => { if (g === "video") return; /* flèches = ±10 s dans le lecteur */ if (ev.key === "ArrowLeft" && p) aller(-1); if (ev.key === "ArrowRight" && n) aller(1); };
  const jf = visionneuse.querySelector('[data-v="jellyfin"]');
  if (jf) jf.onclick = () => regarderDansJellyfin(e).catch((er) => toast(er.message, true));
  if (g === "video") lancerVideo(e).catch((er) => { const l = $("#lecteur"); if (l) l.innerHTML = `<div class="message-vision"><p>${esc(er.message)}</p></div>`; });
  const media = visionneuse.querySelector("audio");
  if (media) media.addEventListener("error", () => {
    visionneuse.querySelector(".corps-vision").innerHTML = `<div class="message-vision"><p>Ce format ne se lit pas dans le navigateur.</p><p class="note">${g === "video" ? "Regarde-le dans Jellyfin, ou télécharge-le." : "Télécharge-le pour l'écouter."}</p></div>`;
  });
  if (g === "texte") {
    try {
      const r = await fetch(urlVoir(e.chemin), { headers: { Range: "bytes=0-1048575" } });
      if (!r.ok) throw new Error(`Erreur ${r.status}`);
      const t = await r.text();
      const pre = visionneuse.querySelector("pre.texte");
      if (pre) pre.textContent = t;
    } catch (er) { const pre = visionneuse.querySelector("pre.texte"); if (pre) pre.textContent = er.message; }
  }
}
visionneuse.addEventListener("close", () => { if (lecteurActif) lecteurActif.fin(); visionneuse.innerHTML = ""; });

// Ouvre ce film dans Jellyfin. La première fois, Jabba demande la clé d'accès Jellyfin.
async function regarderDansJellyfin(e) {
  const url = "/api/jellyfin/trouver?chemin=" + encodeURIComponent(e.chemin);
  let r = await fetch(url, { credentials: "same-origin" });
  let corps = await r.json().catch(() => ({}));
  if (r.status === 409 && corps.detail && corps.detail.cle_jellyfin) {
    const v = await formulaire({
      titre: "Relier Jabba à Jellyfin",
      texte: "Une seule fois : dans Jellyfin, Tableau de bord → Clés API → +, nom « Jabba », puis copie la clé et colle-la ici.",
      champs: [{ nom: "cle", label: "Clé API Jellyfin", indice: "32 caractères" }],
      valider: "Relier",
    });
    if (!v || v === "secondaire") return;
    await api("/api/jellyfin/cle", { method: "POST", json: { cle: v.cle } });
    toast("Jabba est relié à Jellyfin");
    r = await fetch(url, { credentials: "same-origin" });
    corps = await r.json().catch(() => ({}));
  }
  if (!r.ok) throw new Error((corps.detail && (corps.detail.message || corps.detail)) || `Erreur ${r.status}`);
  if (lecteurActif) { const v = $("#lecteur video"); if (v) v.pause(); }
  window.open(corps.lien, "_blank", "noopener");
}

// ------------------------------------------------------------ lecteur vidéo
// Si le navigateur sait lire le fichier, on le lit tel quel. Sinon le serveur le
// convertit pendant la lecture (comme VLC le ferait), en HLS : Safari le lit seul,
// les autres navigateurs avec hls.js (livré avec l'appli).
let lecteurActif = null;
const tempsLisible = (s) => { s = Math.max(0, Math.floor(s || 0)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };
function chargerHls() {
  if (window.Hls) return Promise.resolve();
  return new Promise((ok, ko) => { const s = document.createElement("script"); s.src = "/static/vendor/hls.min.js"; s.onload = ok; s.onerror = ko; document.head.appendChild(s); });
}

async function lancerVideo(e) {
  if (lecteurActif) lecteurActif.fin();
  let infos = null;
  try { infos = await api("/api/lecture/infos?chemin=" + encodeURIComponent(e.chemin)); } catch (_) { /* moteur indisponible : lecture directe */ }
  if (!$("#lecteur")) return;
  // Même lecteur pour toutes les vidéos : lu tel quel quand le navigateur sait le faire, converti sinon.
  return lecteur(e, infos || { duree: 0, audios: [], soustitres: [], video: { codec: "" }, inconnu: true }, !infos || infos.directe);
}

async function lecteur(e, infos, direct) {
  const zone = $("#lecteur");
  if (!zone) return;
  const audioDefaut = (infos.audios.find((a) => a.defaut) || infos.audios[0] || {}).index;
  const st = { sid: null, debut: 0, audio: audioDefaut, sous: null, hls: null, glisse: false, fini: false };
  const textes = infos.soustitres.filter((x) => x.texte);
  zone.innerHTML = `
    <video playsinline webkit-playsinline disablepictureinpicture></video>
    <div class="saut-indic gauche" id="saut-gauche" hidden></div><div class="saut-indic droite" id="saut-droite" hidden></div>
    <div class="etat-lecteur" id="etat-lecteur">${direct ? "Chargement…" : "Le serveur prépare la vidéo…"}</div>
    <div class="commandes">
      <button class="btn btn-petit" type="button" id="lect-jouer" aria-label="Lecture">▶</button>
      <span class="temps" id="lect-temps">0:00</span>
      <input type="range" id="lect-barre" min="0" max="${Math.floor(infos.duree)}" value="0" step="1" aria-label="Position">
      <span class="temps" id="lect-duree">${tempsLisible(infos.duree)}</span>
      ${infos.audios.length > 1 ? `<select id="lect-audio" aria-label="Son">${infos.audios.map((a) => `<option value="${a.index}"${a.index === audioDefaut ? " selected" : ""}>Son · ${esc(a.nom)}</option>`).join("")}</select>` : ""}
      ${textes.length ? `<select id="lect-sous" aria-label="Sous-titres"><option value="">Sans sous-titres</option>${textes.map((x) => `<option value="${x.index}">Sous-titres · ${esc(x.nom)}</option>`).join("")}</select>` : ""}
      <button class="btn btn-petit" type="button" id="lect-son" aria-label="Couper le son">🔊</button>
      <input type="range" id="lect-volume" class="volume" min="0" max="1" step="0.05" value="1" aria-label="Volume">
      <button class="btn btn-petit" type="button" id="lect-plein" aria-label="Plein écran">⛶</button>
    </div>
    <p class="note lect-info">${direct ? "Lu directement" : `Lu par le serveur (format ${esc((infos.video.codec || "?").toUpperCase())})`}${infos.soustitres.length > textes.length ? " · certains sous-titres sont des images : non affichés" : ""} · touche deux fois à gauche ou à droite de l'image : 10 s</p>`;
  const video = zone.querySelector("video"), barre = $("#lect-barre"), temps = $("#lect-temps"), jouer = $("#lect-jouer"), etat = $("#etat-lecteur");
  const montrer = (t) => { etat.textContent = t; etat.hidden = !t; };

  const sousTitres = () => {
    video.querySelectorAll("track").forEach((t) => t.remove());
    if (st.sous === null) return;
    const t = document.createElement("track");
    t.kind = "subtitles"; t.default = true; t.label = "Sous-titres";
    t.src = `/api/lecture/soustitres?chemin=${encodeURIComponent(e.chemin)}&piste=${st.sous}&debut=${st.debut}`;
    video.appendChild(t);
    t.addEventListener("load", () => { t.track.mode = "showing"; });
  };

  const depuis = async (t) => {
    if (direct) {   // fichier lu tel quel : le navigateur va où on veut tout seul
      if (!st.sid) {
        st.sid = "direct";
        video.src = urlVoir(e.chemin);
        sousTitres();
        video.addEventListener("error", () => { if (!st.fini && !infos.inconnu) { direct = false; st.sid = null; depuis(t); } else montrer("Ce format ne se lit pas dans le navigateur."); }, { once: true });
        video.addEventListener("loadedmetadata", () => {
          if (!infos.duree && isFinite(video.duration)) { infos.duree = video.duration; barre.max = String(Math.floor(video.duration)); $("#lect-duree").textContent = tempsLisible(video.duration); }
          if (t) video.currentTime = t;
        }, { once: true });
        video.play().catch(() => montrer("Clique sur ▶ pour lancer la lecture."));
      } else video.currentTime = t;
      return;
    }
    montrer("Le serveur prépare la vidéo…");
    if (st.hls) { st.hls.destroy(); st.hls = null; }
    video.removeAttribute("src"); video.load();
    let r;
    try { r = await api("/api/lecture/demarrer", { method: "POST", json: { chemin: e.chemin, debut: t, audio: st.audio, remplace: st.sid } }); }
    catch (er) { montrer(er.message); return; }
    if (st.fini) { api(`/api/lecture/${r.id}/arreter`, { method: "POST" }).catch(() => {}); return; }
    st.sid = r.id; st.debut = r.debut ?? t;   // le serveur peut repartir un peu avant (image clé)…
    // … on avance alors jusqu'au moment demandé. Et les lecteurs HLS intégrés (Safari, Chrome récent)
    // partent « au plus récent » d'une liste qui grandit : on les ramène au bon endroit.
    const cible = Math.max(0, t - st.debut);
    video.addEventListener("loadedmetadata", () => { if (Math.abs(video.currentTime - cible) > 0.5) video.currentTime = cible; }, { once: true });
    sousTitres();
    // hls.js partout où il marche (iPhone récent compris) : il suit nos consignes. Le lecteur
    // intégré du navigateur, qui saute « au plus récent », ne sert qu'en dernier recours.
    try { await chargerHls(); } catch (_) { /* hors ligne : lecteur intégré */ }
    if (!(window.Hls && window.Hls.isSupported())) {
      video.src = r.url;
      st.garde = true;
      setTimeout(() => { st.garde = false; }, 8000);
      video.addEventListener("timeupdate", function garde() {
        if (!st.garde) return video.removeEventListener("timeupdate", garde);
        if (video.currentTime > cible + 3) video.currentTime = cible;
      });
    } else {
      // La liste grandit pendant la conversion : hls.js la prendrait pour du direct et sauterait
      // « au plus récent ». On le lui interdit : lecture depuis le début, sans rattrapage.
      st.hls = new window.Hls({ autoStartLoad: false, maxBufferLength: 40, manifestLoadingMaxRetry: 10, levelLoadingMaxRetry: 10, fragLoadingMaxRetry: 10,
        liveSyncDurationCount: 1e9, liveMaxLatencyDurationCount: Infinity, maxLiveSyncPlaybackRate: 1, liveDurationInfinity: true, lowLatencyMode: false });
      st.hls.on(window.Hls.Events.MANIFEST_PARSED, () => st.hls.startLoad(0));   // toujours depuis le premier morceau
      st.hls.on(window.Hls.Events.ERROR, (_, d) => { if (d.fatal) montrer("Lecture interrompue. Clique sur ▶ pour reprendre."); });
      st.hls.loadSource(r.url); st.hls.attachMedia(video);
    }
    video.play().catch(() => montrer("Clique sur ▶ pour lancer la lecture."));
  };

  video.addEventListener("playing", () => montrer(""));
  video.addEventListener("waiting", () => montrer("Chargement…"));
  video.addEventListener("play", () => { jouer.textContent = "❚❚"; });
  video.addEventListener("pause", () => { jouer.textContent = "▶"; });
  video.addEventListener("timeupdate", () => {
    const t = st.debut + video.currentTime;
    temps.textContent = tempsLisible(t);
    if (!st.glisse) barre.value = String(Math.floor(t));
  });
  jouer.onclick = () => { if (!st.sid) depuis(0); else if (video.paused) video.play(); else video.pause(); };
  barre.addEventListener("input", () => { st.glisse = true; temps.textContent = tempsLisible(+barre.value); });
  const position = () => st.debut + video.currentTime;
  const allerA = (t) => {
    t = Math.max(0, Math.min(t, (infos.duree || Infinity) - 1));
    temps.textContent = tempsLisible(t); barre.value = String(Math.floor(t));
    const dans = t - st.debut;
    const fin = video.buffered.length ? video.buffered.end(video.buffered.length - 1) : 0;
    if (direct || (dans >= 0 && dans <= fin)) { st.garde = false; if (direct) depuis(t); else video.currentTime = dans; }   // déjà converti : on y va directement
    else depuis(t);
  };
  barre.addEventListener("change", () => { st.glisse = false; allerA(+barre.value); });
  // Plusieurs appuis rapides sur ±10 s s'additionnent avant de relancer (utile quand le serveur convertit).
  let saut = null;
  const decaler = (d) => {
    if (!saut) saut = { cible: position() };
    saut.cible = Math.max(0, saut.cible + d);
    temps.textContent = tempsLisible(saut.cible); barre.value = String(Math.floor(saut.cible));
    clearTimeout(saut.minuteur);
    saut.minuteur = setTimeout(() => { const c = saut.cible; saut = null; allerA(c); }, 350);
  };
  // Comme YouTube : deux touches rapides à gauche ou à droite de l'image = −10 s / +10 s,
  // puis chaque touche suivante du même côté ajoute 10 s. Une seule touche = pause / lecture.
  const indic = { "-1": $("#saut-gauche"), "1": $("#saut-droite") };
  let tape = { t: 0, cote: 0, serie: 0, minuteur: null, masque: null };
  const montrerSaut = (cote) => {
    const el = indic[cote]; el.textContent = (cote < 0 ? "« −" : "+") + (tape.serie * 10) + " s" + (cote < 0 ? "" : " »");
    el.hidden = false; clearTimeout(tape.masque); tape.masque = setTimeout(() => { el.hidden = true; }, 700);
  };
  video.onclick = null;
  video.addEventListener("click", (ev) => {
    const r = video.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width;
    const cote = x < 0.35 ? -1 : x > 0.65 ? 1 : 0, maintenant = Date.now();
    const cachees = zone.classList.contains("repos");
    reveiller();
    if (cachees && !cote) return;   // commandes cachées : une touche au centre les fait juste revenir
    if (cote && ((maintenant - tape.t < 320 && tape.cote === cote) || (tape.serie && tape.cote === cote && maintenant - tape.t < 700))) {
      clearTimeout(tape.minuteur);
      tape.serie = tape.serie ? tape.serie + 1 : 1;
      tape.t = maintenant; tape.cote = cote;
      decaler(10 * cote); montrerSaut(cote);
      return;
    }
    tape = { ...tape, t: maintenant, cote, serie: 0 };
    clearTimeout(tape.minuteur);
    tape.minuteur = setTimeout(() => { if (!tape.serie) jouer.onclick(); }, cote ? 320 : 0);
  });
  video.addEventListener("dblclick", (ev) => ev.preventDefault());
  const clavier = (ev) => {
    if (!visionneuse.open || /INPUT|SELECT|TEXTAREA/.test(ev.target.tagName)) return;
    if (ev.key === "ArrowLeft") { ev.preventDefault(); decaler(-10); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); decaler(10); }
    else if (ev.key === " " || ev.key === "k") { ev.preventDefault(); jouer.onclick(); }
    else if (ev.key === "m") { ev.preventDefault(); $("#lect-son").onclick(); }
    else if (ev.key === "f") { ev.preventDefault(); $("#lect-plein").onclick(); }
    if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") { const c = ev.key === "ArrowLeft" ? -1 : 1; tape.serie = tape.cote === c && Date.now() - tape.t < 700 ? tape.serie + 1 : 1; tape.cote = c; tape.t = Date.now(); montrerSaut(c); }
  };
  document.addEventListener("keydown", clavier);
  const audio = $("#lect-audio");
  if (audio) audio.onchange = () => { st.audio = +audio.value; depuis(st.debut + video.currentTime); };
  const sous = $("#lect-sous");
  if (sous) sous.onchange = () => { st.sous = sous.value === "" ? null : +sous.value; sousTitres(); };
  const sonBtn = $("#lect-son"), volume = $("#lect-volume");
  const majSon = () => { sonBtn.textContent = video.muted || video.volume === 0 ? "🔇" : "🔊"; sonBtn.setAttribute("aria-label", video.muted ? "Remettre le son" : "Couper le son"); volume.value = String(video.muted ? 0 : video.volume); };
  try { const v = parseFloat(localStorage.getItem("jabba-volume")); if (v >= 0 && v <= 1) video.volume = v; } catch (_) { /* rien */ }
  sonBtn.onclick = () => { video.muted = !video.muted; if (!video.muted && video.volume === 0) video.volume = 0.5; majSon(); };
  volume.addEventListener("input", () => { video.volume = +volume.value; video.muted = +volume.value === 0; try { localStorage.setItem("jabba-volume", volume.value); } catch (_) { /* rien */ } majSon(); });
  video.addEventListener("volumechange", majSon);
  majSon();
  // Plein écran avec NOS commandes. Sur iPhone, le plein écran du système reprendrait la vidéo
  // dans son propre lecteur (marqué « direct », barre fausse) : on remplit l'écran nous-mêmes.
  const pleinActif = () => document.fullscreenElement === zone || document.webkitFullscreenElement === zone || zone.classList.contains("plein");
  $("#lect-plein").onclick = () => {
    if (document.fullscreenElement || document.webkitFullscreenElement) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else if (zone.classList.contains("plein")) zone.classList.remove("plein");
    else if (zone.requestFullscreen) zone.requestFullscreen().catch(() => zone.classList.add("plein"));
    else if (zone.webkitRequestFullscreen) zone.webkitRequestFullscreen();
    else zone.classList.add("plein");
    reveiller();
  };
  // En plein écran, les commandes se cachent après 3 s sans toucher ; une touche les fait revenir.
  let repos = null;
  function reveiller() {
    zone.classList.remove("repos"); clearTimeout(repos);
    repos = setTimeout(() => { if (pleinActif() && !video.paused) zone.classList.add("repos"); }, 3000);
  }
  zone.addEventListener("pointermove", reveiller);
  video.addEventListener("pause", reveiller);
  visionneuse.addEventListener("cancel", (ev) => { if (zone.classList.contains("plein")) { ev.preventDefault(); zone.classList.remove("plein"); } }, { once: true });
  lecteurActif = {
    fin: () => {
      st.fini = true;
      document.removeEventListener("keydown", clavier);
      if (st.hls) st.hls.destroy();
      if (st.sid && st.sid !== "direct") api(`/api/lecture/${st.sid}/arreter`, { method: "POST" }).catch(() => {});
      video.removeAttribute("src"); video.load();
      lecteurActif = null;
    },
  };
  depuis(0);
}

function ouvrirElement(liste, i) {
  const e = liste[i];
  if (e.dossier) location.hash = lienDossier(e.chemin);
  else if (genre(e.nom)) voir(liste, i);
  else location.href = urlTelecharger(e.chemin);
}

const barreRecherche = (q = "") => `<form class="recherche" id="form-recherche" role="search">
    <input type="search" name="q" value="${esc(q)}" placeholder="Chercher un fichier ou un dossier, partout" aria-label="Chercher" autocomplete="off" enterkeyhint="search">
  </form>`;
function brancherRecherche() {
  const f = $("#form-recherche");
  if (f) f.onsubmit = (ev) => { ev.preventDefault(); const q = f.q.value.trim(); if (q.length >= 2) location.hash = "#/fichiers?q=" + encodeURIComponent(q); };
}

async function pageRecherche(q) {
  contenu.innerHTML = `<div class="entete"><div><h1>Recherche</h1><p class="sous-titre">« ${esc(q)} »</p></div></div>${barreRecherche(q)}${chargement(4)}`;
  brancherRecherche();
  const r = await api("/api/recherche?q=" + encodeURIComponent(q));
  const el = r.elements;
  contenu.innerHTML = `
    <div class="entete"><div><h1>Recherche</h1><p class="sous-titre">${el.length} résultat${el.length > 1 ? "s" : ""} pour « ${esc(q)} »</p></div>
      <div class="actions"><a class="btn" href="#/fichiers">← Fichiers</a></div></div>
    ${barreRecherche(q)}
    <section class="carte tableau" aria-label="Résultats">
      ${el.length ? el.map((e, i) => `
        <div class="ligne">
          <span class="nom">${iconeElement(e)}
            <span><a href="#" data-ouvrir="${i}">${esc(e.nom)}</a>
            <span class="detail-chemin"><a href="${lienDossier(e.chemin.split("/").slice(0, -1).join("/") || "/")}">${esc(e.chemin.split("/").slice(0, -1).join("/") || "/")}</a></span></span></span>
          <span class="gris col-l">${e.dossier ? "Dossier" : octets(e.taille)}</span>
          <span class="gris col-l">${quand(e.modifie)}</span>
          <span></span>
        </div>`).join("") : `<div class="vide">Rien trouvé. Essaie un autre mot, ou une partie du nom.</div>`}
    </section>
    ${r.complet ? "" : `<p class="note">La recherche s'est arrêtée avant la fin (trop de fichiers). Précise ta recherche.</p>`}`;
  brancherRecherche();
  contenu.querySelectorAll("[data-ouvrir]").forEach((a) => a.addEventListener("click", (ev) => { ev.preventDefault(); ouvrirElement(el, +a.dataset.ouvrir); }));
}

// Historique de navigation dans Fichiers : Précédent / Suivant, comme dans le Finder.
const navFichiers = { pile: [], pos: -1, saut: null };
function noterDossier(chemin) {
  const n = navFichiers;
  if (n.saut !== null && n.pile[n.saut] === chemin) n.pos = n.saut;
  else if (n.pile[n.pos - 1] === chemin) n.pos -= 1;        // retour du navigateur ou de la souris
  else if (n.pile[n.pos + 1] === chemin) n.pos += 1;
  else if (n.pile[n.pos] !== chemin) { n.pile = n.pile.slice(0, n.pos + 1).concat(chemin).slice(-100); n.pos = n.pile.length - 1; }
  n.saut = null;
}
function allerDansHistorique(sens) {
  const n = navFichiers, cible = n.pos + sens;
  if (cible < 0 || cible >= n.pile.length) return;
  n.saut = cible;
  location.hash = "#/fichiers?chemin=" + encodeURIComponent(n.pile[cible]);
}
function barreNavigation(chemin, lien, racine) {
  const n = navFichiers, parent = "/" + chemin.split("/").filter(Boolean).slice(0, -1).join("/");
  return `<div class="nav-fichiers" role="group" aria-label="Navigation">
    <button class="btn btn-petit" id="nav-prec" title="Précédent (⌘[)" aria-label="Précédent"${n.pos > 0 ? "" : " disabled"}>‹</button>
    <button class="btn btn-petit" id="nav-suiv" title="Suivant (⌘])" aria-label="Suivant"${n.pos < n.pile.length - 1 ? "" : " disabled"}>›</button>
    ${chemin !== "/" ? `<a class="btn btn-petit" href="${lien(parent)}" title="Dossier parent (⌘↑)">↑ Dossier parent</a>
    <a class="btn btn-petit" href="${lien("/")}" title="Revenir au début">⌂ ${esc(racine)}</a>` : ""}
  </div>`;
}
document.addEventListener("keydown", (e) => {
  if (!location.hash.startsWith("#/fichiers") || location.hash.includes("q=") || dialogue.open || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
  const cmd = e.metaKey || e.ctrlKey;
  if ((cmd && e.key === "[") || (e.altKey && e.key === "ArrowLeft")) { e.preventDefault(); allerDansHistorique(-1); }
  else if ((cmd && e.key === "]") || (e.altKey && e.key === "ArrowRight")) { e.preventDefault(); allerDansHistorique(1); }
  else if (cmd && e.key === "ArrowUp") { e.preventDefault(); const h = $("#nav-fichiers-parent"); if (h) location.hash = h.getAttribute("href"); }
});

// Emplacements où naviguer : Mes fichiers, puis chaque espace et chaque disque branché.
const cacheEmplacements = { t: 0, v: null };
async function lireEmplacements() {
  if (cacheEmplacements.v && Date.now() - cacheEmplacements.t < 30000) return cacheEmplacements.v;
  try {
    const r = await api("/api/accueil/dossiers");
    cacheEmplacements.v = r.essentiels; cacheEmplacements.t = Date.now();
  } catch (_) { cacheEmplacements.v = cacheEmplacements.v || []; }
  return cacheEmplacements.v;
}
function choixEmplacement(liste, chemin, lien) {
  if (!liste || liste.length < 2) return "";
  const actif = [...liste].filter((e) => e.chemin !== "/" && (chemin === e.chemin || chemin.startsWith(e.chemin + "/")))[0];
  return `<nav class="emplacements" aria-label="Emplacement">${liste.map((e) => {
    const sel = actif ? actif === e : e.chemin === "/";
    const ic = e.type === "disque" ? "ic-disque" : e.type === "racine" ? "ic-maison" : "ic-dossier";
    return `<a class="puce-emplacement${sel ? " actif" : ""}" href="${lien(e.chemin)}"${sel ? ' aria-current="true"' : ""}><span class="${ic}" aria-hidden="true"></span>${esc(e.nom)}</a>`;
  }).join("")}</nav>`;
}

pages.fichiers = async (params) => {
  if (params.get("q")) return pageRecherche(params.get("q"));
  const chemin = params.get("chemin") || "/";
  noterDossier(chemin);
  contenu.innerHTML = `<div class="entete"><div><h1>Fichiers</h1></div></div>${chargement(5)}`;
  const [d, emplacements] = await Promise.all([api("/api/fichiers?chemin=" + encodeURIComponent(chemin)), lireEmplacements()]);
  const morceaux = chemin.split("/").filter(Boolean);
  const lien = (c) => "#/fichiers?chemin=" + encodeURIComponent(c);
  const fil = [`<a href="${lien("/")}">${d.source === "disque" ? "Serveur" : "Nextcloud"}</a>`]
    .concat(morceaux.map((m, i) => i === morceaux.length - 1
      ? `<strong>${esc(m)}</strong>`
      : `<a href="${lien("/" + morceaux.slice(0, i + 1).join("/"))}">${esc(m)}</a>`))
    .join('<span class="sep">/</span>');
  const titre = morceaux.length ? morceaux[morceaux.length - 1] : "Fichiers";
  contenu.innerHTML = `
    <div class="entete">
      <div><h1>${esc(titre)}${d.ecriture ? "" : '<span class="lecture-seule">Lecture seule</span>'}</h1>
        <p class="sous-titre">${d.source === "disque" ? "Disque du serveur (Nextcloud désactivé)" : "Ton Nextcloud · synchronisé avec l'iPhone et le Finder"}</p></div>
      <div class="actions">
        ${chemin !== "/" ? `<button class="btn" id="etoile" aria-pressed="${d.favori}">${d.favori ? "★ Favori" : "☆ Favori"}</button>` : ""}
        <a class="btn" href="#/corbeille">Corbeille</a>
        ${d.lien_nextcloud ? `<a class="btn" href="${esc(d.lien_nextcloud)}" target="_blank" rel="noopener">Ouvrir dans Nextcloud</a>` : ""}
        ${d.ecriture ? `<button class="btn" id="nouveau">Nouveau dossier</button><button class="btn btn-p" id="envoyer">Envoyer des fichiers</button>` : ""}
      </div>
    </div>
    ${barreRecherche()}
    ${choixEmplacement(emplacements, chemin, lien)}
    <div class="ligne-nav">${barreNavigation(chemin, lien, d.source === "disque" ? "Serveur" : "Nextcloud")}
    <nav class="fil" aria-label="Emplacement">${fil}</nav></div>
    <section class="carte tableau" aria-label="Contenu du dossier">
      <div class="ligne titres"><span>Nom</span><span class="col-l">Taille</span><span class="col-l">Modifié</span><span></span></div>
      ${d.elements.length ? d.elements.map((e, i) => `
        <div class="ligne"${e.dossier && e.ecriture ? ` data-dossier="${esc(e.chemin)}"` : ""}>
          <span class="nom">${iconeElement(e)}
            <span>${e.dossier ? `<a href="${lien(e.chemin)}">${esc(e.nom)}</a>` : `<a href="${urlTelecharger(e.chemin)}" data-ouvrir="${i}">${esc(e.nom)}</a>`}
            <span class="mobile-detail">${octets(e.taille)} · ${quand(e.modifie)}</span></span></span>
          <span class="gris col-l">${octets(e.taille)}</span>
          <span class="gris col-l">${quand(e.modifie)}</span>
          <button class="btn-menu" data-i="${i}" aria-label="Actions pour ${esc(e.nom)}">•••</button>
        </div>`).join("") : `<div class="vide">Ce dossier est vide.</div>`}
      ${d.ecriture ? `<div class="depot" id="depot">Glisse des fichiers ici pour les envoyer dans « ${esc(titre)} »</div>` : ""}
    </section>
    <input type="file" id="choix" multiple hidden>`;

  const recharger = () => pages.fichiers(params).catch(montrerErreur);
  $("#nav-prec").onclick = () => allerDansHistorique(-1);
  $("#nav-suiv").onclick = () => allerDansHistorique(1);
  const lienParent = contenu.querySelector(".nav-fichiers a");
  if (lienParent) lienParent.id = "nav-fichiers-parent";
  brancherRecherche();
  contenu.querySelectorAll("[data-ouvrir]").forEach((a) => a.addEventListener("click", (ev) => {
    const e = d.elements[+a.dataset.ouvrir];
    if (genre(e.nom)) { ev.preventDefault(); voir(d.elements, +a.dataset.ouvrir); }
  }));
  const etoile = $("#etoile");
  if (etoile) etoile.onclick = async () => { try { await basculerFavori(chemin, d.favori); } catch (er) { toast(er.message, true); } recharger(); };
  const envoyer = async (fichiers) => {
    if (!fichiers.length) return;
    const fd = new FormData();
    for (const f of fichiers) fd.append("fichiers", f, f.name);
    toast(`Envoi de ${fichiers.length} fichier${fichiers.length > 1 ? "s" : ""}…`);
    try {
      const r = await api("/api/fichiers/envoyer?chemin=" + encodeURIComponent(chemin), { method: "POST", body: fd });
      toast(`${r.envoyes.length} fichier${r.envoyes.length > 1 ? "s envoyés" : " envoyé"}`);
    } catch (e) { toast(e.message, true); }
    recharger();
  };

  if (d.ecriture) {
    $("#nouveau").onclick = async () => {
      const nom = await demander({ titre: "Nouveau dossier", champ: "", valider: "Créer" });
      if (!nom) return;
      if (/[\\/]/.test(nom)) return toast("Le nom ne peut pas contenir / ou \\", true);
      try { await api("/api/fichiers/dossier", { method: "POST", json: { chemin: chemin + "/" + nom } }); toast("Dossier créé"); }
      catch (e) { toast(e.message, true); }
      recharger();
    };
    const choix = $("#choix");
    $("#envoyer").onclick = () => choix.click();
    choix.onchange = () => envoyer([...choix.files]);
    const depot = $("#depot"), zone = $(".tableau");
    ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (ev) => { ev.preventDefault(); depot.classList.add("survol"); }));
    ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (ev) => { ev.preventDefault(); if (t === "drop" || !zone.contains(ev.relatedTarget)) depot.classList.remove("survol"); }));
    zone.addEventListener("drop", (ev) => { if (!ev.defaultPrevented || !ev.jabbaPris) envoyer([...ev.dataTransfer.files].filter((f) => f.size || f.type)); });
  }

  // Glisser des fichiers directement sur un dossier de la liste.
  contenu.querySelectorAll(".ligne[data-dossier]").forEach((ligne) => activerDepot(ligne, ligne.dataset.dossier, recharger));

  contenu.querySelectorAll(".btn-menu").forEach((b) => b.addEventListener("click", async () => {
    const e = d.elements[+b.dataset.i];
    const parent = chemin.replace(/\/$/, "");
    const choix = [];
    if (e.dossier) {
      choix.push({ texte: "Ouvrir", f: () => (location.hash = lien(e.chemin)) });
      choix.push({ texte: "Ajouter à l'accès rapide", f: async () => { try { await basculerFavori(e.chemin, false); } catch (er) { toast(er.message, true); } } });
    }
    else {
      if (genre(e.nom)) choix.push({ texte: "Voir", f: () => voir(d.elements, +b.dataset.i) });
      choix.push({ texte: "Télécharger", f: () => (location.href = urlTelecharger(e.chemin)) });
    }
    if (e.ecriture && d.ecriture) {
      choix.push({ texte: "Renommer", f: async () => {
        const nom = await demander({ titre: "Renommer", champ: e.nom, valider: "Renommer" });
        if (!nom || nom === e.nom) return;
        if (/[\\/]/.test(nom)) return toast("Le nom ne peut pas contenir / ou \\", true);
        try { await api("/api/fichiers/deplacer", { method: "POST", json: { de: e.chemin, vers: parent + "/" + nom } }); toast("Renommé"); }
        catch (er) { toast(er.message, true); }
        recharger();
      } });
      choix.push({ texte: "Déplacer vers…", f: async () => {
        const dest = await demander({ titre: "Déplacer vers", texte: "Chemin du dossier de destination, par exemple /Documents/Archives", champ: parent || "/", valider: "Déplacer" });
        if (!dest) return;
        try { await api("/api/fichiers/deplacer", { method: "POST", json: { de: e.chemin, vers: dest.replace(/\/$/, "") + "/" + e.nom } }); toast("Déplacé"); }
        catch (er) { toast(er.message, true); }
        recharger();
      } });
      choix.push({ texte: "Supprimer", danger: true, f: async () => {
        const texte = "Il part dans la corbeille : tu peux le récupérer pendant 30 jours (Fichiers → Corbeille)."
          + (e.dossier ? " Supprimer un dossier entier demande le mot de passe du serveur." : "");
        if (!(await demander({ titre: `Supprimer « ${e.nom} » ?`, texte, valider: "Supprimer", danger: true }))) return;
        try { await api("/api/fichiers/supprimer", { method: "POST", json: { chemin: e.chemin, dossier: e.dossier } }); toast("Mis à la corbeille"); }
        catch (er) { toast(er.message, true); }
        recharger();
      } });
    }
    const c = await menu(e.nom, choix);
    if (c) setTimeout(c.f, 50);
  }));
};

// ------------------------------------------------------------ corbeille
pages.corbeille = async () => {
  contenu.innerHTML = `<div class="entete"><div><h1>Corbeille</h1></div></div>${chargement(4)}`;
  const c = await api("/api/corbeille");
  const el = c.elements;
  const jours = (t) => Math.max(0, c.garde - Math.floor((Date.now() / 1000 - t) / 86400));
  contenu.innerHTML = `
    <div class="entete"><div><h1>Corbeille</h1><p class="sous-titre">Ce que tu as supprimé, gardé ${c.garde} jours puis effacé tout seul.</p></div>
      <div class="actions"><a class="btn" href="#/fichiers">← Fichiers</a>${el.length ? `<button class="btn btn-danger" id="vider">Vider la corbeille</button>` : ""}</div></div>
    <section class="carte tableau" aria-label="Éléments supprimés">
      <div class="ligne titres"><span>Nom</span><span class="col-l">Taille</span><span class="col-l">Supprimé</span><span></span></div>
      ${el.length ? el.map((e, i) => `
        <div class="ligne">
          <span class="nom">${e.dossier ? '<span class="ic-dossier" aria-hidden="true"></span>' : `<span class="ic-fichier${genre(e.nom) ? " " + genre(e.nom) : ""}" aria-hidden="true"></span>`}
            <span>${esc(e.nom)}<span class="detail-chemin">${e.origine ? "Venait de " + esc(e.origine) : "Origine inconnue"} · encore ${jours(e.supprime)} j</span></span></span>
          <span class="gris col-l">${octets(e.taille)}</span>
          <span class="gris col-l">${quand(new Date(e.supprime * 1000).toISOString())}</span>
          <button class="btn-menu" data-i="${i}" aria-label="Actions pour ${esc(e.nom)}">•••</button>
        </div>`).join("") : `<div class="vide">La corbeille est vide.</div>`}
    </section>
    <p class="note">Restaurer remet l'élément à sa place. Effacer pour de bon demande le mot de passe du serveur.</p>`;
  const recharger = () => pages.corbeille().catch(montrerErreur);
  const vider = $("#vider");
  if (vider) vider.onclick = async () => {
    if (!(await demander({ titre: "Vider la corbeille ?", texte: `Les ${el.length} éléments seront effacés pour de bon. Impossible de revenir en arrière.`, valider: "Tout effacer", danger: true }))) return;
    try { await api("/api/corbeille/effacer", { method: "POST", json: { tout: true } }); toast("Corbeille vidée"); } catch (e) { toast(e.message, true); }
    recharger();
  };
  contenu.querySelectorAll(".btn-menu").forEach((b) => b.addEventListener("click", async () => {
    const e = el[+b.dataset.i];
    const choix = [];
    if (e.origine) choix.push({ texte: "Restaurer", f: async () => {
      try { const r = await api("/api/corbeille/restaurer", { method: "POST", json: { id: e.id, source: e.source, origine: e.origine } }); toast(`Restauré dans ${r.chemin}`); }
      catch (er) { toast(er.message, true); }
      recharger();
    } });
    choix.push({ texte: "Effacer pour de bon", danger: true, f: async () => {
      if (!(await demander({ titre: `Effacer « ${e.nom} » pour de bon ?`, texte: "Impossible de revenir en arrière.", valider: "Effacer", danger: true }))) return;
      try { await api("/api/corbeille/effacer", { method: "POST", json: { id: e.id, source: e.source, nom: e.nom } }); toast("Effacé"); }
      catch (er) { toast(er.message, true); }
      recharger();
    } });
    const c2 = await menu(e.nom, choix);
    if (c2) setTimeout(c2.f, 50);
  }));
};

// ------------------------------------------------------------ utilisateurs
const RANG = { super: "Super admin", admin: "Admin" };
const ilYa = (t) => (t ? quand(new Date(t * 1000).toISOString()) : "Jamais");

pages.utilisateurs = async () => {
  contenu.innerHTML = `<div class="entete"><div><h1>Utilisateurs</h1></div></div>${chargement(4)}`;
  const d = await api("/api/comptes");
  const superAdmin = d.moi.rang === "super";
  const parLogin = Object.fromEntries(d.comptes.filter((c) => c.tailscale).map((c) => [c.tailscale, c]));
  contenu.innerHTML = `
    <div class="entete"><div><h1>Utilisateurs</h1><p class="sous-titre">Les admins de l'appli, et qui se connecte. Tu es <strong>${esc(d.moi.identifiant)}</strong> · ${RANG[d.moi.rang]}.</p></div>
      <div class="actions"><a class="btn" href="#/nuage">Comptes Nextcloud</a><button class="btn btn-p" id="ajouter">Ajouter un admin</button></div></div>
    ${d.demandes.map((x) => `<div class="bandeau demande"><span><strong>${esc(x.nom)}</strong> (${esc(x.identifiant)}) a oublié son mot de passe · demandé par ${esc(x.demandeur)}, ${esc(ilYa(x.t))}.${x.autre_personne ? " <strong>Attention : demandé depuis le compte Tailscale de quelqu'un d'autre.</strong> Le mot de passe provisoire n'ira qu'à son compte à lui." : ""}</span>
      <span class="actions"><button class="btn btn-petit btn-p" data-accepter="${esc(x.id)}">Accepter</button><button class="btn btn-petit" data-refuser="${esc(x.id)}">Refuser</button></span></div>`).join("")}
    ${!superAdmin && d.demandes_n ? `<div class="bandeau"><span>${d.demandes_n} demande${d.demandes_n > 1 ? "s" : ""} de mot de passe en attente : seul un super admin peut y répondre.</span></div>` : ""}
    <section class="carte tableau" aria-label="Comptes admin">
      <div class="ligne titres"><span>Compte</span><span class="col-l">Rang</span><span class="col-l">Dernière connexion</span><span></span></div>
      ${d.comptes.map((c, i) => `
        <div class="ligne">
          <span class="nom"><span class="avatar" aria-hidden="true">${esc((c.nom || c.identifiant)[0].toUpperCase())}</span>
            <span>${esc(c.nom)}<span class="detail-chemin">${esc(c.identifiant)}${c.systeme ? " · mot de passe du serveur" : c.tailscale ? " · Tailscale : " + esc(c.tailscale) : " · sans compte Tailscale"}${c.provisoire ? " · mot de passe provisoire" : ""}${c.rang === "super" && !c.systeme && c.terminal === false ? " · sans terminal" : ""}</span>
            <span class="mobile-detail">${RANG[c.rang]}</span></span></span>
          <span class="col-l"><span class="pastille ${c.rang === "super" ? "p-alerte" : "p-gris"}">${RANG[c.rang]}</span></span>
          <span class="gris col-l">${c.systeme ? "—" : esc(ilYa(c.derniere_connexion))}</span>
          ${c.systeme ? "<span></span>" : `<button class="btn-menu" data-i="${i}" aria-label="Actions pour ${esc(c.nom)}">•••</button>`}
        </div>`).join("")}
    </section>
    <section class="carte pad">
      <label class="ligne-reglage"><span><strong>Espace ouvert à tous les admins</strong><br><span class="note">${d.reglages.espace_admins ? "Tous les admins peuvent ajouter des comptes. Seul un super admin peut nommer ou retirer un super admin." : "Seul un super admin peut ouvrir cet espace."}${superAdmin ? "" : " Réglé par le super admin."}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${d.reglages.espace_admins}" id="espace"${superAdmin ? "" : " disabled"}><span></span></button></label>
    </section>
    <section class="carte pad">
      <h2>Qui se connecte</h2>
      <p class="note" style="margin:6px 0 10px">Rempli tout seul par Tailscale : chaque personne qui ouvre l'appli, depuis quel appareil.</p>
      <div class="journal">${d.visites.map((v) => `<div class="evenement">
          <span class="famille">${d.proprietaires.includes(v.login) ? "Propriétaire" : parLogin[v.login] ? RANG[parLogin[v.login].rang] : "Accès"}</span>
          <span class="texte-ev">${esc(v.nom)} <span class="note">· ${esc(v.login)}${v.appareil ? " · " + esc(v.appareil) : ""} · ${v.n || 1} visite${(v.n || 1) > 1 ? "s" : ""}</span></span>
          <span class="note quand-ev">${esc(ilYa(v.derniere))}</span></div>`).join("") || '<p class="note">Personne pour l\'instant.</p>'}</div>
      ${d.refus.length ? `<h2 style="margin-top:16px">Accès refusés</h2><div class="journal">${d.refus.map((r) => `<div class="evenement f-admin"><span class="famille">Refusé</span>
          <span class="texte-ev">${esc(r.login)}${r.appareil ? ` <span class="note">· ${esc(r.appareil)}</span>` : ""}</span><span class="note quand-ev">${esc(ilYa(r.t))}</span></div>`).join("")}</div>` : ""}
      <p class="note" style="margin-top:10px">Pour donner l'accès à quelqu'un : ajoute-le comme admin avec son compte Tailscale. Les propriétaires sont ceux du fichier de réglages du serveur.</p>
    </section>
    <section class="carte pad"><h2>Appareils connectés</h2>
      <p class="note" style="margin:6px 0 10px">Chaque appareil reste connecté 30 jours. Déconnecte celui que tu as perdu ou prêté.${superAdmin ? "" : " Tu vois tes propres appareils."}</p>
      <div class="journal" id="appareils"><div class="squelette"></div></div></section>`;
  api("/api/sessions").then((ss) => {
    const z = $("#appareils");
    if (!z) return;
    z.innerHTML = ss.map((x) => `<div class="evenement"><span class="famille">${esc(x.identifiant)}</span>
      <span class="texte-ev">${esc(x.appareil || "Appareil inconnu")} <span class="note">· connecté ${esc(ilYa(x.cree))} · vu ${esc(ilYa(x.vu))}</span></span>
      <span>${x.actuelle ? '<span class="pastille p-ok">Cet appareil</span>' : `<button class="btn btn-petit" data-session="${esc(x.id)}">Déconnecter</button>`}</span></div>`).join("") || '<p class="note">Aucun.</p>';
    z.querySelectorAll("[data-session]").forEach((b) => b.addEventListener("click", async () => {
      try { await api(`/api/sessions/${encodeURIComponent(b.dataset.session)}/fermer`, { method: "POST" }); toast("Appareil déconnecté"); } catch (e) { toast(e.message, true); }
      pages.utilisateurs().catch(montrerErreur);
    }));
  }).catch(() => {});

  const recharger = () => pages.utilisateurs().catch(montrerErreur);
  contenu.querySelectorAll("[data-accepter],[data-refuser]").forEach((b) => b.addEventListener("click", async () => {
    const accepter = !!b.dataset.accepter;
    try { await api(`/api/comptes-demandes/${encodeURIComponent(b.dataset.accepter || b.dataset.refuser)}`, { method: "POST", json: { accepter } }); toast(accepter ? "Acceptée : le mot de passe provisoire s'affichera sur son appareil" : "Demande refusée"); }
    catch (e) { toast(e.message, true); }
    recharger();
  }));
  const espace = $("#espace");
  if (espace && superAdmin) espace.onclick = async () => {
    const ouvert = espace.getAttribute("aria-checked") !== "true";
    try { await api("/api/comptes-reglages", { method: "POST", json: { espace_admins: ouvert } }); } catch (e) { toast(e.message, true); }
    recharger();
  };
  $("#ajouter").onclick = async () => {
    const v = await formulaire({
      titre: "Ajouter un admin",
      texte: "Un mot de passe provisoire est créé. Avec son compte Tailscale, il s'affiche sur l'appli de la personne ; sinon, ici, pour que tu le lui donnes.",
      champs: [
        { nom: "nom", label: "Nom affiché", indice: "Camille" },
        { nom: "identifiant", label: "Identifiant de connexion", indice: "camille" },
        { nom: "tailscale", label: "Compte Tailscale (facultatif)", indice: "adresse avec laquelle elle se connecte à Tailscale" },
        ...(superAdmin ? [{ nom: "rang", label: "Rang", options: [["admin", "Admin"], ["super", "Super admin"]], valeur: "admin" }] : []),
      ],
      valider: "Ajouter",
    });
    if (!v || v === "secondaire") return;
    try {
      const r = await api("/api/comptes", { method: "POST", json: { nom: v.nom, identifiant: v.identifiant, tailscale: v.tailscale, rang: v.rang || "admin" } });
      if (r.mot_de_passe) await montrerProvisoire(r.compte, r.mot_de_passe);
      else toast(`${r.compte.nom} ajouté : son mot de passe provisoire s'affichera sur son appli`);
    } catch (e) { toast(e.message, true); }
    recharger();
  };
  contenu.querySelectorAll(".btn-menu").forEach((b) => b.addEventListener("click", async () => {
    const c = d.comptes[+b.dataset.i];
    const action = async (corps, ok) => { try { await api(`/api/comptes/${encodeURIComponent(c.id)}`, { method: "POST", json: corps }); toast(ok); } catch (e) { toast(e.message, true); } recharger(); };
    const choix = [
      { texte: "Renommer", f: async () => { const nom = await demander({ titre: "Nom affiché", champ: c.nom, valider: "Renommer" }); if (nom) action({ action: "nom", nom }, "Renommé"); } },
      { texte: c.tailscale ? "Changer le compte Tailscale" : "Relier un compte Tailscale", f: async () => {
        const t = await demander({ titre: "Compte Tailscale", texte: "L'adresse avec laquelle la personne se connecte à Tailscale. Vide = plus d'accès à l'appli par ce compte.", champ: c.tailscale || "", valider: "Enregistrer" });
        if (t !== null) action({ action: "tailscale", tailscale: t }, "Enregistré");
      } },
    ];
    if (superAdmin) choix.push({ texte: c.rang === "super" ? "Passer admin" : "Nommer super admin", f: () => action({ action: "rang", rang: c.rang === "super" ? "admin" : "super" }, "Rang changé") });
    if (superAdmin && c.rang === "super") choix.push({ texte: c.terminal === false ? "Autoriser le terminal" : "Retirer le terminal",
      f: () => action({ action: "terminal", terminal: c.terminal === false }, c.terminal === false ? "Terminal autorisé" : "Terminal retiré") });
    choix.push({ texte: "Retirer ce compte", danger: true, f: async () => {
      if (await demander({ titre: `Retirer ${c.nom} ?`, texte: "Il ne pourra plus faire d'action admin" + (c.tailscale ? " ni ouvrir l'appli" : "") + ".", valider: "Retirer", danger: true })) action({ action: "supprimer" }, "Compte retiré");
    } });
    const x = await menu(`${c.nom} · ${RANG[c.rang]}`, choix);
    if (x) setTimeout(x.f, 50);
  }));
};

async function montrerProvisoire(compte, mdp) {
  await new Promise((resoudre) => {
    dform.innerHTML = `<h2>Mot de passe provisoire</h2>
      <p>Donne-le à <strong>${esc(compte.nom)}</strong> (identifiant <strong>${esc(compte.identifiant)}</strong>). Il ne sera plus affiché, il sert une fois dans les 24 h, puis la personne choisit le sien.</p>
      <p class="code-secours">${esc(mdp)}</p>
      <div class="actions"><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogue.onclose = () => resoudre();
    dialogue.showModal();
  });
}

// ------------------------------------------------------------ journal
const FAMILLES = { outil: "Outil", admin: "Admin", disque: "Disque", courant: "Courant", fichier: "Fichier", appli: "Appli", alerte: "Alerte", sauvegardes: "Sauvegardes", nuage: "Nextcloud", systeme: "Système" };
const ligneJournal = (ev) => `<div class="evenement f-${esc(ev.famille)}">
    <span class="famille">${FAMILLES[ev.famille] || "Système"}</span>
    <span class="texte-ev">${esc(ev.texte)}${ev.qui ? ` <span class="note">· ${esc(ev.qui)}</span>` : ""}</span>
    <span class="note quand-ev">${quand(new Date(ev.t * 1000).toISOString())}</span></div>`;

pages.journal = async (params) => {
  const famille = params.get("famille") || "";
  contenu.innerHTML = `<div class="entete"><div><h1>Journal</h1></div></div>${chargement(5)}`;
  const evs = await api("/api/journal?n=300" + (famille ? "&famille=" + encodeURIComponent(famille) : ""));
  contenu.innerHTML = `
    <div class="entete"><div><h1>Journal</h1><p class="sous-titre">Ce qui s'est passé sur le serveur</p></div>
      <div class="actions"><a class="btn" href="#/systeme">← Système</a></div></div>
    <div class="filtres">${[["", "Tout"], ...Object.entries(FAMILLES)].map(([k, v]) => `<a class="filtre${k === famille ? " actif" : ""}" href="#/journal${k ? "?famille=" + k : ""}">${v}</a>`).join("")}</div>
    <section class="carte pad journal">${evs.length ? evs.map(ligneJournal).join("") : '<p class="note">Rien pour l\'instant.</p>'}</section>`;
};

// ------------------------------------------------------------ alertes (notifications)
function cleEnOctets(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}
const pushPossible = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const surIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

async function abonnementActuel() {
  if (!pushPossible()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

async function activerAlertes(cle) {
  if (await Notification.requestPermission() !== "granted") throw new Error("Alertes refusées. Autorise-les dans les réglages de l'appareil (Notifications → Jabba).");
  const reg = await navigator.serviceWorker.ready;
  let ab = await reg.pushManager.getSubscription();
  if (ab) await ab.unsubscribe();
  ab = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: cleEnOctets(cle) });
  const appareil = surIOS() ? "iPhone ou iPad" : /Mac/.test(navigator.userAgent) ? "Mac" : navigator.platform || "Appareil";
  await api("/api/alertes/abonnement", { method: "POST", json: { abonnement: ab.toJSON(), appareil } });
}

async function carteAlertes() {
  const z = $("#alertes");
  if (!z) return;
  const a = await api("/api/alertes");
  let ici = false, raison = "";
  if (!pushPossible()) raison = surIOS()
    ? "Sur iPhone, ajoute d'abord l'appli à l'écran d'accueil (bouton Partager → « Sur l'écran d'accueil »), puis ouvre-la depuis son icône et reviens ici."
    : "Ce navigateur ne sait pas recevoir d'alertes. Utilise Safari (Mac, iPhone) ou Chrome.";
  else {
    const ab = await abonnementActuel();
    if (ab) ici = (await api("/api/alertes/abonnement/etat", { method: "POST", json: { endpoint: ab.endpoint } })).abonne;
  }
  z.innerHTML = `<h2>Alertes</h2>
    <p class="note" style="margin:8px 0 12px">${a.appareils ? `${a.appareils} appareil${a.appareils > 1 ? "s reçoivent" : " reçoit"} les alertes.` : "Aucun appareil ne reçoit les alertes."} ${ici ? "<strong>Celui-ci en fait partie.</strong>" : ""}</p>
    ${raison ? `<div class="bandeau" style="margin-bottom:12px"><span>${esc(raison)}</span></div>` : ""}
    <div class="actions" style="margin-bottom:14px">
      ${raison ? "" : ici ? `<button class="btn" id="alertes-off">Ne plus les recevoir ici</button>` : `<button class="btn btn-p" id="alertes-on">Recevoir les alertes sur cet appareil</button>`}
      ${a.appareils ? `<button class="btn" id="alertes-essai">Envoyer une alerte d'essai</button>` : ""}
    </div>
    <div class="types-alertes">${a.types.map((t) => `<label class="ligne-reglage"><span><strong>${esc(t.nom)}</strong><br><span class="note">${esc(t.detail)}</span></span>
      <button class="interrupteur" role="switch" aria-checked="${t.actif}" data-type="${esc(t.id)}" aria-label="${esc(t.nom)}"><span></span></button></label>`).join("")}</div>
    <p class="note">Ces réglages valent pour tous tes appareils. Tout est aussi noté dans le journal, même ce qui ne t'est pas envoyé.</p>`;
  const on = $("#alertes-on"), off = $("#alertes-off"), essai = $("#alertes-essai");
  if (on) on.onclick = async () => { on.disabled = true; try { await activerAlertes(a.cle); toast("Alertes activées sur cet appareil"); } catch (e) { toast(e.message, true); } carteAlertes().catch(() => {}); };
  if (off) off.onclick = async () => {
    try { const ab = await abonnementActuel(); if (ab) { await api("/api/alertes/abonnement/retirer", { method: "POST", json: { endpoint: ab.endpoint } }); await ab.unsubscribe(); } toast("Cet appareil ne recevra plus d'alertes"); }
    catch (e) { toast(e.message, true); }
    carteAlertes().catch(() => {});
  };
  if (essai) essai.onclick = async () => { try { await api("/api/alertes/essai", { method: "POST" }); toast("Alerte d'essai envoyée : elle arrive dans quelques secondes"); } catch (e) { toast(e.message, true); } };
  z.querySelectorAll("[data-type]").forEach((b) => b.addEventListener("click", async () => {
    const actif = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(actif));
    try { await api("/api/alertes/types", { method: "POST", json: { type: b.dataset.type, actif } }); }
    catch (e) { toast(e.message, true); b.setAttribute("aria-checked", String(!actif)); }
  }));
}

// ------------------------------------------------------------ mise à jour de l'appli
async function carteMaj(verifier = false) {
  const z = $("#maj");
  if (!z) return;
  const m = await api("/api/appli/maj" + (verifier ? "?verifier=1" : ""));
  let corps;
  if (!m.depot) corps = `<p class="note">Pas encore reliée à GitHub : les mises à jour se feront ici en un clic une fois le dépôt réglé.</p>`;
  else if (!m.pret) corps = `<p class="note">Le dépôt est réglé (${esc(m.depot)}), mais le dossier de l'appli sur le serveur n'est pas encore relié à GitHub.</p>`;
  else if (m.disponible) corps = `<div class="bandeau" style="margin-bottom:10px"><span><strong>Version ${esc(m.derniere.version)} disponible</strong> (tu as la ${esc(m.actuelle)}).</span></div>
      ${m.derniere.notes ? `<pre class="notes-version">${esc(m.derniere.notes)}</pre>` : ""}
      <div class="actions"><button class="btn btn-p" id="installer">Installer la ${esc(m.derniere.version)}</button></div>`;
  else corps = `<p class="note" style="margin-bottom:10px">À jour : version ${esc(m.actuelle)}${m.derniere ? "" : " (GitHub n'a pas répondu)"}.</p><div class="actions"><button class="btn" id="verifier">Vérifier maintenant</button></div>`;
  z.innerHTML = `<h2>Appli Jabba</h2><div style="margin-top:8px">${corps}</div>
    <details class="historique"><summary>Historique des versions</summary><div id="versions"><div class="squelette"></div></div></details>`;
  const det = z.querySelector(".historique");
  det.addEventListener("toggle", async () => {
    if (!det.open || det.dataset.charge) return;
    det.dataset.charge = "1";
    try {
      const vs = await api("/api/appli/versions");
      $("#versions").innerHTML = vs.map((v) => `<div class="version">
        <div class="entete-version"><strong>Version ${esc(v.version)}</strong>${v.version === m.actuelle ? ' <span class="pastille p-ok">Installée</span>' : ""}<span class="note">${esc(new Date(v.date + "T12:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" }))}</span></div>
        <ul>${v.changements.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></div>`).join("") || '<p class="note">Pas d\'historique.</p>';
    } catch (e) { $("#versions").innerHTML = `<p class="note">${esc(e.message)}</p>`; }
  });
  const v = $("#verifier"), inst = $("#installer");
  if (v) v.onclick = () => { v.disabled = true; carteMaj(true).catch((e) => toast(e.message, true)); };
  if (inst) inst.onclick = async () => {
    if (!(await demander({ titre: `Installer la version ${m.derniere.version} ?`, texte: "L'appli se coupe 1 à 3 minutes pendant l'installation, puis se recharge toute seule. Les outils, tes fichiers et tes réglages ne sont pas touchés.", valider: "Installer" }))) return;
    try {
      await api("/api/appli/maj", { method: "POST" });
      const debut = Date.now();
      z.innerHTML = `<h2>Appli Jabba</h2>
        <p style="margin:8px 0 6px"><strong>Installation de la ${esc(m.derniere.version)}</strong></p>
        <div class="barre-maj" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="3"><span style="width:3%"></span></div>
        <p class="note" id="maj-etape">Préparation…</p>`;
      let pct = 3;
      const afficher = (t, p = null) => {
        // jamais de recul ; entre deux étapes, la barre avance doucement d'elle-même
        pct = Math.min(97, Math.max(pct + 0.4, p ?? 0));
        const b = z.querySelector(".barre-maj");
        if (!b) return;
        b.setAttribute("aria-valuenow", String(Math.round(pct)));
        b.firstElementChild.style.width = pct + "%";
        $("#maj-etape").textContent = t;
      };
      while (Date.now() - debut < 20 * 60000) {
        await new Promise((r) => setTimeout(r, 3000));
        try {
          const x = await api("/api/moi");
          if (x.version === m.derniere.version) { afficher("Installée ! Rechargement…", 100); pct = 100; z.querySelector(".barre-maj span").style.width = "100%"; setTimeout(() => location.reload(), 800); return; }
          const e = await api("/api/appli/maj/etat");
          if (e.statut === "echec") {
            z.innerHTML = `<h2>Appli Jabba</h2>
              <div class="erreur" style="margin-top:8px"><strong>La version ${esc(e.version)} ne s'est pas installée.</strong> Ta version actuelle continue de fonctionner normalement.</div>
              ${e.detail && e.detail.length ? `<pre class="notes-version">${esc(e.detail.join("\n"))}</pre>` : ""}
              <div class="actions"><button class="btn btn-p" id="reessayer-maj">Réessayer</button></div>`;
            $("#reessayer-maj").onclick = () => carteMaj(true).catch((er) => toast(er.message, true));
            return;
          }
          const min = Math.floor((Date.now() - debut) / 60000);
          afficher(`${e.etape || "Installation en cours"}${min ? ` · ${min} min` : ""} — jusqu'à 10 minutes sur un petit serveur. Ne ferme pas cette page.`, e.pourcent);
        } catch (_) { afficher("Redémarrage de l'appli…", 94); }
      }
      toast("L'installation prend plus de temps que prévu. Recharge la page dans quelques minutes.", true);
    } catch (e) { toast(e.message, true); }
  };
}

// ------------------------------------------------------------ mode invité (sans connexion admin)
const urlInvite = (d, chemin, voir = 0) => `/api/invite/fichier?d=${encodeURIComponent(d)}&chemin=${encodeURIComponent(chemin)}${voir ? "&voir=1" : ""}`;

pages.invite = async (params) => {
  const e = await api("/api/invite/etat");
  const titre = moi && moi.serveur ? moi.serveur[0].toUpperCase() + moi.serveur.slice(1) : "Jabba";
  const piedAdmin = e.lan
    ? `<p class="note centre">Administration : depuis l'appli Jabba, par Tailscale.</p>`
    : `<div class="centre"><button class="btn btn-p" id="connexion">Connexion admin</button></div>`;
  if (!e.actif) {
    contenu.innerHTML = `<div class="invite-accueil"><span class="logo grand">J</span><h1>${esc(titre)}</h1><p class="sous-titre">L'accès invités est fermé pour le moment.</p></div>${piedAdmin}`;
  } else if (!e.entre) {
    contenu.innerHTML = `<div class="invite-accueil"><span class="logo grand">J</span><h1>Bienvenue chez ${esc(titre)}</h1>
        <p class="sous-titre">Tape le code d'accès à 4 chiffres (ou scanne le QR code).</p>
        <form id="form-code" class="form-code"><input name="code" inputmode="numeric" pattern="[0-9]*" maxlength="4" autocomplete="one-time-code" aria-label="Code d'accès" placeholder="••••"><button class="btn btn-p" type="submit">Entrer</button></form></div>${piedAdmin}`;
    $("#form-code").onsubmit = async (ev) => {
      ev.preventDefault();
      try { await api("/api/invite/entrer", { method: "POST", json: { code: ev.target.code.value } }); router(); }
      catch (er) { toast(er.message, true); }
    };
    $("#form-code").code.focus();
  } else if (params.get("d")) {
    return pageDossierInvite(params.get("d"), params.get("chemin") || "/");
  } else {
    contenu.innerHTML = `<div class="entete"><div><h1>${esc(titre)}</h1><p class="sous-titre">${e.lan ? "Bienvenue ! Voici ce qui est partagé avec toi." : "Mode invité : ce que voient les invités. Connecte-toi pour tout le reste."}</p></div></div>
      <section aria-label="Dossiers partagés"><h2 class="titre-section">Dossiers</h2>
        <div class="tuiles">${e.dossiers.map((x) => `<a class="carte tuile" href="#/invite?d=${encodeURIComponent(x.id)}"><span class="ic-tuile dossier" aria-hidden="true"></span><span class="nom-tuile">${esc(x.nom)}</span><span class="note">${x.mode === "depot" ? "Lire et déposer" : "Lecture seule"}</span></a>`).join("") || '<p class="note">Aucun dossier partagé.</p>'}</div></section>
      ${e.outils.length ? `<section aria-label="Outils"><h2 class="titre-section">Outils</h2><section class="carte liste-raccourcis">${e.outils.map((o) => `<a href="${esc(o.lien)}" target="_blank" rel="noopener"><span>${esc(o.nom)} <span class="note">· ${esc(o.role)}</span></span><span>↗</span></a>`).join("")}</section></section>` : ""}
      ${piedAdmin}`;
  }
  const c = $("#connexion");
  if (c) c.onclick = () => connexionAdmin();
};

async function pageDossierInvite(did, chemin) {
  const d = await api(`/api/invite/fichiers?d=${encodeURIComponent(did)}&chemin=${encodeURIComponent(chemin)}`);
  const lien = (c) => `#/invite?d=${encodeURIComponent(did)}&chemin=${encodeURIComponent(c)}`;
  const morceaux = chemin.split("/").filter(Boolean);
  const fil = [`<a href="#/invite">Accueil</a>`, morceaux.length ? `<a href="${lien("/")}">${esc(d.dossier.nom)}</a>` : `<strong>${esc(d.dossier.nom)}</strong>`]
    .concat(morceaux.map((m, i) => i === morceaux.length - 1 ? `<strong>${esc(m)}</strong>` : `<a href="${lien("/" + morceaux.slice(0, i + 1).join("/"))}">${esc(m)}</a>`)).join('<span class="sep">/</span>');
  const depot = d.dossier.mode === "depot";
  contenu.innerHTML = `
    <div class="entete"><div><h1>${esc(morceaux.length ? morceaux[morceaux.length - 1] : d.dossier.nom)}${depot ? "" : '<span class="lecture-seule">Lecture seule</span>'}</h1></div>
      ${depot ? `<div class="actions"><button class="btn btn-p" id="envoyer">Déposer des fichiers</button></div>` : ""}</div>
    <nav class="fil" aria-label="Emplacement">${fil}</nav>
    <section class="carte tableau">
      ${d.elements.length ? d.elements.map((e) => `<div class="ligne">
        <span class="nom">${e.dossier ? '<span class="ic-dossier" aria-hidden="true"></span>' : VIGNETTES.includes(ext(e.nom)) ? `<span class="vignette" aria-hidden="true"><img alt="" loading="lazy" src="${urlInvite(did, e.chemin, 1)}" onerror="this.parentNode.className='ic-fichier';this.remove()"></span>` : `<span class="ic-fichier${genre(e.nom) ? " " + genre(e.nom) : ""}" aria-hidden="true"></span>`}
          <span>${e.dossier ? `<a href="${lien(e.chemin)}">${esc(e.nom)}</a>` : `<a href="${urlInvite(did, e.chemin)}">${esc(e.nom)}</a>`}<span class="mobile-detail">${e.dossier ? "" : octets(e.taille)}</span></span></span>
        <span class="gris col-l">${e.dossier ? "" : octets(e.taille)}</span><span class="gris col-l">${quand(e.modifie)}</span>
        ${e.dossier ? "<span></span>" : `<a class="btn btn-petit" href="${urlInvite(did, e.chemin)}" aria-label="Télécharger ${esc(e.nom)}">↓</a>`}</div>`).join("") : '<div class="vide">Ce dossier est vide.</div>'}
      ${depot ? `<div class="depot" id="depot">Glisse des fichiers ici pour les déposer</div>` : ""}
    </section><input type="file" id="choix" multiple hidden>`;
  if (!depot) return;
  const envoyer = async (fichiers) => {
    fichiers = fichiers.filter((f) => f.size || f.type);
    if (!fichiers.length) return;
    const fd = new FormData();
    fichiers.forEach((f) => fd.append("fichiers", f, f.name));
    toast(`Envoi de ${fichiers.length} fichier${fichiers.length > 1 ? "s" : ""}…`);
    try { const r = await api(`/api/invite/envoyer?d=${encodeURIComponent(did)}&chemin=${encodeURIComponent(chemin)}`, { method: "POST", body: fd }); toast(`${r.envoyes.length} fichier${r.envoyes.length > 1 ? "s déposés" : " déposé"}, merci !`); }
    catch (er) { toast(er.message, true); }
    pageDossierInvite(did, chemin).catch(montrerErreur);
  };
  const choix = $("#choix");
  $("#envoyer").onclick = () => choix.click();
  choix.onchange = () => envoyer([...choix.files]);
  const zone = $(".tableau"), dep = $("#depot");
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (ev) => { ev.preventDefault(); dep.classList.add("survol"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (ev) => { ev.preventDefault(); if (t === "drop" || !zone.contains(ev.relatedTarget)) dep.classList.remove("survol"); }));
  zone.addEventListener("drop", (ev) => envoyer([...ev.dataTransfer.files]));
}

// ------------------------------------------------------------ invités : réglages (admin)
pages.invites = async () => {
  contenu.innerHTML = `<div class="entete"><div><h1>Invités</h1></div></div>${chargement(4)}`;
  const d = await api("/api/invites");
  contenu.innerHTML = `
    <div class="entete"><div><h1>Invités</h1><p class="sous-titre">Ce que voient les personnes connectées à ton Wi-Fi, sans compte ni application.</p></div></div>
    <section class="carte pad">
      <label class="ligne-reglage"><span><strong>Accès invités</strong><br><span class="note">${d.actif ? "Ouvert : avec le code, un invité sur ton Wi-Fi voit les dossiers ci-dessous." : "Fermé : la page invités n'affiche rien."}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${d.actif}" id="actif"><span></span></button></label>
    </section>
    ${d.actif ? `<section class="carte pad acces-invites">
      <div class="qr"><img src="/api/invites/qr.svg?c=${esc(d.code)}" alt="QR code de l'accès invités" width="180" height="180"></div>
      <div><h2>Code d'accès : <span class="code-invite">${esc(d.code)}</span></h2>
        <p class="note">Les invités scannent le QR code, connectés à ton Wi-Fi principal (pas le Wi-Fi invités de la box, isolé du serveur). Sans téléphone : ouvrir <strong>${esc(d.url ? d.url.split("/?")[0] : d.nom_local)}</strong> ou <strong>${esc(d.nom_local)}</strong>, puis taper le code.</p>
        <p class="note">Un appareil reste reconnu 7 jours. Changer le code fait sortir tout le monde.</p>
        <div class="actions"><button class="btn" id="code">Nouveau code</button><button class="btn" id="imprimer">Imprimer</button></div></div>
    </section>` : ""}
    <h2 class="titre-section">Dossiers partagés</h2>
    <section class="carte tableau">
      ${d.dossiers.map((x) => `<div class="ligne"><span class="nom"><span class="ic-dossier" aria-hidden="true"></span><span>${esc(x.nom)}<span class="detail-chemin">${esc(x.chemin)}</span></span></span>
        <span class="col-l"><select data-mode="${esc(x.id)}" aria-label="Droits sur ${esc(x.nom)}"><option value="lecture"${x.mode === "lecture" ? " selected" : ""}>Lecture seule</option><option value="depot"${x.mode === "depot" ? " selected" : ""}>Lire et déposer</option></select></span>
        <span></span><button class="btn-menu" data-retirer="${esc(x.id)}" aria-label="Retirer ${esc(x.nom)}">✕</button></div>`).join("") || '<div class="vide">Aucun dossier partagé.</div>'}
      <div class="depot"><button class="btn" id="ajouter-dossier">Partager un dossier</button></div>
    </section>
    <h2 class="titre-section">Outils sur le Wi-Fi</h2>
    <section class="carte pad">
      <p class="note" style="margin:0 0 12px">Un outil coché est joignable depuis ton Wi-Fi (par les invités, la télé…) et apparaît sur leur page. Décoché, il n'est joignable que par Tailscale. Chaque outil garde son propre mot de passe.</p>
      ${d.outils.map((o) => `<label class="ligne-reglage" style="margin-bottom:12px"><span><strong>${esc(o.nom)}</strong> <span class="note">· port ${o.port}${o.actif ? "" : " · désactivé dans l'établi"}</span><br><span class="note">${esc(o.role)}</span></span>
        <button class="interrupteur" role="switch" aria-checked="${o.wifi}" data-wifi="${esc(o.id)}"><span></span></button></label>`).join("") || '<p class="note">Aucun outil.</p>'}
    </section>`;
  const recharger = () => pages.invites().catch(montrerErreur);
  const envoyer = async (corps, ok) => { try { await api("/api/invites", { method: "POST", json: corps }); if (ok) toast(ok); } catch (e) { toast(e.message, true); } recharger(); };
  $("#actif").onclick = () => envoyer({ action: "actif", actif: !d.actif }, d.actif ? "Accès invités fermé" : "Accès invités ouvert");
  const code = $("#code");
  if (code) code.onclick = async () => { if (await demander({ titre: "Nouveau code ?", texte: "Les invités déjà entrés devront saisir le nouveau code, et l'ancien QR code ne marchera plus.", valider: "Changer le code" })) envoyer({ action: "code" }, "Nouveau code créé"); };
  const imp = $("#imprimer");
  if (imp) imp.onclick = () => {
    const w = window.open("", "_blank");
    if (!w) return toast("Autorise les fenêtres pour imprimer.", true);
    w.document.write(`<title>Accès invités</title><body style="font-family:-apple-system,sans-serif;text-align:center;padding:40px"><h1>Wi-Fi : fichiers partagés</h1><img src="${location.origin}/api/invites/qr.svg" width="320"><p style="font-size:22px">Code : <b>${esc(d.code)}</b></p><p>${esc(d.url ? d.url.split("/?")[0] : d.nom_local)}</p><script>onload=()=>print()<\/script></body>`);
    w.document.close();
  };
  contenu.querySelectorAll("[data-mode]").forEach((sel) => sel.addEventListener("change", () => envoyer({ action: "mode_dossier", id: sel.dataset.mode, mode: sel.value }, "Enregistré")));
  contenu.querySelectorAll("[data-retirer]").forEach((b) => b.addEventListener("click", async () => {
    if (await demander({ titre: "Ne plus partager ce dossier ?", texte: "Les invités ne le verront plus. Son contenu n'est pas touché.", valider: "Retirer" })) envoyer({ action: "retirer_dossier", id: b.dataset.retirer }, "Dossier retiré");
  }));
  contenu.querySelectorAll("[data-wifi]").forEach((b) => b.addEventListener("click", () => envoyer({ action: "wifi", outil: b.dataset.wifi, wifi: b.getAttribute("aria-checked") !== "true" }, "Enregistré")));
  $("#ajouter-dossier").onclick = async () => {
    const chemin = await choisirDossier();
    if (!chemin) return;
    const c = await menu(`Partager « ${chemin.split("/").pop()} »`, [{ texte: "En lecture seule", mode: "lecture" }, { texte: "Lire et déposer", mode: "depot" }]);
    if (c) envoyer({ action: "ajouter_dossier", chemin, mode: c.mode }, "Dossier partagé");
  };
};

// Petite fenêtre pour choisir un dossier du serveur.
function choisirDossier(chemin = "/") {
  return new Promise(async (resoudre) => {
    let d;
    try { d = await api("/api/invites/parcourir?chemin=" + encodeURIComponent(chemin)); } catch (e) { toast(e.message, true); return resoudre(null); }
    const parent = chemin === "/" ? null : chemin.split("/").slice(0, -1).join("/") || "/";
    dform.innerHTML = `<h2>Choisir un dossier</h2><p class="note">${esc(chemin === "/" ? "Dossiers du serveur" : chemin)}</p>
      <div class="liste-dossiers">${parent !== null ? `<button class="btn" type="button" data-aller="${esc(parent)}">‹ Retour</button>` : ""}
      ${d.elements.map((e) => `<button class="btn" type="button" data-aller="${esc(e.chemin)}"><span class="ic-dossier" aria-hidden="true"></span> ${esc(e.nom)}</button>`).join("") || '<p class="note">Pas de sous-dossier.</p>'}</div>
      <div class="actions"><button class="btn" value="non" type="submit">Annuler</button>${chemin !== "/" ? `<button class="btn btn-p" value="oui" type="submit">Choisir ce dossier</button>` : ""}</div>`;
    dialogue.onclose = () => resoudre(dialogue.returnValue === "oui" ? chemin : null);
    dialogue.returnValue = "";
    if (!dialogue.open) dialogue.showModal();
    dform.querySelectorAll("[data-aller]").forEach((b) => b.addEventListener("click", () => {
      dialogue.onclose = null; dialogue.close();
      choisirDossier(b.dataset.aller).then(resoudre);
    }));
  });
}

// ------------------------------------------------------------ serveur injoignable et aide
const memo = {
  lire: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } },
  ecrire: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* rien */ } },
};
let relance = null, dernierEchec = "reseau";

function ecranInjoignable(type) {
  dernierEchec = type;
  const nom = memo.lire("jabba-serveur") || "le serveur";
  const dernier = +memo.lire("jabba-dernier-contact") || 0;
  const horsLigne = navigator.onLine === false;
  const pistes = horsLigne ? [
    ["Cet appareil n'a pas de connexion", "Vérifie le Wi-Fi ou les données mobiles de cet appareil, puis réessaie."],
  ] : type === "appli" ? [
    ["Une mise à jour est peut-être en cours", "Après « Installer », l'appli redémarre : attends 2 minutes, la page se rouvrira toute seule."],
    ["L'appli Jabba s'est arrêtée", `${esc(nom)} est allumé et joignable, mais l'appli ne répond pas. Redémarre le serveur (appui court sur le bouton d'alimentation, puis rallume-le) : l'appli repart toute seule.`],
  ] : [
    ["Tailscale est-il connecté sur cet appareil ?", "Ouvre l'appli Tailscale : elle doit indiquer « Connected ». Sinon, active-la."],
    ["Un VPN est-il actif ?", "NordVPN ou un autre VPN peut bloquer Tailscale et le réseau local. Coupe-le, puis réessaie."],
    [`${esc(nom[0].toUpperCase() + nom.slice(1))} est-il allumé ?`, "Après une coupure de courant, il peut rester éteint : appuie sur son bouton d'alimentation et attends 2 minutes."],
    ["Son câble réseau est-il branché ?", "Vérifie le câble entre le serveur et la Freebox, et que la Freebox fonctionne (Internet marche sur tes autres appareils ?)."],
  ];
  document.body.classList.add("injoignable");
  contenu.innerHTML = `
    <div class="entete"><div><h1>${type === "appli" ? "Jabba ne répond pas" : `${esc(nom[0].toUpperCase() + nom.slice(1))} est injoignable`}</h1>
      <p class="sous-titre">${dernier ? "Dernier contact réussi " + quand(new Date(dernier).toISOString()).toLowerCase() + ". " : ""}Nouvel essai automatique toutes les 10 secondes.</p></div>
      <div class="actions"><a class="btn" href="#/aide">Aide</a><button class="btn btn-p" id="reessayer">Réessayer</button></div></div>
    <section class="carte pad pistes">
      <h2>Pistes, dans l'ordre</h2>
      <ol>${pistes.map(([t, d]) => `<li><strong>${t}</strong><span>${d}</span></li>`).join("")}</ol>
    </section>
    <p class="note">Rien n'est perdu : les fichiers restent sur le serveur. Dès qu'il répond, cette page se recharge toute seule.</p>`;
  const essai = async () => {
    try { const r = await fetch("/api/moi", { credentials: "same-origin", cache: "no-store" }); if (r.ok) location.reload(); }
    catch (_) { /* toujours injoignable */ }
  };
  $("#reessayer").onclick = () => { toast("Nouvel essai…"); essai(); };
  clearInterval(relance);
  relance = setInterval(essai, 10000);
}

const AIDE = [
  ["Jabba ne s'ouvre pas ou dit « injoignable »", [
    "Ouvre l'appli Tailscale sur cet appareil : elle doit indiquer « Connected ».",
    "Coupe tout VPN (NordVPN…) : il peut bloquer Tailscale et le réseau local.",
    "Vérifie que le serveur est allumé (après une coupure de courant, rallume-le) et que son câble réseau va bien à la Freebox.",
    "Essaie depuis un autre appareil : si ça marche ailleurs, le souci vient de cet appareil.",
    "Juste après une mise à jour de l'appli, attends 2 minutes.",
  ]],
  ["Un disque branché n'apparaît pas", [
    "Attends 15 secondes, puis Disques → Actualiser.",
    "Formats lisibles : exFAT, FAT, NTFS, ext4. Un disque Mac (APFS) est signalé comme illisible : reformate-le en exFAT avec l'Utilitaire de disque.",
    "Le disque fait des petits clics et n'apparaît pas : il manque de courant. Branche-le directement sur le serveur, ou sur un hub alimenté (avec son propre bloc secteur) : un hub sans alimentation ne suffit pas pour un disque 2,5\".",
    "Si tu avais cliqué « Oublier ce disque », remets-le en ajout automatique depuis Disques.",
  ]],
  ["Un dossier de disque paraît vide dans Fichiers", [
    "Juste après le branchement, Nextcloud relit le disque : quelques minutes pour un gros disque. Actualise ensuite.",
    "Vérifie dans Disques que le disque est bien affiché avec son espace utilisé.",
  ]],
  ["Mot de passe oublié", [
    "Compte du serveur : « Mot de passe oublié ? » à la connexion, puis le code de secours (6 mots).",
    "Autre compte admin : « Mot de passe oublié ? » envoie une demande au super admin ; une fois acceptée, un mot de passe provisoire s'affiche sur ton appareil.",
  ]],
  ["Les alertes n'arrivent pas", [
    "Sur iPhone, Jabba doit être installée sur l'écran d'accueil (Partager → Sur l'écran d'accueil) pour recevoir des alertes.",
    "Système → Alertes : active les alertes sur cet appareil et vérifie les interrupteurs par type.",
    "Réglages de l'appareil → Notifications → Jabba : elles doivent être autorisées.",
  ]],
  ["Les invités n'arrivent pas sur la page Wi-Fi", [
    "Invités : l'accès doit être ouvert.",
    "L'invité doit être sur le même Wi-Fi que le serveur, sans VPN.",
    "Donne-lui le QR code ; si tu as changé le code, l'ancien ne marche plus.",
  ]],
  ["Jellyfin, Nextcloud ou un autre outil ne s'ouvre pas", [
    "Établi : l'interrupteur de l'outil doit être activé.",
    "Utilise « Redémarrer » sur l'outil, puis attends une minute.",
    "Un outil qui affiche « illisible » ou « arrêté » est aussi signalé dans le Journal (Système).",
  ]],
];

pages.aide = async () => {
  contenu.innerHTML = `
    <div class="entete"><div><h1>Aide</h1><p class="sous-titre">Pistes et solutions aux soucis les plus courants</p></div>
      <div class="actions"><button class="btn" id="retour-aide">← Retour</button></div></div>
    <section class="carte aide">${AIDE.map(([titre, pistes], i) => `
      <details${i === 0 && document.body.classList.contains("injoignable") ? " open" : ""}><summary>${esc(titre)}</summary>
        <ul>${pistes.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></details>`).join("")}
    </section>`;
  $("#retour-aide").onclick = () => (history.length > 1 ? history.back() : (location.hash = "#/accueil"));
};

// ------------------------------------------------------------ terminal du serveur
// xterm.js est livré avec l'appli (static/vendor), chargé seulement sur cette page.
let terminalOuvert = null;   // {ws, term, fin()}
function chargerXterm() {
  if (window.Terminal && window.FitAddon) return Promise.resolve();
  const lien = document.createElement("link");
  lien.rel = "stylesheet"; lien.href = "/static/vendor/xterm.css";
  document.head.appendChild(lien);
  const script = (src) => new Promise((ok, ko) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = ko; document.head.appendChild(s); });
  return script("/static/vendor/xterm.js").then(() => script("/static/vendor/addon-fit.js"));
}

const TOUCHES_TERMINAL = [["Échap", "\x1b"], ["Tab", "\t"], ["Ctrl+C", "\x03"], ["Ctrl+D", "\x04"], ["↑", "\x1b[A"], ["↓", "\x1b[B"], ["←", "\x1b[D"], ["→", "\x1b[C"]];

pages.terminal = async () => {
  if (terminalOuvert) terminalOuvert.fin();
  contenu.innerHTML = `<div class="entete"><div><h1>Terminal</h1><p class="sous-titre">Le serveur, comme en SSH, avec le compte du serveur. <code>sudo</code> redemande son mot de passe. Chaque commande est notée au Journal à ton nom.</p></div>
      <div class="actions"><a class="btn" href="#/systeme">← Système</a></div></div>
    <div class="terminal-zone" id="terminal"><p class="note" style="padding:16px">Ouverture…</p></div>
    <div class="touches-terminal">${TOUCHES_TERMINAL.map(([n], i) => `<button class="btn btn-petit" type="button" data-touche="${i}">${n}</button>`).join("")}</div>`;
  let ticket;
  try {
    await chargerXterm();
    ticket = await api("/api/terminal/ticket", { method: "POST" });
  } catch (e) {
    $("#terminal").innerHTML = `<div class="erreur" style="margin:16px">${esc(e.message)}</div>`;
    return;
  }
  const zone = $("#terminal");
  zone.innerHTML = "";
  const term = new window.Terminal({ cursorBlink: true, fontSize: 14, fontFamily: "ui-monospace, Menlo, Consolas, monospace", scrollback: 5000,
    theme: { background: "#101318", foreground: "#E6E8EC", cursor: "#8FB3FF", selectionBackground: "#2A3A5C" } });
  const fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(zone);
  fit.fit();
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal/ws?ticket=${encodeURIComponent(ticket.ticket)}`);
  ws.binaryType = "arraybuffer";
  const envoyer = (o) => { if (ws.readyState === 1) ws.send(JSON.stringify(o)); };
  const taille = () => { try { fit.fit(); envoyer({ t: "r", l: term.rows, c: term.cols }); } catch (_) { /* page quittée */ } };
  ws.onopen = () => { taille(); term.focus(); };
  ws.onmessage = (ev) => term.write(new Uint8Array(ev.data));
  ws.onclose = () => {
    term.write("\r\n\x1b[90m— Session terminée —\x1b[0m\r\n");
    if (location.hash.startsWith("#/terminal")) {
      const b = document.createElement("div");
      b.className = "actions"; b.style.marginTop = "10px";
      b.innerHTML = '<button class="btn btn-p" type="button">Rouvrir le terminal</button>';
      b.firstChild.onclick = () => pages.terminal().catch(montrerErreur);
      zone.after(b);
    }
  };
  term.onData((d) => envoyer({ t: "i", d }));
  window.addEventListener("resize", taille);
  contenu.querySelectorAll("[data-touche]").forEach((b) => b.addEventListener("click", () => { envoyer({ t: "i", d: TOUCHES_TERMINAL[+b.dataset.touche][1] }); term.focus(); }));
  terminalOuvert = {
    fin: () => { window.removeEventListener("resize", taille); try { ws.close(); } catch (_) { /* déjà fermé */ } term.dispose(); terminalOuvert = null; },
  };
};
window.addEventListener("hashchange", () => { if (terminalOuvert && !location.hash.startsWith("#/terminal")) terminalOuvert.fin(); });

// ------------------------------------------------------------ comptes Nextcloud
const go = (o) => (o == null ? "illimité" : String(Math.round((o / 1073741824) * 10) / 10).replace(".", ",") + " Go");
const nomGroupe = (g, n) => {
  if (g === n.tous) return "Tous les membres";
  const r = (n.rangs || []).find((x) => x.id === g);
  return r ? `${r.nom} et au-dessus` : g;
};
// À qui donner un dossier : tous, puis les rangs (du plus haut au plus bas), puis les groupes.
const ciblesDossier = (n) => [
  ...n.groupes.filter((g) => g.id === n.tous),
  ...(n.rangs_actif ? [...n.rangs].reverse() : []),
  ...n.groupes.filter((g) => g.id !== n.tous),
];
async function choisirRang(n, titre, actuel) {
  const choix = [...n.rangs].reverse().map((r) => ({ texte: r.nom + (r.id === actuel ? " (actuel)" : ""), id: r.id }));
  choix.push({ texte: "Aucun rang" + (actuel ? "" : " (actuel)"), id: null });
  return menu(titre, choix);
}

async function choisirEspace(titre, actuel, n, superAdmin, illimite = true) {
  const tailles = [1, 2, 5, 10, 20, 50, 100, 200].filter((t) => superAdmin || t <= n.plafond_go);
  const choix = tailles.map((t) => ({ texte: `${t} Go${actuel === t * 1073741824 ? " (actuel)" : ""}`, go: t }));
  choix.push({ texte: "Autre…", autre: true });
  if (superAdmin && illimite) choix.push({ texte: "Illimité", go: null });
  const c = await menu(titre + (superAdmin ? "" : ` (plafond : ${n.plafond_go} Go)`), choix);
  if (!c) return undefined;
  if (!c.autre) return c.go;
  const v = await demander({ titre, texte: "En Go (par exemple 7,5).", champ: "" });
  const x = v && parseFloat(v.replace(",", "."));
  return x > 0 ? x : undefined;
}

pages.nuage = async () => {
  contenu.innerHTML = `<div class="entete"><div><h1>Comptes Nextcloud</h1></div></div>${chargement(4)}`;
  const n = await api("/api/nuage");
  const superAdmin = n.rang === "super";
  const recharger = () => pages.nuage().catch(montrerErreur);
  const agir = async (url, json, ok) => {
    try { const r = await api(url, { method: "POST", json }); if (ok) toast(ok); return r; }
    catch (e) { toast(e.message, true); return null; }
  };
  if (!n.etat.present) {
    contenu.innerHTML = `<div class="entete"><div><h1>Comptes Nextcloud</h1></div></div>
      <section class="carte pad"><p>Nextcloud n'est pas allumé. Active-le dans l'<a href="#/outils">Établi</a>, puis reviens ici.</p></section>`;
    return;
  }
  if (!n.etat.pret) {
    contenu.innerHTML = `<div class="entete"><div><h1>Comptes Nextcloud</h1><p class="sous-titre">Pas encore en route.</p></div></div>
      <section class="carte pad"><h2>Mettre en route</h2>
        <p class="note" style="margin:8px 0">Comme Dropbox : chacun peut demander un compte, et un super admin le valide ici. Chaque compte a <strong>5 Go</strong> par défaut, que les admins peuvent changer sans dépasser le plafond. Des <strong>dossiers communs</strong> peuvent être partagés avec tous les membres ou avec un groupe.</p>
        <p class="note" style="margin:8px 0 14px">Jabba installe deux applis officielles de Nextcloud (inscription, dossiers communs) et pose les réglages. Les comptes déjà créés ne changent pas.</p>
        ${superAdmin ? '<div class="actions"><button class="btn btn-p" id="nuage-route">Mettre en route</button></div>' : '<p class="note">Seul un super admin peut le faire.</p>'}
      </section>`;
    const b = $("#nuage-route");
    if (b) b.onclick = async () => {
      b.disabled = true; b.textContent = "Installation… (1 à 2 minutes)";
      await agir("/api/nuage/mettre-en-route", {}, "Comptes Nextcloud en route");
      recharger();
    };
    return;
  }
  const attente = n.comptes.filter((c) => c.etat === "attente");
  const autres = n.comptes.filter((c) => c.etat !== "attente");
  const ligneCompte = (c) => {
    const p = c.quota && c.utilise != null ? Math.min(100, Math.round((100 * c.utilise) / c.quota)) : 0;
    return `<button class="nuage-compte" data-compte="${esc(c.id)}">
      <span class="nuage-qui"><strong>${esc(c.nom)}</strong> <span class="note">${esc(c.id)}${c.email ? " · " + esc(c.email) : ""}</span>
        <span class="nuage-badges">${c.rang_nuage ? `<span class="pastille p-ok">${esc((n.rangs.find((r) => r.id === c.rang_nuage) || {}).nom || "")}</span>` : ""}${c.etat === "suspendu" ? '<span class="pastille p-rouge">Suspendu</span>' : ""}${c.admin ? '<span class="pastille p-gris">Admin Nextcloud</span>' : ""}${c.groupes.filter((g) => g !== n.tous && g !== "admin" && !g.startsWith("rang-")).map((g) => `<span class="pastille p-gris">${esc(g)}</span>`).join("")}</span></span>
      <span class="nuage-espace"><span>${c.utilise != null ? octets(c.utilise) + " / " : ""}${go(c.quota)}</span>${c.quota ? `<span class="jauge"><span style="width:${p}%;${p >= 90 ? "background:var(--rouge)" : ""}"></span></span>` : ""}</span>
    </button>`;
  };
  const acces = (f) => f.acces.map((a) => `${esc(nomGroupe(a.groupe, n))} : ${esc(n.modes[a.mode].nom.toLowerCase())}`).join(" · ") || "Personne";
  contenu.innerHTML = `
    <div class="entete"><div><h1>Comptes Nextcloud</h1><p class="sous-titre">${autres.length} compte${autres.length > 1 ? "s" : ""} · ${go(n.promis)} promis${n.espace ? ` · ${go(n.espace.total - n.espace.utilise)} libres sur le disque de données` : ""}</p></div>
      <div class="actions">${superAdmin ? '<button class="btn btn-p" id="nuage-ajouter">Ajouter un compte</button>' : ""}</div></div>
    ${attente.length ? `<section class="carte pad nuage-attente"><h2>À valider</h2>
      ${attente.map((c) => `<div class="sauv-ligne"><span><strong>${esc(c.nom)}</strong> <span class="note">${esc(c.id)}${c.email ? " · " + esc(c.email) : ""}</span></span>
        ${superAdmin ? `<span class="actions"><button class="btn btn-petit" data-refuser="${esc(c.id)}">Refuser</button><button class="btn btn-p btn-petit" data-valider="${esc(c.id)}">Valider</button></span>` : '<span class="note">Un super admin doit valider</span>'}</div>`).join("")}
    </section>` : ""}
    <section class="carte pad" style="margin-top:16px"><h2>Comptes</h2>
      <div class="sauv-liste">${autres.map(ligneCompte).join("") || '<p class="note">Aucun compte.</p>'}</div></section>
    <div class="grille g2" style="margin-top:16px">
      <section class="carte pad"><div class="entete-carte"><h2>Dossiers communs</h2><button class="btn btn-petit" id="nuage-dossier">Nouveau</button></div>
        <p class="note" style="margin:6px 0 8px">Ils apparaissent dans le Nextcloud de chaque personne qui y a accès.</p>
        <div class="sauv-liste">${n.dossiers.map((f) => `<button class="sauv-ligne" data-dossier="${f.id}"><span><strong>${esc(f.nom)}</strong><br><span class="note">${acces(f)}</span></span><span class="note">${octets(f.taille)}${f.quota ? " / " + go(f.quota) : ""}</span></button>`).join("") || '<p class="note">Aucun pour l\'instant.</p>'}</div></section>
      <section class="carte pad"><div class="entete-carte"><h2>Rangs</h2>${n.createur ? `<button class="interrupteur" role="switch" aria-checked="${n.rangs_actif}" id="rangs-actif" aria-label="Utiliser des rangs"><span></span></button>` : ""}</div>
        <p class="note" style="margin:6px 0 8px">${n.rangs_actif
          ? "Du plus haut au plus bas. Un dossier commun donné à un rang est vu par ce rang et ceux au-dessus, pas par ceux en dessous."
          : "Désactivés. Avec les rangs (par exemple Adhérent, Employé, Gérant), on donne des dossiers communs à un rang et à ceux au-dessus."}${n.createur ? "" : " Seul le compte du serveur règle les rangs."}</p>
        ${n.rangs_actif ? `<div class="sauv-liste">${[...n.rangs].reverse().map((r) => `<button class="sauv-ligne" data-rang="${esc(r.id)}"${n.createur ? "" : " disabled"}><span><strong>${esc(r.nom)}</strong></span><span class="note">${r.membres} personne${r.membres > 1 ? "s" : ""}</span></button>`).join("") || '<p class="note">Aucun rang pour l\'instant.</p>'}</div>
        ${n.createur ? '<div class="actions" style="margin-top:10px"><button class="btn btn-petit" id="rang-nouveau">Nouveau rang</button></div>' : ""}` : ""}
      </section>
      <section class="carte pad"><div class="entete-carte"><h2>Groupes</h2><button class="btn btn-petit" id="nuage-groupe">Nouveau</button></div>
        <p class="note" style="margin:6px 0 8px">Pour donner un dossier commun à quelques personnes (le fablab, la famille…). Chaque compte validé fait partie de « Tous les membres ».</p>
        <div class="sauv-liste">${n.groupes.map((g) => `<button class="sauv-ligne" data-groupe="${esc(g.id)}"><span><strong>${esc(nomGroupe(g.id, n))}</strong></span><span class="note">${g.membres.length} membre${g.membres.length > 1 ? "s" : ""}</span></button>`).join("")}</div></section>
      ${superAdmin ? `<section class="carte pad"><h2>Réglages</h2>
        <label class="ligne-reglage"><span><strong>Espace des nouveaux comptes</strong></span><button class="btn btn-petit" id="nuage-defaut">${esc(n.etat.quota_defaut.replace(" GB", " Go").replace(" MB", " Mo"))}</button></label>
        <label class="ligne-reglage"><span><strong>Plafond par compte</strong><br><span class="note">Un admin ne peut pas donner plus. Un super admin, si.</span></span><button class="btn btn-petit" id="nuage-plafond">${n.plafond_go} Go</button></label>
        <p class="note" style="margin-top:10px">Inscription : lien « S'inscrire » sur la page de connexion de Nextcloud. Tant que Nextcloud n'a pas d'adresse publique, seuls ceux qui ont accès au serveur peuvent l'ouvrir. L'adresse e-mail n'est pas vérifiée (pas encore de serveur d'e-mails).</p>
      </section>` : ""}
    </div>`;

  // à valider
  contenu.querySelectorAll("[data-valider]").forEach((b) => b.addEventListener("click", async () => {
    let rang = null;
    if (n.rangs_actif && n.rangs.length) {
      const r = await choisirRang(n, `Rang de ${b.dataset.valider}`, null);
      if (!r) return;
      rang = r.id;
    }
    if (await agir(`/api/nuage/comptes/${encodeURIComponent(b.dataset.valider)}`, { action: "valider", rang }, "Compte validé")) recharger();
  }));
  contenu.querySelectorAll("[data-refuser]").forEach((b) => b.addEventListener("click", async () => {
    if (!(await demander({ titre: `Refuser ${b.dataset.refuser} ?`, texte: "La demande est supprimée. La personne pourra en refaire une.", valider: "Refuser", danger: true }))) return;
    if (await agir(`/api/nuage/comptes/${encodeURIComponent(b.dataset.refuser)}`, { action: "refuser" }, "Demande refusée")) recharger();
  }));

  // un compte
  contenu.querySelectorAll("[data-compte]").forEach((b) => b.addEventListener("click", async () => {
    const c = n.comptes.find((x) => x.id === b.dataset.compte);
    const url = `/api/nuage/comptes/${encodeURIComponent(c.id)}`;
    const choix = [{ texte: `Changer l'espace (${go(c.quota)})`, f: "espace" }, { texte: "Groupes", f: "groupes" }];
    if (superAdmin && n.rangs_actif && n.rangs.length) choix.unshift({ texte: `Rang (${(n.rangs.find((r) => r.id === c.rang_nuage) || { nom: "aucun" }).nom})`, f: "rang" });
    if (superAdmin) {
      choix.push({ texte: "Nouveau mot de passe", f: "mdp" });
      if (!c.admin) choix.push({ texte: c.etat === "suspendu" ? "Réactiver" : "Suspendre", f: "suspendre" }, { texte: "Supprimer le compte", f: "supprimer", danger: true });
    }
    const ch = await menu(`${c.nom} (${c.id})`, choix);
    if (!ch) return;
    if (ch.f === "rang") {
      const r = await choisirRang(n, `Rang de ${c.nom}`, c.rang_nuage);
      if (r && r.id !== c.rang_nuage && await agir(url, { action: "rang", rang: r.id }, `${c.nom} : ${r.id ? r.texte.replace(" (actuel)", "") : "aucun rang"}`)) recharger();
    } else if (ch.f === "espace") {
      const v = await choisirEspace(`Espace de ${c.nom}`, c.quota, n, superAdmin);
      if (v !== undefined && await agir(url, { action: "quota", go: v }, `Espace de ${c.nom} : ${v == null ? "illimité" : String(v).replace(".", ",") + " Go"}`)) recharger();
    } else if (ch.f === "groupes") {
      const g = await menu(`Groupes de ${c.nom}`, n.groupes.filter((x) => x.id !== n.tous).map((x) => ({ texte: (c.groupes.includes(x.id) ? "✓ " : "") + x.id, id: x.id })));
      if (!g) return;
      const dedans = !c.groupes.includes(g.id);
      if (await agir(url, { action: "groupe", groupe: g.id, dedans }, dedans ? `${c.nom} ajouté à ${g.id}` : `${c.nom} retiré de ${g.id}`)) recharger();
    } else if (ch.f === "mdp") {
      if (!(await demander({ titre: `Nouveau mot de passe pour ${c.nom} ?`, texte: "L'ancien ne marchera plus. Les applis Nextcloud de la personne devront se reconnecter.", valider: "Changer" }))) return;
      const r = await agir(url, { action: "mdp" });
      if (r) await montrerMdpNuage(c, r.mdp);
    } else if (ch.f === "suspendre") {
      const actif = c.etat === "suspendu";
      if (!actif && !(await demander({ titre: `Suspendre ${c.nom} ?`, texte: "La personne ne peut plus se connecter. Ses fichiers sont gardés.", valider: "Suspendre", danger: true }))) return;
      if (await agir(url, { action: "suspendre", actif }, actif ? "Compte réactivé" : "Compte suspendu")) recharger();
    } else if (ch.f === "supprimer") {
      const v = await demander({ titre: `Supprimer ${c.nom} ?`, texte: `Le compte et TOUS ses fichiers sont effacés. Pour confirmer, tape ${c.id}.`, champ: "", valider: "Supprimer", danger: true });
      if (v !== c.id) { if (v !== null) toast("Pas confirmé : rien n'a été supprimé.", true); return; }
      if (await agir(url, { action: "supprimer" }, "Compte supprimé")) recharger();
    }
  }));

  // ajouter
  const aj = $("#nuage-ajouter");
  if (aj) aj.onclick = async () => {
    const v = await formulaire({ titre: "Ajouter un compte", texte: "Pour quelqu'un qui ne passe pas par l'inscription. Un mot de passe provisoire s'affichera.",
      champs: [{ nom: "nom", label: "Prénom et nom" }, { nom: "id", label: "Identifiant (minuscules, sans espace)" }, { nom: "email", label: "E-mail (facultatif)", type: "email" },
        ...(n.rangs_actif && n.rangs.length ? [{ nom: "rang", label: "Rang", valeur: n.rangs[0].id, options: [...[...n.rangs].reverse().map((x) => [x.id, x.nom]), ["", "Aucun rang"]] }] : [])], valider: "Créer" });
    if (!v || v === "secondaire") return;
    const r = await agir("/api/nuage/comptes", { id: v.id.trim().toLowerCase(), nom: v.nom, email: v.email, rang: v.rang || null });
    if (r) { await montrerMdpNuage({ nom: v.nom || v.id, id: v.id.trim().toLowerCase() }, r.mdp); recharger(); }
  };

  // dossiers communs
  const choisirMode = (titre) => menu(titre, Object.entries(n.modes).map(([k, m]) => ({ texte: `${m.nom} : ${m.detail}`, mode: k })));
  $("#nuage-dossier").onclick = async () => {
    const nom = await demander({ titre: "Nouveau dossier commun", texte: "Son nom, tel qu'il apparaîtra dans Nextcloud.", champ: "", valider: "Suivant" });
    if (!nom) return;
    const g = await menu("Pour qui ?", ciblesDossier(n).map((x) => ({ texte: nomGroupe(x.id, n), id: x.id })));
    if (!g) return;
    const m = await choisirMode(`${nomGroupe(g.id, n)} pourra…`);
    if (!m) return;
    if (await agir("/api/nuage/dossiers", { action: "creer", nom, groupe: g.id, mode: m.mode }, `Dossier « ${nom} » créé`)) recharger();
  };
  contenu.querySelectorAll("[data-dossier]").forEach((b) => b.addEventListener("click", async () => {
    const f = n.dossiers.find((x) => x.id === +b.dataset.dossier);
    const ch = await menu(f.nom, [{ texte: "Qui y a accès", f: "acces" }, { texte: `Taille maximale (${f.quota ? go(f.quota) : "aucune"})`, f: "quota" },
      { texte: "Renommer", f: "renommer" }, { texte: "Supprimer le dossier", f: "supprimer", danger: true }]);
    if (!ch) return;
    const corps = { id: f.id };
    if (ch.f === "acces") {
      const g = await menu(`Accès à « ${f.nom} »`, ciblesDossier(n).map((x) => {
        const a = f.acces.find((y) => y.groupe === x.id);
        return { texte: `${nomGroupe(x.id, n)} : ${a ? n.modes[a.mode].nom.toLowerCase() : "pas d'accès"}`, id: x.id, a };
      }));
      if (!g) return;
      const choix = Object.entries(n.modes).map(([k, m]) => ({ texte: `${m.nom} : ${m.detail}`, mode: k }));
      if (g.a) choix.push({ texte: "Pas d'accès", mode: null, danger: true });
      const m = await menu(`${nomGroupe(g.id, n)} pourra…`, choix);
      if (!m) return;
      Object.assign(corps, { action: "acces", groupe: g.id, mode: m.mode });
    } else if (ch.f === "quota") {
      const v = await choisirEspace(`Taille maximale de « ${f.nom} »`, f.quota, { plafond_go: 100000 }, true);
      if (v === undefined) return;
      Object.assign(corps, { action: "quota", go: v });
    } else if (ch.f === "renommer") {
      const nom = await demander({ titre: "Renommer", champ: f.nom, valider: "Renommer" });
      if (!nom || nom === f.nom) return;
      Object.assign(corps, { action: "renommer", nom });
    } else {
      const v = await demander({ titre: `Supprimer « ${f.nom} » ?`, texte: "Le dossier et TOUT son contenu sont effacés pour tout le monde. Pour confirmer, tape SUPPRIMER.", champ: "", valider: "Supprimer", danger: true });
      if (v !== "SUPPRIMER") { if (v !== null) toast("Pas confirmé : rien n'a été supprimé.", true); return; }
      corps.action = "supprimer";
    }
    if (await agir("/api/nuage/dossiers", corps, "C'est fait")) recharger();
  }));

  // rangs (compte du serveur)
  const rangs = (json, ok) => agir("/api/nuage/rangs", json, ok).then((r) => r && recharger());
  const ra = $("#rangs-actif");
  if (ra) ra.onclick = async () => {
    if (n.rangs_actif && !(await demander({ titre: "Désactiver les rangs ?", texte: "Chacun garde son rang, mais les dossiers donnés à un rang ne sont plus visibles jusqu'à la réactivation.", valider: "Désactiver", danger: true }))) return;
    rangs({ action: "activer", actif: !n.rangs_actif }, n.rangs_actif ? "Rangs désactivés" : "Rangs activés");
  };
  const rn = $("#rang-nouveau");
  if (rn) rn.onclick = async () => {
    const nom = await demander({ titre: "Nouveau rang", texte: "Il se place tout en haut ; tu pourras le descendre ensuite.", champ: "", valider: "Créer" });
    if (nom) rangs({ action: "creer", nom }, `Rang « ${nom} » créé`);
  };
  contenu.querySelectorAll("[data-rang]").forEach((b) => b.addEventListener("click", async () => {
    const r = n.rangs.find((x) => x.id === b.dataset.rang), i = n.rangs.indexOf(r);
    const choix = [{ texte: "Renommer", f: "renommer" }];
    if (i < n.rangs.length - 1) choix.push({ texte: "Monter d'un cran", f: "haut" });
    if (i > 0) choix.push({ texte: "Descendre d'un cran", f: "bas" });
    choix.push({ texte: "Supprimer le rang", f: "supprimer", danger: true });
    const ch = await menu(r.nom, choix);
    if (!ch) return;
    if (ch.f === "renommer") {
      const nom = await demander({ titre: "Renommer le rang", champ: r.nom, valider: "Renommer" });
      if (nom && nom !== r.nom) rangs({ action: "renommer", id: r.id, nom }, "Rang renommé");
    } else if (ch.f === "supprimer") {
      if (await demander({ titre: `Supprimer le rang ${r.nom} ?`, texte: `Ses ${r.membres} membre(s) descendent d'un rang. Les dossiers donnés à ce rang ne leur sont plus donnés.`, valider: "Supprimer", danger: true }))
        rangs({ action: "supprimer", id: r.id }, "Rang supprimé");
    } else rangs({ action: "deplacer", id: r.id, sens: ch.f }, "Ordre changé");
  }));

  // groupes
  $("#nuage-groupe").onclick = async () => {
    const nom = await demander({ titre: "Nouveau groupe", texte: "Par exemple : Fablab, Famille…", champ: "", valider: "Créer" });
    if (nom && await agir("/api/nuage/groupes", { nom }, `Groupe « ${nom} » créé`)) recharger();
  };
  contenu.querySelectorAll("[data-groupe]").forEach((b) => b.addEventListener("click", async () => {
    const g = n.groupes.find((x) => x.id === b.dataset.groupe);
    const noms = g.membres.map((id) => (n.comptes.find((c) => c.id === id) || { nom: id }).nom);
    if (g.id === n.tous) return demander({ titre: "Tous les membres", texte: noms.join(", ") || "Personne pour l'instant.", valider: "OK" });
    const ch = await menu(g.id, [{ texte: "Ajouter quelqu'un", f: "ajouter" }, ...(noms.length ? [{ texte: "Retirer quelqu'un", f: "retirer" }] : []), { texte: "Supprimer le groupe", f: "supprimer", danger: true }]);
    if (!ch) return;
    if (ch.f === "supprimer") {
      if (!(await demander({ titre: `Supprimer le groupe ${g.id} ?`, texte: "Les comptes et les fichiers restent. Ses membres perdent l'accès aux dossiers communs donnés à ce groupe.", valider: "Supprimer", danger: true }))) return;
      if (await agir("/api/nuage/groupes", { nom: g.id, supprimer: true }, "Groupe supprimé")) recharger();
      return;
    }
    const dedans = ch.f === "ajouter";
    const liste = n.comptes.filter((c) => c.etat !== "attente" && g.membres.includes(c.id) !== dedans);
    if (!liste.length) return toast(dedans ? "Tout le monde en fait déjà partie." : "Personne à retirer.");
    const c = await menu(dedans ? `Ajouter à ${g.id}` : `Retirer de ${g.id}`, liste.map((x) => ({ texte: `${x.nom} (${x.id})`, id: x.id })));
    if (c && await agir(`/api/nuage/comptes/${encodeURIComponent(c.id)}`, { action: "groupe", groupe: g.id, dedans }, "C'est fait")) recharger();
  }));

  // réglages
  if (!superAdmin) return;
  $("#nuage-defaut").onclick = async () => {
    const v = await choisirEspace("Espace des nouveaux comptes", null, n, false, false);
    if (v != null && await agir("/api/nuage/reglages", { quota_defaut_go: v }, `Nouveaux comptes : ${String(v).replace(".", ",")} Go`)) recharger();
  };
  $("#nuage-plafond").onclick = async () => {
    const v = await demander({ titre: "Plafond par compte", texte: "En Go. Un admin ne pourra pas donner plus à un compte.", champ: String(n.plafond_go), valider: "Changer" });
    const x = v && parseFloat(v.replace(",", "."));
    if (x > 0 && await agir("/api/nuage/reglages", { plafond_go: x }, `Plafond : ${v} Go`)) recharger();
  };
};

async function montrerMdpNuage(c, mdp) {
  await new Promise((resoudre) => {
    dform.innerHTML = `<h2>Mot de passe Nextcloud</h2>
      <p>Donne-le à <strong>${esc(c.nom)}</strong> (identifiant <strong>${esc(c.id)}</strong>). Il ne sera plus affiché. Conseille-lui de le changer dans Nextcloud (Paramètres personnels, Sécurité).</p>
      <p class="code-secours">${esc(mdp)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-mdp">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogue.onclose = () => resoudre();
    dialogue.showModal();
    $("#copier-mdp").onclick = async () => { try { await navigator.clipboard.writeText(mdp); toast("Copié"); } catch (_) { toast("Copie impossible : sélectionne-le à la main.", true); } };
  });
}

// ------------------------------------------------------------ sauvegardes
const heureDe = (t) => new Date(t * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
const jourDe = (t) => new Date(t * 1000).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
const dureeCourte = (s) => (s < 60 ? `${Math.max(1, Math.round(s))} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`);
let suiviSauvegarde = null;

async function montrerPhrase(phrase, premiere) {
  await new Promise((resoudre) => {
    dform.innerHTML = `<h2>${premiere ? "La clé de tes sauvegardes" : "Phrase de passe des sauvegardes"}</h2>
      <p>Tes sauvegardes sont chiffrées avec cette phrase. <strong>Sans elle, personne ne peut les relire, toi non plus</strong>, par exemple si le serveur est perdu et qu'il faut tout récupérer sur un autre ordinateur.</p>
      <p>${premiere ? "Elle ne sera plus affichée d'elle-même : enregistre-la maintenant dans l'app Mots de passe et note-la sur papier, rangée ailleurs que le disque." : "Garde-la dans l'app Mots de passe et sur papier."}</p>
      <p class="code-secours">${esc(phrase)}</p>
      <div class="actions"><button class="btn" type="button" id="copier-phrase">Copier</button><button class="btn btn-p" value="ok" type="submit">C'est noté</button></div>`;
    dialogue.onclose = () => resoudre();
    dialogue.showModal();
    $("#copier-phrase").onclick = async () => { try { await navigator.clipboard.writeText(phrase); toast("Phrase copiée"); } catch (_) { toast("Copie impossible : sélectionne la phrase à la main.", true); } };
  });
}

function etatSauvegarde(s) {
  if (s.en_cours) return `<span class="pastille p-alerte">En cours</span> <span class="note">depuis ${heureDe(s.depuis)}</span>`;
  const r = s.dernier;
  if (!r) return `<span class="pastille p-gris">Pas encore faite</span>`;
  if (!r.ok) return `<span class="pastille p-rouge">Échouée</span> <span class="note">${esc(ilYa(r.fin))}</span><br><span style="color:var(--rouge)">${esc(r.erreur)}</span>`;
  return `<span class="pastille p-ok">Réussie</span> <span class="note">${esc(ilYa(r.fin))} à ${heureDe(r.fin)}, en ${dureeCourte(r.duree)}</span>${r.avert ? `<br><span class="note">${esc(r.avert)}</span>` : ""}`;
}

pages.sauvegardes = async () => {
  if (suiviSauvegarde) { clearTimeout(suiviSauvegarde); suiviSauvegarde = null; }
  contenu.innerHTML = `<div class="entete"><div><h1>Sauvegardes</h1></div></div>${chargement()}`;
  const s = await api("/api/sauvegardes");
  const superAdmin = adminConnecte && adminConnecte.rang === "super";
  const choixDisque = (sel) => `<select id="sauv-disque">${s.disques.map((d) => `<option value="${esc(d.nom)}"${d.nom === sel ? " selected" : ""}>${esc(d.nom)}</option>`).join("")}</select>`;
  const quoi = `<p class="note" style="margin:8px 0">Chaque nuit, une copie chiffrée part sur le disque choisi : tes dossiers (sans les films ni les téléchargements, qui existent ailleurs), les réglages des outils avec leurs bases, et ceux du serveur. Seules les nouveautés prennent de la place. On garde les ${s.garder.jours} derniers jours, ${s.garder.semaines} semaines et ${s.garder.mois} mois.</p>`;
  if (!s.pret) {
    contenu.innerHTML = `<div class="entete"><div><h1>Sauvegardes</h1><p class="sous-titre">Pas encore en route.</p></div></div>
      <section class="carte pad"><h2>Mettre en route</h2>${quoi}
        <p class="note" style="margin:8px 0 14px">La copie est rangée dans un dossier caché du disque (<code>.sauvegardes-jabba</code>), à côté de ce qu'il contient déjà. Un disque en lecture seule n'est ouvert en écriture que le temps de la copie.</p>
        ${s.disques.length ? (superAdmin ? `<label class="note" for="sauv-disque">Disque des sauvegardes</label>${choixDisque(s.disques[0].nom)}
          <div class="actions" style="margin-top:14px"><button class="btn btn-p" id="sauv-activer">Mettre en route</button></div>`
          : '<p class="note">Seul un super admin peut les mettre en route.</p>')
          : '<div class="bandeau"><span>Aucun disque externe : branche celui qui recevra les sauvegardes, il apparaîtra ici.</span></div>'}
      </section>`;
    const b = $("#sauv-activer");
    if (b) b.onclick = async () => {
      b.disabled = true; b.textContent = "Mise en route…";
      try {
        const r = await api("/api/sauvegardes/activer", { method: "POST", json: { disque: $("#sauv-disque").value } });
        if (r.phrase) await montrerPhrase(r.phrase, true);
        toast("Sauvegardes en route : la première peut partir tout de suite");
      } catch (e) { toast(e.message, true); }
      pages.sauvegardes().catch(montrerErreur);
    };
    return;
  }
  const r = s.derniere_reussie;
  contenu.innerHTML = `
    <div class="entete"><div><h1>Sauvegardes</h1><p class="sous-titre">${s.actif ? `Chaque nuit à ${esc(s.heure)}, vers ${esc(s.disque)}` : "En pause"}</p></div>
      <div class="actions"><button class="btn btn-p" id="sauv-lancer"${s.en_cours || !s.branche ? " disabled" : ""}>${s.en_cours ? "Sauvegarde en cours…" : "Sauvegarder maintenant"}</button></div></div>
    ${s.branche ? "" : `<div class="bandeau"><span>Le disque ${esc(s.disque)} n'est pas branché. ${s.actif ? "La sauvegarde partira toute seule dès qu'il reviendra." : ""}</span></div>`}
    <div class="grille g2">
      <section class="carte pad"><h2>Dernière sauvegarde</h2>
        <p style="margin:10px 0">${etatSauvegarde(s)}</p>
        <dl class="cles">
          <dt>Dernière réussie</dt><dd>${r ? `${esc(jourDe(r.fin))} à ${heureDe(r.fin)}` : "Aucune"}</dd>
          ${r && r.taille != null ? `<dt>Contenu</dt><dd>${octets(r.taille)} · ${(r.fichiers || 0).toLocaleString("fr-FR")} fichiers</dd>
          <dt>Nouveautés copiées</dt><dd>${octets(r.ajoute)}${r.nouveaux != null ? ` <span class="note">(${r.nouveaux} nouveaux, ${r.modifies} modifiés)</span>` : ""}</dd>` : ""}
          <dt>Prochaine</dt><dd>${s.prochaine ? (s.prochaine * 1000 - Date.now() < 86400000 ? (+s.heure.slice(0, 2) < 12 ? "Cette nuit" : "Aujourd'hui") : esc(quand(new Date(s.prochaine * 1000).toISOString()))) + ` à ${esc(s.heure)}` : "En pause"}</dd>
        </dl>
      </section>
      <section class="carte pad"><h2>Réglages</h2>${quoi}
        ${superAdmin ? `
        <label class="ligne-reglage"><span><strong>Sauvegarde chaque nuit</strong></span>
          <button class="interrupteur" role="switch" aria-checked="${s.actif}" id="sauv-actif" aria-label="Sauvegarde chaque nuit"><span></span></button></label>
        <label class="ligne-reglage"><span><strong>Heure</strong><br><span class="note">Une sauvegarde manquée (serveur éteint, disque absent) part dès que possible.</span></span>
          <input type="time" id="sauv-heure" value="${esc(s.heure)}" style="width:auto"></label>
        ${s.sources.map((x) => `<label class="ligne-reglage"><span><strong>${esc(x.nom)}</strong><br><span class="note">${esc(x.detail)}</span></span>
          <button class="interrupteur" role="switch" aria-checked="${x.actif}" data-source="${esc(x.id)}" aria-label="${esc(x.nom)}"><span></span></button></label>`).join("")}
        <label class="ligne-reglage"><span><strong>Disque</strong></span><span style="min-width:140px">${choixDisque(s.disque)}</span></label>
        <div class="actions" style="margin-top:12px"><button class="btn" id="sauv-phrase">Revoir la phrase de passe</button></div>`
        : `<p class="note">Seul un super admin peut changer ces réglages.</p>`}
      </section>
    </div>
    <section class="carte pad" id="sauv-copies" style="margin-top:16px"><div class="entete-carte"><h2>Retrouver un fichier</h2></div>
      <p class="note" style="margin:8px 0 12px">Choisis une date, puis le fichier ou le dossier à récupérer. Il revient dans <strong>Mes dossiers › Restaurations</strong> : rien n'est remplacé.</p>
      <div class="actions"><button class="btn" id="sauv-voir"${s.branche ? "" : " disabled"}>Voir les sauvegardes</button></div>
    </section>`;
  $("#sauv-lancer").onclick = async () => {
    try { await api("/api/sauvegardes/lancer", { method: "POST" }); toast("Sauvegarde lancée"); } catch (e) { toast(e.message, true); }
    pages.sauvegardes().catch(montrerErreur);
  };
  $("#sauv-voir").onclick = () => listeCopies().catch((e) => toast(e.message, true));
  if (s.en_cours) suiviSauvegarde = setTimeout(() => { if (location.hash.startsWith("#/sauvegardes")) pages.sauvegardes().catch(montrerErreur); }, 5000);
  if (!superAdmin) return;
  const regler = async (json, quoi2) => {
    try { await api("/api/sauvegardes/reglages", { method: "POST", json }); toast(quoi2); } catch (e) { toast(e.message, true); }
    pages.sauvegardes().catch(montrerErreur);
  };
  $("#sauv-actif").onclick = () => regler({ actif: !s.actif }, s.actif ? "Sauvegardes en pause" : "Sauvegardes réactivées");
  $("#sauv-heure").onchange = (ev) => ev.target.value && regler({ heure: ev.target.value }, `Chaque nuit à ${ev.target.value}`);
  contenu.querySelectorAll("[data-source]").forEach((b) => b.addEventListener("click", () =>
    regler({ sources: { [b.dataset.source]: b.getAttribute("aria-checked") !== "true" } }, "Contenu des sauvegardes changé")));
  $("#sauv-disque").onchange = async (ev) => {
    const nom = ev.target.value;
    if (!(await demander({ titre: `Sauvegarder sur ${nom} ?`, texte: `Les prochaines sauvegardes iront sur ${nom}. Celles déjà faites restent sur ${s.disque}. La phrase de passe ne change pas.`, valider: "Changer" }))) { ev.target.value = s.disque; return; }
    try { await api("/api/sauvegardes/activer", { method: "POST", json: { disque: nom } }); toast(`Sauvegardes vers ${nom}`); } catch (e) { toast(e.message, true); }
    pages.sauvegardes().catch(montrerErreur);
  };
  $("#sauv-phrase").onclick = async () => {
    try { const r = await api("/api/sauvegardes/phrase", { method: "POST" }); await montrerPhrase(r.phrase, false); } catch (e) { toast(e.message, true); }
  };
};

async function listeCopies() {
  const z = $("#sauv-copies");
  z.innerHTML = `<h2>Retrouver un fichier</h2>${chargement(2)}`;
  const { copies } = await api("/api/sauvegardes/historique");
  z.innerHTML = `<div class="entete-carte"><h2>Retrouver un fichier</h2></div>
    <p class="note" style="margin:8px 0 12px">${copies.length ? "Choisis la date de la copie." : "Aucune sauvegarde pour l'instant."}</p>
    <div class="sauv-liste">${copies.map((c) => `<button class="sauv-ligne" data-copie="${esc(c.id)}"><span><strong>${esc(quand(c.date))}</strong> <span class="note">${esc(new Date(c.date).toLocaleString("fr-FR", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" }))}</span></span><span>›</span></button>`).join("")}</div>`;
  z.querySelectorAll("[data-copie]").forEach((b) => b.addEventListener("click", () => {
    const c = copies.find((x) => x.id === b.dataset.copie);
    parcourirCopie(c, null).catch((e) => toast(e.message, true));
  }));
}

const NOMS_RACINES = { "/srv/data": "Fichiers", "/opt/stacks": "Réglages des outils", "/etc": "Réglages du serveur" };
async function parcourirCopie(c, chemin) {
  const z = $("#sauv-copies");
  const date = new Date(c.date).toLocaleString("fr-FR", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  const racine = chemin && c.chemins.find((r) => chemin === r || chemin.startsWith(r + "/"));
  let elements;
  if (!chemin) elements = c.chemins.map((r) => ({ nom: NOMS_RACINES[r] || r, chemin: r, dossier: true }));
  else {
    z.querySelector(".sauv-liste").innerHTML = '<div class="squelette"></div>';
    elements = (await api(`/api/sauvegardes/parcourir?id=${encodeURIComponent(c.id)}&chemin=${encodeURIComponent(chemin)}`)).elements;
  }
  const morceaux = [];
  if (racine) {
    morceaux.push([NOMS_RACINES[racine] || racine, racine]);
    let acc = racine;
    for (const m of chemin.slice(racine.length).split("/").filter(Boolean)) { acc += "/" + m; morceaux.push([m, acc]); }
  }
  z.innerHTML = `<div class="entete-carte"><h2>Copie du ${esc(date)}</h2><button class="btn btn-petit" id="sauv-dates">Autre date</button></div>
    <div class="fil" style="margin:10px 0"><a href="#" data-aller="">Début</a>${morceaux.map(([n, p]) => `<span class="sep">›</span><a href="#" data-aller="${esc(p)}">${esc(n)}</a>`).join("")}</div>
    <div class="sauv-liste">${elements.map((e) => `<div class="sauv-ligne">
        ${e.dossier ? `<a href="#" data-aller="${esc(e.chemin)}"><strong>${esc(e.nom)}</strong></a>` : `<span>${esc(e.nom)} <span class="note">${octets(e.taille)}</span></span>`}
        ${chemin ? `<button class="btn btn-petit" data-restaurer="${esc(e.chemin)}">Récupérer</button>` : ""}</div>`).join("") || '<p class="note">Dossier vide.</p>'}</div>`;
  $("#sauv-dates").onclick = () => listeCopies().catch((e) => toast(e.message, true));
  z.querySelectorAll("[data-aller]").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    parcourirCopie(c, a.dataset.aller || null).catch((e) => toast(e.message, true));
  }));
  z.querySelectorAll("[data-restaurer]").forEach((b) => b.addEventListener("click", async () => {
    const p = b.dataset.restaurer, nom = p.split("/").pop();
    if (!(await demander({ titre: `Récupérer « ${nom} » ?`, texte: `Il revient tel qu'il était le ${date}, dans Mes dossiers › Restaurations. Rien n'est remplacé.`, valider: "Récupérer" }))) return;
    b.disabled = true; b.textContent = "Récupération…";
    try { await api("/api/sauvegardes/restaurer", { method: "POST", json: { id: c.id, chemin: p } }); toast(`« ${nom} » est dans Mes dossiers › Restaurations`); b.textContent = "Récupéré"; }
    catch (e) { toast(e.message, true); b.disabled = false; b.textContent = "Récupérer"; }
  }));
}

// ------------------------------------------------------------ navigation
let moi = null;
function montrerErreur(e) {
  if (e && e.injoignable) return ecranInjoignable(e.injoignable);
  contenu.innerHTML = `<div class="entete"><div><h1>Oups</h1></div></div>${erreur(e)}`;
}

async function router() {
  const [chemin, requete] = (location.hash.slice(2) || (moi && moi.mode === "invite" ? "invite" : "accueil")).split("?");
  let page = pages[chemin] ? chemin : "accueil";
  if (moi && moi.mode === "invite" && page !== "aide") page = "invite";
  if (!moi && page !== "aide") return ecranInjoignable(dernierEchec);   // serveur injoignable   // sans connexion admin : seulement la page invités
  if (page !== "ouvrir") document.body.classList.remove("plein-ecran");
  document.querySelectorAll("[data-page]").forEach((a) => {
    const actif = a.dataset.page === page || (a.dataset.page === "outils" && page === "ouvrir") || (a.dataset.page === "plus" && ["disques", "systeme", "journal", "corbeille", "utilisateurs", "invites", "terminal", "sauvegardes", "nuage"].includes(page)) || (a.dataset.page === "fichiers" && page === "corbeille") || (a.dataset.page === "systeme" && ["journal", "utilisateurs", "terminal", "sauvegardes", "nuage"].includes(page));
    a.classList.toggle("actif", actif);
    if (actif) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  document.title = "Jabba · " + ({ outils: "Établi", ouvrir: "Outil", nuage: "Comptes Nextcloud" }[page] || page[0].toUpperCase() + page.slice(1));
  try { await pages[page](new URLSearchParams(requete || "")); }
  catch (e) { montrerErreur(e); }
}
window.addEventListener("hashchange", router);

(async () => {
  try {
    moi = await api("/api/moi");
    if (moi.serveur) utilisateurAdmin = moi.serveur.toLowerCase();
    if (moi.admin && !moi.admin.provisoire) adminConnecte = { identifiant: moi.admin.identifiant, rang: moi.admin.rang, compte_serveur: moi.admin.identifiant === utilisateurAdmin };
    document.body.classList.toggle("mode-invite", moi.mode === "invite");
    $("#qui").innerHTML = moi.mode === "invite"
      ? `Mode invité · v${esc(moi.version)}`
      : `${esc(moi.admin.identifiant)} · ${moi.admin.rang === "super" ? "super admin" : "admin"}<br>v${esc(moi.version)} · <a href="#" id="sortir">Se déconnecter</a>`;
    const sortir = $("#sortir");
    if (sortir) sortir.onclick = (ev) => { ev.preventDefault(); deconnexion(); };
    // QR code invité : http://<serveur>/?c=1234 → on entre tout seul, puis on retire le code de l'adresse.
    const code = new URLSearchParams(location.search).get("c");
    if (code && moi.mode === "invite") {
      history.replaceState(null, "", location.pathname + "#/invite");
      try { await api("/api/invite/entrer", { method: "POST", json: { code } }); } catch (e) { toast(e.message, true); }
    }
    memo.ecrire("jabba-serveur", moi.serveur || "");
    memo.ecrire("jabba-dernier-contact", String(Date.now()));
  } catch (e) {
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
    if (location.hash === "#/aide") await pages.aide(); else montrerErreur(e);
    return;
  }
  await router();
  if (moi.remise) afficherRemise();
  else if (moi.admin && moi.admin.provisoire) choisirMonMotDePasse(true).then((ok) => ok && location.reload());
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
