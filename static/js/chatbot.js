// Démo ultra-légère pour la page d’accueil.
// Affiche 3 messages successifs pour montrer le concept.

(function () {
  const logId = "demo-log";
  function append(line) {
    const box = document.getElementById(logId);
    if (!box) return;
    const p = document.createElement("p");
    p.textContent = line;
    box.appendChild(p);
  }

  function runDemo() {
    const box = document.getElementById(logId);
    if (!box) return;
    box.innerHTML = ""; // reset
    const lines = [
      "👋 Bonjour, je suis Betty — votre chatbot métier clé en main.",
      "Je qualifie vos leads 24/7 et envoie les demandes vers votre email.",
      "Choisissez un pack (Avocat / Médecin / Immo), une couleur, un avatar — et c’est parti !"
    ];
    let i = 0;
    (function tick() {
      if (i < lines.length) {
        append(lines[i++]);
        setTimeout(tick, 800);
      }
    })();
  }

  window.addEventListener("DOMContentLoaded", runDemo);

  // bouton relancer (si un élément #demo-restart existe)
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t && t.id === "demo-restart") runDemo();
  });
})();
