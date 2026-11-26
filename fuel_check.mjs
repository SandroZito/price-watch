/** Parseur des prix maximums belges (Energia / SPF Économie). */
export const FUEL_SOURCE = "https://www.energiafed.be/fr/prix-maximums";

const LABELS = { diesel: "Diesel", e10: "Essence 95 E10" };
const NAMES = { "Essence 95 RON - E10": "e10", Diesel: "diesel" };

export function parseFuelHtml(html) {
  const out = {};
  for (const block of String(html).split("samenstelling-chart__header").slice(1)) {
    const name = /__title">([^<]+)</.exec(block)?.[1];
    const key = NAMES[name];
    const raw = /€\/l\s*([\d,.]+)/.exec(block)?.[1];
    const price = Number(raw?.replace(",", "."));
    const date = /à partir du\s*(\d{4}-\d{2}-\d{2})/.exec(block)?.[1];
    // Borne de vraisemblance : un prix carburant belge est entre 0,50 et 4 €/L.
    if (key && Number.isFinite(price) && price >= 0.5 && price <= 4) {
      out[key] = { price, date: date || "" };
    }
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
