import test from "node:test";
import assert from "node:assert/strict";
import { parseFuelHtml, computeChanges, formatFuelMessage } from "../fuel_check.mjs";

const HTML = `
<div class="samenstelling-chart__header">
  <h3 class="samenstelling-chart__title">Essence 95 RON - E10</h3>
  <div class="samenstelling-chart__price price-change--equal">€/l 1.7820</div>
  <div class="samenstelling-chart__date">à partir du 2026-11-24</div>
</div>
<div class="samenstelling-chart__header">
  <h3 class="samenstelling-chart__title">Diesel</h3>
  <div class="samenstelling-chart__price price-change--equal">€/l 2.2030</div>
  <div class="samenstelling-chart__date">à partir du 2026-11-26</div>
</div>
`;

test("parseFuelHtml extrait diesel et e10 avec leur date d'effet", () => {
  const prices = parseFuelHtml(HTML);
  assert.deepEqual(prices, {
    diesel: { price: 2.203, date: "2026-11-26" },
    e10: { price: 1.782, date: "2026-11-24" },
  });
});

test("parseFuelHtml renvoie vide si la page change de structure", () => {
  assert.deepEqual(parseFuelHtml("<html>sans prix</html>"), {});
});

test("computeChanges détecte une hausse et une baisse", () => {
  const previous = {
    diesel: { price: 2.19, date: "2026-11-20" },
    e10: { price: 1.8, date: "2026-11-20" },
  };
  const current = parseFuelHtml(HTML);
  const changes = computeChanges(previous, current);
  assert.equal(changes.length, 2);
  assert.equal(changes[0].key, "diesel");
  assert.equal(changes[0].old, 2.19);
  assert.equal(changes[0].price, 2.203);
  assert.equal(changes[1].key, "e10");
});

test("computeChanges ne signale rien sans changement", () => {
  const current = parseFuelHtml(HTML);
  assert.deepEqual(computeChanges(current, current), []);
});

test("formatFuelMessage affiche 4 décimales, le sens et la date", () => {
  const changes = computeChanges(
    { diesel: { price: 2.19, date: "2026-11-20" } },
    { diesel: { price: 2.203, date: "2026-11-26" } },
  );
  const text = formatFuelMessage(changes);
  assert.match(text, /📈.*Diesel/);
  assert.match(text, /2,2030 €\/L/);
  assert.match(text, /2026-11-26/);
});
