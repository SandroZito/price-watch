/** Garde-fous temporels pour les alertes exécutées dans GitHub Actions. */
const HOUR = 3600_000;
const DAY = 24 * HOUR;

export function isoWeek(d = new Date()) {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7) + 3);
  const firstThursday = new Date(Date.UTC(t.getUTCFullYear(), 0, 4));
  const week = 1 + Math.round((t.getTime() - firstThursday.getTime()) / (7 * DAY));
  return `${t.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

export function shouldFuelCheck(meta = {}, now = Date.now()) {
  const at = meta.fuelCheckedAt && Date.parse(meta.fuelCheckedAt);
  return !at || !Number.isFinite(at) || now - at >= 6 * HOUR;
}

export function shouldWatchdog(meta = {}, now = Date.now()) {
  const hb = meta.heartbeatAt && Date.parse(meta.heartbeatAt);
  if (!hb || !Number.isFinite(hb)) return false; // jamais de heartbeat : on ne peut pas juger
  if (now - hb < 24 * HOUR) return false;
  const last = meta.watchdogAt && Date.parse(meta.watchdogAt);
  return !last || !Number.isFinite(last) || now - last >= DAY;
}

export function formatWatchdog(ageHours, at) {
  return (
    "⚠️ *Folio — Veille portefeuille en pause*\n\n" +
    `Aucun signe du Mac depuis ${Math.round(ageHours)} h (dernier relevé : ${at}).\n` +
    "Les alertes 5 % / 10 % ne partent pas tant que l'app Folio est arrêtée."
  );
}

export function shouldNews(meta = {}, now = Date.now()) {
  const d = new Date(now);
  const daysSinceMonday = (d.getUTCDay() + 6) % 7;
  const monday07 =
    Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - daysSinceMonday * DAY + 7 * HOUR;
  return now >= monday07 && meta.newsWeek !== isoWeek(d);
}
