// Script d’intégration côté client.
// Usage chez le client : <script src="https://TON-DOMAINE/static/js/embed.js?bot_id=UUID"></script>

(function () {
  // Récupère le bot_id depuis l’URL du script (et pas depuis window.location)
  const thisScript = document.currentScript;
  const src = thisScript ? thisScript.src : "";
  const qs = src.split("?")[1] || "";
  const params = new URLSearchParams(qs);
  const botId = params.get("bot_id");

  if (!botId) {
    console.warn("[Betty] bot_id manquant dans le src du script.");
    return;x
  }

  // Création de la bulle + panneau
  const root = document.createElement("div");
  root.setAttribute("data-betty-root", "");
  document.body.appendChild(root);

  // Styles inline minimalistes (pas de dépendance externe)
  function style(el, obj) {
    Object.assign(el.style, obj);
  }

  const button = document.createElement("button");
  button.setAttribute("aria-label", "Ouvrir Betty");
  button.textContent = "💬 Betty";
  style(button, {
    position: "fixed", right: "20px", bottom: "20px",
    padding: "12px 16px", border: "none", borderRadius: "999px",
    cursor: "pointer", boxShadow: "0 10px 30px rgba(0,0,0,.25)",
    zIndex: "2147483646"
  });

  const panel = document.createElement("div");
  style(panel, {
    position: "fixed", right: "20px", bottom: "70px",
    width: "360px", height: "520px", borderRadius: "18px",
    background: "#0d1117", border: "1px solid #1f2937",
    display: "none", overflow: "hidden",
    boxShadow: "0 10px 40px rgba(0,0,0,.35)",
    zIndex: "2147483646"
  });

  const header = document.createElement("div");
  header.textContent = "Betty";
  style(header, {
    padding: "10px 14px", fontWeight: "600",
    borderBottom: "1px solid #1f2937", color: "#e5e7eb"
  });

  const iframe = document.createElement("iframe");
  // Pour le MVP, on charge une page neutre. Tu pourras pointer vers /chat?bot_id=... quand tu l’auras.
  iframe.src = `https://ton-domaine/chat?bot_id=${botId}`;
  iframe.setAttribute("title", "Betty Chat");
  style(iframe, { width: "100%", height: "calc(100% - 42px)", border: "0" });

  panel.appendChild(header);
  panel.appendChild(iframe);

  root.appendChild(button);
  root.appendChild(panel);

  // Toggle
  button.addEventListener("click", () => {
    panel.style.display = panel.style.display === "none" ? "block" : "none";
  });

  // Expose un petit API global (optionnel)
  window.Betty = {
    open: () => { panel.style.display = "block"; },
    close: () => { panel.style.display = "none"; },
    botId
  };
})();

