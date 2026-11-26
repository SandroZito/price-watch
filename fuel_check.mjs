#!/usr/bin/env node
/** Prix maximums belges -> alerte Folio Finance, exécuté par GitHub Actions. */
import fs from "node:fs";
import path from "node:path";

const SOURCE = "https://www.energiafed.be/fr/prix-maximums";
const STATE = path.join(process.cwd(), "fuel-state.json");
const LABELS = { diesel: "Diesel", e10: "Essence 95 E10" };
const NAMES = { "Essence 95 RON - E10": "e10", Diesel: "diesel" };

export function parseFuelHtml(html) {
  const out = {};
  for (const block of html.split("samenstelling-chart__header").slice(1)) {
    const name = /__title">([^<]+)</.exec(block)?.[1];
    const key = NAMES[name];
    const raw = /€\/l\s*([\d,.]+)/.exec(block)?.[1];
    const price = Number(raw?.replace(",", "."));
    const date = /à partir du\s*(\d{4}-\d{2}-\d{2})/.exec(block)?.[1];
    if (key && Number.isFinite(price) && price > 0) out[key] = { price, date: date || "" };
  }
  return out;
}

export function computeChanges(previous, current) {
  const changes = [];
  for (const [key, label] of Object.entries(LABELS)) {
    const cur = current[key];
    const prev = previous?.[key];
    if (!cur || !prev) continue;
    if (Math.abs(cur.price - prev.price) > 0.0001) {
      changes.push({ key, label, ...cur, old: prev.price });
    }
  }
  return changes;
}

export function formatFuelMessage(changes) {
  const nf = new Intl.NumberFormat("fr-FR", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
  const eur = (v) => `${nf.format(v)} €`;
  const out = ["⛽ *Folio — carburants en Belgique*", ""];
  for (const c of changes) {
    const up = c.price > c.old;
    out.push(
      `${up ? "📈" : "📉"} *${c.label}* : ${eur(c.price)}/L` +
        ` (${up ? "hausse" : "baisse"} depuis ${eur(c.old)}/L, applicable le ${c.date || "—"})`,
    );
  }
  out.push("", "Prix maximums officiels (Energia / SPF Économie).");
  return out.join("\n");
}

async function sendText(text, token, chat) {
  let last;
  for (let i = 1; i <= 3; i++) {
    try {
      const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chat, text, disable_web_page_preview: true }),
        signal: AbortSignal.timeout(20000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return;
    } catch (e) {
      last = e;
      console.log(`  tentative ${i}/3 : ${e.message}`);
      if (i < 3) await new Promise((r) => setTimeout(r, 1500 * i));
    }
  }
  throw last;
}

export async function main({ dry = false } = {}) {
  let html;
  try {
    const res = await fetch(SOURCE, {
      signal: AbortSignal.timeout(20000),
      headers: { "User-Agent": "Mozilla/5.0 (Folio veille prix)" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    html = await res.text();
  } catch (e) {
    console.log(`Carburant indisponible : ${e.message}`);
    return 0; // le prochain run réessaiera, sans écraser l'état
  }

  const current = parseFuelHtml(html);
  if (!Object.keys(current).length) {
    console.log("Aucun prix carburant trouvé (structure de page modifiée ?)");
    return 0;
  }

  let previous = {};
  try {
    previous = JSON.parse(fs.readFileSync(STATE, "utf8"));
  } catch {
    /* premier relevé : silencieux */
  }
  const changes = computeChanges(previous, current);
  const text = formatFuelMessage(changes);

  if (dry) {
    console.log(changes.length ? text : "Aucun changement de prix carburant.");
    return 0;
  }

  if (changes.length) {
    const token = process.env.FOLIO_TELEGRAM_BOT_TOKEN;
    const chat = process.env.FOLIO_TELEGRAM_CHAT_ID;
    if (!token || !chat) {
      console.log("Secrets Telegram absents, message non envoyé.");
      return 1;
    }
    try {
      await sendText(text, token, chat);
      console.log(`Envoyé : ${changes.length} changement(s) carburant`);
    } catch (e) {
      console.log(`Échec envoi : ${e.message}`);
      return 1; // état non écrit : le message sera réessayé
    }
  } else {
    console.log("Aucun changement de prix carburant.");
  }

  fs.writeFileSync(STATE, JSON.stringify(current, null, 2) + "\n");
  return 0;
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main({ dry: process.argv.includes("--dry") })
    .then((code) => {
      process.exitCode = code;
    })
    .catch((e) => {
      console.error(e);
      process.exitCode = 1;
    });
}
