#!/usr/bin/env node
/** Relais GitHub Actions : envoie les messages Folio en attente via Telegram. */
import fs from "node:fs";
import path from "node:path";

const FILE = path.join(process.cwd(), "folio-outbox.json");

async function send(text, token, chat) {
  for (let i = 0; i < text.length; i += 3400) {
    const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chat, text: text.slice(i, i + 3400), disable_web_page_preview: true }),
      signal: AbortSignal.timeout(20000),
    });
    if (!res.ok) throw new Error(`Telegram ${res.status}: ${(await res.text()).slice(0, 120)}`);
  }
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
