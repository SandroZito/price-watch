import test from "node:test";
import assert from "node:assert/strict";
import { shouldFuelCheck, shouldWatchdog, formatWatchdog, shouldNews } from "../side_checks.mjs";

const now = Date.parse("2026-11-26T12:00:00Z");

test("shouldFuelCheck attend 6 heures entre deux relevés", () => {
  assert.equal(shouldFuelCheck({}, now), true);
  assert.equal(shouldFuelCheck({ fuelCheckedAt: "2026-11-26T06:00:00Z" }, now), true);
  assert.equal(shouldFuelCheck({ fuelCheckedAt: "2026-11-26T11:00:00Z" }, now), false);
});

test("shouldWatchdog alerte après 12h sans heartbeat, au plus une fois par jour", () => {
  assert.equal(shouldWatchdog({ heartbeatAt: "2026-11-26T11:30:00Z" }, now), false);
  assert.equal(
    shouldWatchdog({ heartbeatAt: "2026-11-25T20:00:00Z", watchdogAt: "2026-11-26T11:00:00Z" }, now),
    false,
  );
  assert.equal(shouldWatchdog({ heartbeatAt: "2026-11-25T20:00:00Z" }, now), true);
  assert.equal(shouldWatchdog({}, now), false);
});

test("formatWatchdog indique l'âge et le dernier relevé", () => {
  const text = formatWatchdog(16, "2026-11-25T20:00:00Z");
  assert.match(text, /Veille portefeuille en pause/);
  assert.match(text, /16 h/);
  assert.match(text, /2026-11-25T20:00:00Z/);
});

test("shouldNews ne déclenche que le lundi après 07h UTC, une fois par semaine", () => {
  const monday = Date.parse("2026-11-23T08:00:00Z");
  assert.equal(shouldNews({}, monday), true);
  assert.equal(shouldNews({ newsWeek: "2026-W48" }, monday), false);
  assert.equal(shouldNews({}, Date.parse("2026-11-23T06:00:00Z")), false);
  assert.equal(shouldNews({}, Date.parse("2026-11-24T08:00:00Z")), false);
});
