// Thème choisi sur cet appareil (Système → Apparence), posé avant l'affichage pour éviter un flash.
// Script classique (pas un module) chargé dans <head> : la politique de sécurité interdit le script en ligne.
// « auto » (ou rien) : le thème suit l'appareil.
//
// Sommaire : une seule fonction, lancée tout de suite, sans nom.
(function () {
  var theme = "auto";
  try {
    theme = localStorage.getItem("jabba-theme") || "auto";
  } catch (_) {
    /* navigation privée : on suit l'appareil */
  }
  if (theme === "clair" || theme === "sombre") document.documentElement.dataset.theme = theme;
})();
