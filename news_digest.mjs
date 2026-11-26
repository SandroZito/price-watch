#!/usr/bin/env node
/** Digeste d'actualité Belgique/économie pour Folio Finance (RSS public). */
const FEED =
  "https://news.google.com/rss/search?q=Belgique%20%C3%A9conomie%20finances&hl=fr&gl=BE&ceid=BE:fr";

const decode = (s) =>
  String(s || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .trim();

export function parseRss(xml) {
  const items = [];
  const seen = new Set();
  for (const match of String(xml).matchAll(/<item>([\s\S]*?)<\/item>/g)) {
    const block = match[1];
    const title = decode(/<title>([\s\S]*?)<\/title>/.exec(block)?.[1]);
    const link = decode(/<link>([\s\S]*?)<\/link>/.exec(block)?.[1]);
    const source = decode(/<source[^>]*>([\s\S]*?)<\/source>/.exec(block)?.[1]);
    if (!title || seen.has(title)) continue;
    seen.add(title);
    items.push({ title, link, source: source || "source inconnue" });
  }
  return items.slice(0, 5);
}

export function formatDigest(items) {
  if (!items?.length) return "";
  const out = ["📰 *Folio — actualité Belgique / économie*", ""];
  for (const item of items) {
    out.push(`• ${item.title} — ${item.source}`);
    if (item.link) out.push(`  ${item.link}`);
  }
  out.push("", "⚠️ Contenu agrégé automatiquement, pas un conseil en investissement.");
  return out.join("\n");
}

export async function fetchDigest() {
  const res = await fetch(FEED, {
    signal: AbortSignal.timeout(20000),
    headers: { "User-Agent": "Mozilla/5.0 (Folio veille actualité)" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return formatDigest(parseRss(await res.text()));
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  fetchDigest()
    .then((text) => console.log(text || "Aucune actualité."))
    .catch((e) => {
      console.error(e);
      process.exitCode = 1;
    });
}
