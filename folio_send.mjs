#!/usr/bin/env node
/** Relais GitHub Actions : envoie les messages Folio en attente via Telegram. */
import fs from "node:fs";
import path from "node:path";

const FILE = path.join(process.cwd(), "folio-outbox.json");
const CHUNK = 3400;
const MAX_TRIES = 3;

const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function sendChunk(text, token, chat) {
  let lastError;
  for (let attempt = 1; attempt <= MAX_TRIES; attempt++) {
    try {
      const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chat, text, disable_web_page_preview: true }),
        signal: AbortSignal.timeout(20000),
      });
      if (!res.ok) throw new Error(`Telegram ${res.status}: ${(await res.text()).slice(0, 120)}`);
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
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chat = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chat) {
    console.log("Secrets Telegram absents, rien à faire.");
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
