/* static/js/embed.js */
(function(){
  // Le script peut être inséré avec data-attributes OU via query string
  // Exemples:
  // <script src=".../static/js/embed.js" data-bot-id="immo-002-7f3a1c29" data-owner="Cabinet Martin"></script>
  // <script src=".../static/js/embed.js?public_id=immo-002-7f3a1c29&owner=Cabinet%20Martin"></script>

  const thisScript = document.currentScript || (function(){
    const scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  const qs     = new URLSearchParams((thisScript.src.split("?")[1]||""));
  const ds     = thisScript.dataset || {};
  const publicId = ds.botId || ds.publicId || qs.get("public_id") || "";
  const owner   = ds.owner || qs.get("owner") || "";

  if(!publicId){
    console.warn("[Betty Embed] public_id manquant.");
    return;
  }

  const origin = thisScript.src.replace(/\/static\/js\/embed\.js.*$/,'');
  const apiUrl = origin + "/api/embed_meta?public_id=" + encodeURIComponent(publicId);

  // Styles minimalistes du widget
  const css = `
  .betty-widget{position:fixed; right:24px; bottom:24px; z-index:2147483000; font-family:Inter,system-ui,sans-serif}
  .betty-button{border-radius:999px; padding:12px 16px; border:0; cursor:pointer; box-shadow:0 6px 30px rgba(0,0,0,.28); color:#fff; font-weight:600}
  .betty-panel{position:fixed; right:24px; bottom:84px; width:360px; max-height:70vh; background:#0b0f14; color:#e5e7eb; border:1px solid #1f2937; border-radius:16px; overflow:hidden; box-shadow:0 12px 40px rgba(0,0,0,.45)}
  .betty-head{padding:14px 14px 10px; border-bottom:1px solid #1f2937; text-align:center}
  .betty-head img{width:64px;height:64px;border-radius:999px;border:1px solid #1f2937; background:#0b0f14;object-fit:cover}
  .betty-name{margin-top:6px;font-weight:700}
  .betty-body{padding:12px; overflow:auto; max-height:50vh}
  .betty-bubble{max-width:280px; padding:10px 12px; border-radius:12px; border:1px solid #1f2937; margin:8px 0; background:#111827; white-space:pre-wrap}
  .betty-bubble.me{background:#1b2230; margin-left:auto}
  .betty-form{display:flex; gap:8px; padding:12px; border-top:1px solid #1f2937}
  .betty-input{flex:1; border:1px solid #1f2937; border-radius:10px; padding:10px; background:#0b0f14; color:#e5e7eb}
  .betty-send{border:0; border-radius:10px; padding:10px 14px; font-weight:600; cursor:pointer}
  .betty-typing{opacity:.8; font-size:12px}
  `;
  const style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);

  // Container
  const root = document.createElement('div'); root.className = 'betty-widget'; document.body.appendChild(root);

  // Bouton floating
  let color = '#4F46E5';
  const button = document.createElement('button'); button.className = 'betty-button'; button.textContent = 'Parler à Betty';
  button.style.background = color;
  root.appendChild(button);

  // Panel
  const panel = document.createElement('div'); panel.className = 'betty-panel'; panel.style.display = 'none';
  panel.innerHTML = `
    <div class="betty-head">
      <img id="betty-avatar" alt="Avatar">
      <div class="betty-name" id="betty-title">Betty</div>
      <div style="font-size:12px;opacity:.8" id="betty-owner"></div>
    </div>
    <div class="betty-body" id="betty-messages">
      <div class="betty-bubble" id="betty-greet">Bonjour, je suis Betty. Comment puis-je vous aider ?</div>
    </div>
    <form class="betty-form" id="betty-form">
      <input class="betty-input" id="betty-input" type="text" placeholder="Écrivez et appuyez Entrée…" autocomplete="off">
      <button class="betty-send" id="betty-send" type="submit">Envoyer</button>
    </form>
  `;
  root.appendChild(panel);

  const avatarEl = panel.querySelector('#betty-avatar');
  const titleEl  = panel.querySelector('#betty-title');
  const ownerEl  = panel.querySelector('#betty-owner');
  const msgs     = panel.querySelector('#betty-messages');
  const form     = panel.querySelector('#betty-form');
  const input    = panel.querySelector('#betty-input');

  function addBubble(text, me=false){
    const d = document.createElement('div');
    d.className = 'betty-bubble' + (me ? ' me' : '');
    d.textContent = text;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
  }
  let typingEl = null;
  function showTyping(){
    typingEl = document.createElement('div');
    typingEl.className = 'betty-bubble betty-typing';
    typingEl.textContent = 'Betty écrit…';
    msgs.appendChild(typingEl);
    msgs.scrollTop = msgs.scrollHeight;
  }
  function hideTyping(){
    if(typingEl && typingEl.parentNode){ typingEl.parentNode.removeChild(typingEl); }
    typingEl = null;
  }

  // Ouvrir/fermer
  button.addEventListener('click', ()=>{
    const show = panel.style.display === 'none';
    panel.style.display = show ? 'block' : 'none';
    button.textContent = show ? 'Fermer Betty' : 'Parler à Betty';
    if(show) input.focus();
  });

  // Charger méta
  fetch(apiUrl).then(r=>r.json()).then(meta=>{
    if(meta.error){ console.warn("[Betty Embed] "+meta.error); return; }
    color = meta.color_hex || color;
    button.style.background = color;
    titleEl.textContent = meta.display_name || 'Betty';
    ownerEl.textContent = owner || meta.owner_name || '';
    avatarEl.src = meta.avatar_url || '';
    const greet = meta.greeting || '';
    if(greet) document.getElementById('betty-greet').textContent = greet;
  }).catch(()=>{});

  // Envoi message -> API /api/bettybot
  form.addEventListener('submit', function(e){
    e.preventDefault();
    const text = (input.value||'').trim(); if(!text) return;
    addBubble(text, true); input.value = '';
    showTyping();
    fetch(origin + '/api/bettybot', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ message: text, bot_id: publicId.split('-').slice(0,2).join('-') }) // fallback si nécessaire
    })
    .then(r=>r.json())
    .then(j=>{
      hideTyping();
      addBubble(j.response || "Désolé, une erreur est survenue.");
    }).catch(()=>{
      hideTyping();
      addBubble("Désolé, une erreur est survenue.");
    });
  });

})();
