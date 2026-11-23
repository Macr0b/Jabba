// Thème (clair, sombre) et style (Cube, Salle des machines, Atelier clair) choisis sur cet appareil
// (Système → Apparence), posés avant l'affichage pour éviter un flash.
// Script classique (pas un module) chargé dans <head> : la politique de sécurité interdit le script en ligne.
// « auto » (ou rien) : le thème suit l'appareil.
//
// Sommaire : une seule fonction, lancée tout de suite, sans nom.
(function () {
  var theme = "auto";
  var style = "cube";
  try {
    theme = localStorage.getItem("jabba-theme") || "auto";
    style = localStorage.getItem("jabba-style") || "cube";
  } catch (_) {
    /* navigation privée : on suit l'appareil, style Cube */
  }
  if (theme === "clair" || theme === "sombre") document.documentElement.dataset.theme = theme;
  if (style === "machines" || style === "atelier") document.documentElement.dataset.style = style;
})();
