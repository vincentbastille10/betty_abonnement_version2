from app import app  # expose l'objet Flask pour Vercel
{% extends "base.html" %}
{% block content %}
<div id="chat-container">
  <div id="chat-messages"></div>
  <form id="chat-form">
    <input type="text" id="user-input" placeholder="Écrivez ici..." autocomplete="off">
    <button type="submit">Envoyer</button>
  </form>
</div>
<script>
const botId = new URLSearchParams(window.location.search).get('bot_id');

async function sendMessage(message) {
  const res = await fetch("/api/bettybot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, bot_id: botId })
  });
  const data = await res.json();
  return data.response;
}

document.querySelector("#chat-form").addEventListener("submit", async e => {
  e.preventDefault();
  const input = document.querySelector("#user-input");
  const msg = input.value;
  input.value = "";
  const response = await sendMessage(msg);
  document.querySelector("#chat-messages").innerHTML += `
    <div class="user">${msg}</div>
    <div class="bot">${response}</div>
  `;
});
</script>
{% endblock %}
