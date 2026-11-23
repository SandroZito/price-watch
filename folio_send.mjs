#!/usr/bin/env node
/** Relais GitHub Actions : envoie les messages Folio via un bot Telegram dédié. */
import fs from "node:fs";
import path from "node:path";

const FILE = path.join(process.cwd(), "folio-outbox.json");
const CHUNK = 3400;
const MAX_TRIES = 3;

const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(token, method, params = {}) {
  const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    signal: AbortSignal.timeout(20000),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(`Telegram ${method}: ${data.description || res.status}`);
  return data.result;
}

/** Trouve le chat du nouveau bot : on lui a envoyé /start au moins une fois. */
async function discoverChat(token) {
  const updates = await api(token, "getUpdates", { limit: 20 });
  const chats = (updates || [])
    .map((u) => u.message?.chat || u.my_chat_member?.chat || u.edited_message?.chat)
    .filter(Boolean);
  const pick = chats.find((c) => c.type === "private") || chats[0];
  if (!pick) throw new Error("Aucun chat trouvé : envoyez /start au nouveau bot puis réessayez.");
  console.log(`Chat détecté : ${pick.id} (${pick.type})`);
  return String(pick.id);
}

async function sendChunk(text, token, chat) {
  let lastError;
  for (let attempt = 1; attempt <= MAX_TRIES; attempt++) {
    try {
      await api(token, "sendMessage", { chat_id: chat, text, disable_web_page_preview: true });
      return;
    } catch (e) {
      lastError = e;
      console.log(`  tentative ${attempt}/${MAX_TRIES} échouée : ${e.message}`);
      if (attempt < MAX_TRIES) await pause(1500 * attempt);
    }
  }
  throw lastError;
}

async function send(text, token, chat) {
  const chunks = [];
  for (let i = 0; i < text.length; i += CHUNK) chunks.push(text.slice(i, i + CHUNK));
  for (const chunk of chunks) await sendChunk(chunk, token, chat);
}

async function main() {
  // Nouveau bot dédié si le secret existe, sinon ancien bot (transition).
  const dedicated = process.env.FOLIO_TELEGRAM_BOT_TOKEN;
  const token = dedicated || process.env.TELEGRAM_BOT_TOKEN;
  if (!token) {
    console.log("Secret Telegram absent, rien à faire.");
    return;
  }

  let outbox;
  try {
    outbox = JSON.parse(fs.readFileSync(FILE, "utf8"));
  } catch {
    console.log("Aucun outbox, rien à faire.");
    return;
  }
  if (!outbox.messages?.length) {
    console.log("Aucun message en attente.");
    return;
  }

  let chat = dedicated
    ? process.env.FOLIO_TELEGRAM_CHAT_ID || outbox.chat_id
    : process.env.TELEGRAM_CHAT_ID;
  if (dedicated && !chat) chat = await discoverChat(token);
  if (!chat) {
    console.log("Chat Telegram absent, rien à faire.");
    return;
  }

  const failed = [];
  const sent = [];
  for (const m of outbox.messages) {
    try {
      await send(m.text, token, chat);
      sent.push(m.id);
      console.log(`Envoyé : ${m.id}`);
    } catch (e) {
      failed.push(m);
      console.log(`Échec ${m.id}: ${e.message}`);
    }
    await pause(600);
  }
  outbox.chat_id = String(chat);
  outbox.messages = failed;
  outbox.last_run = new Date().toISOString();
  fs.writeFileSync(FILE, JSON.stringify(outbox, null, 2) + "\n");
  console.log(`Terminé : ${sent.length} envoyé(s), ${failed.length} en échec.`);
  if (failed.length) process.exitCode = 1;
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
