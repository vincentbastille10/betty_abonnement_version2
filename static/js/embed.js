// Usage client : <script src="https://TON-DOMAINE/static/js/embed.js?bot_id=UUID"></script>

(function () {
  // --- Params ---
  const thisScript = document.currentScript;
  const src = thisScript ? thisScript.src : "";
  const qs = src.split("?")[1] || "";
  const params = new URLSearchParams(qs);
  const botId = params.get("bot_id");
  if (!botId) {
    console.warn("[Betty] bot_id manquant dans le src du script.");
    return;
  }

  // --- Helpers ---
  function style(el, obj) { Object.assign(el.style, obj); }
  function el(tag, attrs = {}, styles = {}) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    style(e, styles);
    return e;
  }

  // --- Conteneur racine plein écran (option : fixe en bas à droite si tu préfères) ---
  const root = el("div", {"data-betty-root": ""}, {
    position: "fixed",
    right: "20px",
    bottom: "20px",
    width: "420px",
    height: "560px",
    borderRadius: "18px",
    background: "#0d1117",
    border: "1px solid #1f2937",
    boxShadow: "0 10px 40px rgba(0,0,0,.35)",
    overflow: "hidden",
    zIndex: "2147483646",
    display: "block"
  });
  document.body.appendChild(root);

  // --- Header avec avatar + titre ---
  const header = el("div", {}, {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    padding: "10px 12px",
    borderBottom: "1px solid #1f2937",
    color: "#e5e7eb",
    fontWeight: "600"
  });

  const avatar = el("img", { alt: "Avatar Betty", referrerpolicy: "no-referrer" }, {
    width: "28px", height: "28px", borderRadius: "999px", objectFit: "cover",
    background: "#111827"
  });
  // On tentera de récupérer l’avatar via /api/bot_meta, sinon fallback
  avatar.src = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/1f916.svg";

  const title = el("div", {}, { fontSize: "14px" });
  title.textContent = "Betty";

  header.appendChild(avatar);
  header.appendChild(title);

  // --- Zone messages ---
  const messages = el("div", {}, {
    height: "calc(100% - 50px - 54px)",
    padding: "12px",
    overflowY: "auto",
    scrollBehavior: "smooth",
    background: "#0b0f14"
  });

  function pushMsg(text, who = "bot") {
    const wrap = el("div", {}, { marginBottom: "10px", display: "flex" });
    if (who === "user") style(wrap, { justifyContent: "flex-end" });

    const bubble = el("div", {}, {
      maxWidth: "80%",
      padding: "10px 12px",
      borderRadius: "12px",
      lineHeight: "1.35",
      fontSize: "14px",
      whiteSpace: "pre-wrap",
      color: "#e5e7eb",
      background: who === "user" ? "#1f2937" : "#111827",
      border: "1px solid #1f2937"
    });
    bubble.textContent = text;
    wrap.appendChild(bubble);
    messages.appendChild(wrap);
    messages.scrollTop = messages.scrollHeight;
  }

  // Message d’accueil
  pushMsg("Bonjour, je suis Betty. Comment puis-je vous aider ?");

  // --- Zone input ---
  const form = el("form", { "aria-label": "Envoyer un message à Betty" }, {
    display: "flex",
    gap: "8px",
    padding: "10px",
    borderTop: "1px solid #1f2937",
    background: "#0d1117"
  });
  const input = el("input", { type: "text", placeholder: "Écrivez et appuyez sur Entrée…" }, {
    flex: "1",
    padding: "12px 14px",
    borderRadius: "12px",
    border: "1px solid #1f2937",
    outline: "none",
    background: "#0b0f14",
    color: "#e5e7eb",
    fontSize: "14px"
  });
  const sendBtn = el("button", { type: "submit" }, {
    padding: "12px 14px",
    borderRadius: "12px",
    border: "1px solid #1f2937",
    background: "#111827",
    color: "#e5e7eb",
    cursor: "pointer"
  });
  sendBtn.textContent = "Envoyer";

  form.appendChild(input);
  form.appendChild(sendBtn);

  root.appendChild(header);
  root.appendChild(messages);
  root.appendChild(form);

  input.focus();

  // --- Métadonnées bot (nom, avatar, couleur) ---
  // Facultatif mais recommandé : fournis /api/bot_meta côté serveur
  fetch(`/api/bot_meta?bot_id=${encodeURIComponent(botId)}`)
    .then(r => r.ok ? r.json() : null)
    .then(meta => {
      if (!meta) return;
      if (meta.name) title.textContent = meta.name;
      if (meta.avatar_url) avatar.src = meta.avatar_url;
      if (meta.color_hex) {
        // Accent sur le bouton / bordures si tu veux
        sendBtn.style.borderColor = meta.color_hex;
      }
    })
    .catch(() => { /* ignore */ });

  // --- Envoi message (Entrée = submit) ---
  let busy = false;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = (input.value || "").trim();
    if (!text || busy) return;

    pushMsg(text, "user");
    input.value = "";
    input.disabled = true;
    sendBtn.disabled = true;
    busy = true;

    try {
      const res = await fetch("/api/bettybot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, bot_id: botId })
      });
      const data = await res.json();
      const reply = (data && data.response) ? data.response : "Désolé, une erreur est survenue.";
      pushMsg(reply, "bot");
    } catch (err) {
      pushMsg("Erreur réseau. Réessayez dans un instant.", "bot");
    } finally {
      busy = false;
      input.disabled = false;
      sendBtn.disabled = false;
      input.focus();
    }
  });
})();
