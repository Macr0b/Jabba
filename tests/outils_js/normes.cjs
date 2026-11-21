// Contrôle des normes du code de l'interface (app/static/js) :
//  - chaque fichier commence par un commentaire avec un « Sommaire » qui nomme chacune de ses fonctions ;
//  - une instruction par ligne (pas deux instructions, ni deux déclarations, sur la même ligne).
// Usage : node tests/outils_js/normes.cjs <dossier>   (code de sortie 1 et la liste des écarts s'il y en a)
const fs = require("fs");
const path = require("path");
const acorn = require("acorn");

function fichiers(dossier) {
  return fs.readdirSync(dossier, { withFileTypes: true }).flatMap((entree) => {
    const chemin = path.join(dossier, entree.name);
    if (entree.isDirectory()) return entree.name === "vendor" ? [] : fichiers(chemin);
    return entree.name.endsWith(".js") ? [chemin] : [];
  });
}

// Les fonctions de premier niveau, exportées ou non (déclarations, ou constantes qui valent une fonction).
function nomsDePremierNiveau(programme) {
  const noms = [];
  for (let noeud of programme.body) {
    if (noeud.type === "ExportNamedDeclaration" && noeud.declaration) noeud = noeud.declaration;
    if (noeud.type === "FunctionDeclaration") noms.push(noeud.id.name);
    if (noeud.type === "VariableDeclaration")
      noeud.declarations.forEach((d) => {
        if (d.init && /Function/.test(d.init.type) && d.id.type === "Identifier") noms.push(d.id.name);
      });
  }
  return noms;
}

// Deux instructions qui commencent sur la même ligne, dans le même bloc ; ou plusieurs déclarations d'un coup.
function lignesChargees(programme, rel) {
  const ecarts = [];
  const visiter = (noeud) => {
    if (!noeud || typeof noeud.type !== "string") return;
    const corps = Array.isArray(noeud.body) ? noeud.body : noeud.type === "SwitchCase" ? noeud.consequent : null;
    if (corps) {
      for (let i = 1; i < corps.length; i++)
        if (corps[i].loc.start.line === corps[i - 1].loc.end.line)
          ecarts.push(`${rel}:${corps[i].loc.start.line} deux instructions sur la ligne`);
    }
    if (noeud.type === "VariableDeclaration" && noeud.declarations.length > 1)
      ecarts.push(`${rel}:${noeud.loc.start.line} plusieurs déclarations d'un coup`);
    for (const cle of Object.keys(noeud)) {
      if (cle === "loc") continue;
      const valeur = noeud[cle];
      if (Array.isArray(valeur)) valeur.forEach(visiter);
      else if (valeur && typeof valeur.type === "string") visiter(valeur);
    }
  };
  visiter(programme);
  return ecarts;
}

const racine = process.argv[2];
const ecarts = [];
for (const fichier of fichiers(racine)) {
  const rel = path.relative(racine, fichier);
  const source = fs.readFileSync(fichier, "utf8");
  const commentaires = [];
  const programme = acorn.parse(source, {
    ecmaVersion: "latest",
    sourceType: "module",
    locations: true,
    onComment: commentaires,
  });
  const entete = commentaires.filter((c) => c.end <= (programme.body[0] ? programme.body[0].start : source.length));
  const texte = entete.map((c) => c.value).join("\n");
  if (!/Sommaire/.test(texte)) ecarts.push(`${rel} : pas de « Sommaire » en tête`);
  else {
    const echappe = (nom) => nom.replace(/[$]/g, "\\$");
    const oublies = nomsDePremierNiveau(programme).filter(
      (nom) => !new RegExp(`(?<![\\w$])${echappe(nom)}(?![\\w$])`).test(texte),
    );
    if (oublies.length) ecarts.push(`${rel} : absent du sommaire : ${oublies.join(", ")}`);
  }
  ecarts.push(...lignesChargees(programme, rel));
}
ecarts.forEach((ecart) => console.log(ecart));
console.log(`${ecarts.length} écart${ecarts.length > 1 ? "s" : ""}`);
process.exit(ecarts.length ? 1 : 0);
