#!/usr/bin/env node
/** Relais GitHub Actions : messages Folio + alertes qui survivent au Mac éteint. */
import fs from "node:fs";
import path from "node:path";
import { FUEL_SOURCE, parseFuelHtml, computeChanges, formatFuelMessage } from "./fuel_check.mjs";
import { shouldFuelCheck, shouldWatchdog, formatWatchdog, shouldNews, isoWeek } from "./side_checks.mjs";
import { fetchDigest } from "./news_digest.mjs";

const FILE = path.join(process.cwd(), "folio-outbox.json");
const CHUNK = 3400;
const MAX_TRIES = 3;
const HOUR = 3600_000;
const DRY = process.argv.includes("--dry");

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

async function discoverChat(token) {
  const updates = await api(token, "getUpdates", { limit: 20 });
  const chats = (updates || [])
    .map((u) => u.message?.chat || u.my_chat_member?.chat || u.edited_message?.chat)
    .filter(Boolean);
  const pick = chats.find((c) => c.type === "private") || chats[0];
  if (!pick) throw new Error("Aucun chat trouvé : envoyez /start au bot puis réessayez.");
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
  if (DRY) {
    console.log(`[dry] message pour ${chat} :\n${text}`);
    return;
  }
  for (let i = 0; i < text.length; i += CHUNK) {
    await sendChunk(text.slice(i, i + CHUNK), token, chat);
  }
}

/** Trois vérifications indépendantes du Mac, avec état persisté dans outbox.meta. */
async function runSideChecks(meta, token, chat) {
  const now = Date.now();

  if (shouldFuelCheck(meta, now)) {
    try {
      const res = await fetch(FUEL_SOURCE, {
        signal: AbortSignal.timeout(20000),
        headers: { "User-Agent": "Mozilla/5.0 (Folio veille prix)" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const current = parseFuelHtml(await res.text());
      if (!Object.keys(current).length) throw new Error("page carburant illisible");
      const changes = computeChanges(meta.fuel, current);
      if (changes.length) {
        await send(formatFuelMessage(changes), token, chat);
        console.log(`Envoyé : ${changes.length} changement(s) carburant`);
      }
      meta.fuel = current;
      meta.fuelCheckedAt = new Date(now).toISOString();
    } catch (e) {
      console.log(`Carburant : ${e.message} (réessai au prochain tour)`);
    }
  }

  if (shouldWatchdog(meta, now)) {
    const age = (now - Date.parse(meta.heartbeatAt)) / HOUR;
    try {
      await send(formatWatchdog(age, meta.heartbeatAt), token, chat);
      meta.watchdogAt = new Date(now).toISOString();
      console.log("Envoyé : watchdog Mac");
    } catch (e) {
      console.log(`Watchdog : ${e.message}`);
    }
  }

  if (shouldNews(meta, now)) {
    try {
      const text = await fetchDigest();
      if (text) {
        await send(text, token, chat);
        console.log("Envoyé : digeste actualité");
      }
      meta.newsWeek = isoWeek(new Date(now));
    } catch (e) {
      console.log(`Actualité : ${e.message} (réessai au prochain tour)`);
    }
  }
}

async function main() {
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
  outbox.meta ||= {};

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
  for (const m of outbox.messages || []) {
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

  const metaBefore = JSON.stringify(outbox.meta);
  await runSideChecks(outbox.meta, token, chat);
  const metaChanged = JSON.stringify(outbox.meta) !== metaBefore;

  if (DRY) {
    console.log("[dry] fichier non modifié.");
    return;
  }
  if (!sent.length && !failed.length && !metaChanged) {
    console.log("Rien à écrire dans l'outbox.");
    return;
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
