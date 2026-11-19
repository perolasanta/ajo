/**
 * Red-first tests for Unit 3: dividend period helpers.
 * Run: node --test frontend/src/dividendPeriods.test.mjs
 */
import assert from "node:assert/strict";
import test from "node:test";
import {
  periodsForFrequency,
  periodKeyForFrequency,
  periodLabel,
  periodBoundaries,
  recordDateInPeriod,
} from "./dividendPeriods.mjs";

// ---------------------------------------------------------------------------
// periodsForFrequency
// ---------------------------------------------------------------------------

test("annual: returns years up to and including this year", () => {
  const periods = periodsForFrequency("annual", new Date("2026-11-18"));
  const keys = periods.map((p) => p.key);
  assert.ok(keys.includes("2026"), "current year included");
  assert.ok(keys.includes("2025"), "past year included");
  assert.ok(!keys.includes("2027"), "future year excluded");
});

test("annual: does not include years before 2001", () => {
  const periods = periodsForFrequency("annual", new Date("2026-01-01"));
  const keys = periods.map((p) => p.key);
  assert.ok(!keys.includes("2000"), "2000 excluded");
  assert.ok(keys.includes("2001"), "2001 included");
});

test("half_yearly: H1 and H2 up to current half-year on 18 Nov 2026", () => {
  const periods = periodsForFrequency("half_yearly", new Date("2026-11-18"));
  const keys = periods.map((p) => p.key);
  assert.ok(keys.includes("2026-H2"), "current half included");
  assert.ok(keys.includes("2026-H1"), "past half included");
  assert.ok(!keys.includes("2027-H1"), "future half excluded");
});

test("half_yearly: H1 available on 2026-01-01, H2 not yet", () => {
  const periods = periodsForFrequency("half_yearly", new Date("2026-01-01"));
  const keys = periods.map((p) => p.key);
  assert.ok(keys.includes("2026-H1"), "H1 starts 1 Jan");
  assert.ok(!keys.includes("2026-H2"), "H2 starts 1 Jul, not yet");
});

test("quarterly: Q1-Q4 up to current quarter on 18 Nov 2026", () => {
  const periods = periodsForFrequency("quarterly", new Date("2026-11-18"));
  const keys = periods.map((p) => p.key);
  assert.ok(keys.includes("2026-Q4"), "Q4 included");
  assert.ok(keys.includes("2026-Q1"), "Q1 included");
  assert.ok(!keys.includes("2027-Q1"), "future excluded");
});

test("quarterly: only Q1 available on 2024-01-01", () => {
  const periods = periodsForFrequency("quarterly", new Date("2024-01-01"));
  const keys = periods.map((p) => p.key);
  assert.ok(keys.includes("2024-Q1"));
  assert.ok(!keys.includes("2024-Q2"));
});

test("monthly: months up to November on 18 Nov 2026", () => {
  const periods = periodsForFrequency("monthly", new Date("2026-11-18"));
  const keys = periods.map((p) => p.key);
  assert.ok(keys.includes("2026-M11"), "November included");
  assert.ok(keys.includes("2026-M01"), "January included");
  assert.ok(!keys.includes("2026-M12"), "December not yet");
});

test("monthly: December included on 2026-12-31", () => {
  const periods = periodsForFrequency("monthly", new Date("2026-12-31"));
  const keys = periods.map((p) => p.key);
  assert.ok(keys.includes("2026-M12"));
});

// ---------------------------------------------------------------------------
// periodLabel
// ---------------------------------------------------------------------------

test("annual label is just the year", () => {
  assert.equal(periodLabel("2026"), "2026");
});

test("half-yearly H1 label includes Jan and Jun", () => {
  assert.match(periodLabel("2026-H1"), /Jan|H1/i);
  assert.match(periodLabel("2026-H1"), /Jun|2026/i);
});

test("half-yearly H2 label includes Jul and Dec", () => {
  assert.match(periodLabel("2026-H2"), /Jul|H2/i);
});

test("quarterly Q1 label includes Q1 or Jan-Mar", () => {
  assert.match(periodLabel("2026-Q1"), /Q1|Jan/i);
});

test("quarterly Q4 label includes Q4 or Oct", () => {
  assert.match(periodLabel("2026-Q4"), /Q4|Oct/i);
});

test("monthly M11 label includes Nov and 2026", () => {
  assert.match(periodLabel("2026-M11"), /Nov/i);
  assert.match(periodLabel("2026-M11"), /2026/);
});

test("monthly M01 label includes Jan", () => {
  assert.match(periodLabel("2026-M01"), /Jan/i);
});

// ---------------------------------------------------------------------------
// periodBoundaries
// ---------------------------------------------------------------------------

test("annual 2026: 1 Jan to 31 Dec", () => {
  const { start, end } = periodBoundaries("2026");
  assert.equal(start.toISOString().slice(0, 10), "2026-01-01");
  assert.equal(end.toISOString().slice(0, 10), "2026-12-31");
});

test("H1 2026: 1 Jan to 30 Jun", () => {
  const { start, end } = periodBoundaries("2026-H1");
  assert.equal(start.toISOString().slice(0, 10), "2026-01-01");
  assert.equal(end.toISOString().slice(0, 10), "2026-06-30");
});

test("H2 2026: 1 Jul to 31 Dec", () => {
  const { start, end } = periodBoundaries("2026-H2");
  assert.equal(start.toISOString().slice(0, 10), "2026-07-01");
  assert.equal(end.toISOString().slice(0, 10), "2026-12-31");
});

test("Q2 2026: 1 Apr to 30 Jun", () => {
  const { start, end } = periodBoundaries("2026-Q2");
  assert.equal(start.toISOString().slice(0, 10), "2026-04-01");
  assert.equal(end.toISOString().slice(0, 10), "2026-06-30");
});

test("Q4 2026: 1 Oct to 31 Dec", () => {
  const { start, end } = periodBoundaries("2026-Q4");
  assert.equal(start.toISOString().slice(0, 10), "2026-10-01");
  assert.equal(end.toISOString().slice(0, 10), "2026-12-31");
});

test("M02 2024 (leap year): 1 Feb to 29 Feb", () => {
  const { start, end } = periodBoundaries("2024-M02");
  assert.equal(start.toISOString().slice(0, 10), "2024-02-01");
  assert.equal(end.toISOString().slice(0, 10), "2024-02-29");
});

test("M02 2026 (non-leap): 1 Feb to 28 Feb", () => {
  const { start, end } = periodBoundaries("2026-M02");
  assert.equal(start.toISOString().slice(0, 10), "2026-02-01");
  assert.equal(end.toISOString().slice(0, 10), "2026-02-28");
});

test("M12 2026: 1 Dec to 31 Dec", () => {
  const { start, end } = periodBoundaries("2026-M12");
  assert.equal(start.toISOString().slice(0, 10), "2026-12-01");
  assert.equal(end.toISOString().slice(0, 10), "2026-12-31");
});

// ---------------------------------------------------------------------------
// recordDateInPeriod
// ---------------------------------------------------------------------------

test("record date inside annual period", () => {
  assert.equal(recordDateInPeriod("2026-06-30", "2026"), true);
  assert.equal(recordDateInPeriod("2026-01-01", "2026"), true);
  assert.equal(recordDateInPeriod("2026-12-31", "2026"), true);
});

test("record date outside annual period", () => {
  assert.equal(recordDateInPeriod("2027-01-01", "2026"), false);
  assert.equal(recordDateInPeriod("2025-12-31", "2026"), false);
});

test("record date inside Q3", () => {
  assert.equal(recordDateInPeriod("2026-07-15", "2026-Q3"), true);
  assert.equal(recordDateInPeriod("2026-09-30", "2026-Q3"), true);
});

test("record date outside Q3", () => {
  assert.equal(recordDateInPeriod("2026-06-30", "2026-Q3"), false);
  assert.equal(recordDateInPeriod("2026-10-01", "2026-Q3"), false);
});

test("record date inside M11", () => {
  assert.equal(recordDateInPeriod("2026-11-01", "2026-M11"), true);
  assert.equal(recordDateInPeriod("2026-11-30", "2026-M11"), true);
});

test("record date outside M11", () => {
  assert.equal(recordDateInPeriod("2026-10-31", "2026-M11"), false);
  assert.equal(recordDateInPeriod("2026-12-01", "2026-M11"), false);
});

// ---------------------------------------------------------------------------
// periodKeyForFrequency
// ---------------------------------------------------------------------------

test("annual key for 2026-06-15 is 2026", () => {
  assert.equal(periodKeyForFrequency("annual", new Date("2026-06-15")), "2026");
  assert.equal(periodKeyForFrequency("annual", new Date("2026-12-31")), "2026");
});

test("half_yearly: Jan-Jun gives H1, Jul-Dec gives H2", () => {
  assert.equal(periodKeyForFrequency("half_yearly", new Date("2026-01-01")), "2026-H1");
  assert.equal(periodKeyForFrequency("half_yearly", new Date("2026-06-30")), "2026-H1");
  assert.equal(periodKeyForFrequency("half_yearly", new Date("2026-07-01")), "2026-H2");
  assert.equal(periodKeyForFrequency("half_yearly", new Date("2026-12-31")), "2026-H2");
});

test("quarterly keys for each quarter", () => {
  assert.equal(periodKeyForFrequency("quarterly", new Date("2026-01-01")), "2026-Q1");
  assert.equal(periodKeyForFrequency("quarterly", new Date("2026-03-31")), "2026-Q1");
  assert.equal(periodKeyForFrequency("quarterly", new Date("2026-04-01")), "2026-Q2");
  assert.equal(periodKeyForFrequency("quarterly", new Date("2026-09-30")), "2026-Q3");
  assert.equal(periodKeyForFrequency("quarterly", new Date("2026-10-01")), "2026-Q4");
  assert.equal(periodKeyForFrequency("quarterly", new Date("2026-12-31")), "2026-Q4");
});

test("monthly key for November 2026", () => {
  assert.equal(periodKeyForFrequency("monthly", new Date("2026-11-01")), "2026-M11");
  assert.equal(periodKeyForFrequency("monthly", new Date("2026-11-30")), "2026-M11");
  assert.equal(periodKeyForFrequency("monthly", new Date("2026-01-15")), "2026-M01");
  assert.equal(periodKeyForFrequency("monthly", new Date("2026-12-31")), "2026-M12");
});
