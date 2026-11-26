import test from "node:test";
import assert from "node:assert/strict";
import { runSideChecks } from "../folio_send.mjs";
import { parseFuelHtml } from "../fuel_check.mjs";

const NOW = Date.parse("2026-11-26T12:00:00Z");
const fullPage = `
<div class="samenstelling-chart__header">
 <h3 class="samenstelling-chart__title">Essence 95 RON - E10</h3>
 <div class="samenstelling-chart__price">€/l 1.7820</div>
 <div class="samenstelling-chart__date">à partir du 2026-11-24</div>
</div>
<div class="samenstelling-chart__header">
 <h3 class="samenstelling-chart__title">Diesel</h3>
 <div class="samenstelling-chart__price">€/l 2.2030</div>
 <div class="samenstelling-chart__date">à partir du 2026-11-26</div>
</div>`;

function stubFetch(html, newsXml = "<rss><channel></channel></rss>") {
  const original = global.fetch;
  global.fetch = async (url) => ({
    ok: true,
    text: async () => (String(url).includes("energiafed") ? html : newsXml),
  });
  return () => {
    global.fetch = original;
  };
}

const makeMeta = () => ({
  fuel: {
    diesel: { price: 2.19, date: "2026-11-20" },
    e10: { price: 1.78, date: "2026-11-20" },
  },
});

test("une page carburant partielle ne détruit pas l'état", async () => {
  const restore = stubFetch(fullPage.replace(/Essence 95 RON - E10[\s\S]*?<\/div>\s*<\/div>/, "X"));
  const meta = makeMeta();
  const sent = [];
  await runSideChecks(meta, "t", "c", async (t) => sent.push(t), NOW);
  restore();
  assert.deepEqual(meta.fuel, makeMeta().fuel);
  assert.equal(sent.length, 0);
});

test("un prix invraisemblable est rejeté sans écriture d'état", () => {
  const prices = parseFuelHtml(
    fullPage.replace("€/l 2.2030", "€/l 22030").replace("€/l 1.7820", "€/l 1.7820"),
  );
  assert.equal(prices.diesel, undefined);
  assert.equal(prices.e10.price, 1.782);
});

test("le watchdog attend 24 h sans heartbeat, pas 12 h", async () => {
  const restore = stubFetch(fullPage);
  const meta = { ...makeMeta(), fuelCheckedAt: "2026-11-26T11:00:00Z", heartbeatAt: "2026-11-25T16:00:00Z" };
  const sent = [];
  await runSideChecks(meta, "t", "c", async (t) => sent.push(t), NOW);
  restore();
  assert.equal(sent.length, 0);
  assert.equal(meta.watchdogAt, undefined);
});

test("une semaine d'actualité vide n'est pas marquée comme envoyée", async () => {
  const restore = stubFetch(fullPage, "<rss><channel></channel></rss>");
  const monday = Date.parse("2026-11-23T08:00:00Z");
  const meta = { fuelCheckedAt: "2026-11-23T07:00:00Z" };
  const sent = [];
  await runSideChecks(meta, "t", "c", async (t) => sent.push(t), monday);
  restore();
  assert.equal(meta.newsWeek, undefined);
});
