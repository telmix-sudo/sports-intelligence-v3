"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { localDay, bounds, summarize, totalBalance, experimentalRows, fetchReport } = require("../docs/balance.js");
const ASOF = "2026-11-02T12:00:00Z";
function trade(id, status, pnl, settlement) {
  return { id, status, stake_eur: 5, pnl_eur: pnl, settlement_utc: settlement, forecast_utc: "2026-10-20T12:00:00Z" };
}
const rows = [trade("prior", "SETTLED", -5, "2026-10-30T20:00:00Z"),
  trade("win", "SETTLED", 4.25, "2026-11-01T10:00:00Z"),
  trade("loss", "SETTLED", -2.5, "2026-11-01T20:00:00Z"),
  trade("void", "VOID", 0, "2026-11-01T21:00:00Z"), trade("open", "OPEN")];
const account = { bankroll_initial_eur: 1000, bankroll_eur: 996.75, open: 1, settled: 3, voided: 1 };
const created = "2026-10-18T12:00:00Z";

test("day, Monday week and month bounds include year changes and leap days", () => {
  assert.deepEqual(bounds("day", "2028-02-29"), { start: "2028-02-29", end: "2028-03-01" });
  assert.deepEqual(bounds("week", "2027-01-03"), { start: "2026-12-28", end: "2027-01-04" });
  assert.deepEqual(bounds("month", "2026-12"), { start: "2026-12-01", end: "2027-01-01" });
  assert.throws(() => bounds("day", "2026-02-30"));
});
test("Portugal calendar days use summer time and its autumn transition", () => {
  assert.equal(localDay("2026-07-01T23:30:00Z"), "2026-07-02");
  assert.equal(localDay("2026-10-24T23:30:00Z"), "2026-10-25");
  assert.equal(localDay("2026-10-25T23:30:00Z"), "2026-10-25");
  assert.throws(() => localDay("2026-11-01T12:00:00"));
});
test("gains/losses are net PnL; open stakes and void refunds do not change balances", () => {
  const result = summarize(rows, account, created, bounds("day", "2026-11-01"), ASOF);
  assert.equal(result.opening, 995);
  assert.equal(result.gains, 4.25);
  assert.equal(result.losses, 2.5);
  assert.equal(result.net, 1.75);
  assert.equal(result.closing, 996.75);
  assert.deepEqual([result.wins, result.defeats, result.voided, result.open], [1, 1, 1, 1]);
});
test("a day with no settlements carries forward the confirmed opening balance", () => {
  const result = summarize(rows, account, created, bounds("day", "2026-10-31"), ASOF);
  assert.equal(result.opening, 995);
  assert.equal(result.closing, 995);
  assert.equal(result.net, 0);
});
test("weekly and monthly movements reconcile with the complete ledger", () => {
  const week = summarize(rows, account, created, bounds("week", "2026-11-01"), ASOF);
  assert.equal(week.opening, 1000); assert.equal(week.net, -3.25); assert.equal(week.closing, 996.75);
  const month = summarize(rows, account, created, bounds("month", "2026-11"), ASOF);
  assert.equal(month.opening, 995); assert.equal(month.net, 1.75);
  assert.equal(summarize(rows, account, created, bounds("all"), ASOF).closing, account.bankroll_eur);
});
test("future periods and periods before wallet creation have no invented balance", () => {
  assert.equal(summarize(rows, account, created, bounds("day", "2026-11-03"), ASOF).unavailable, "future");
  assert.equal(summarize(rows, account, created, bounds("day", "2026-10-17"), ASOF).closing, null);
});
test("missing settlement dates, duplicate rows and mixed snapshots fail closed", () => {
  assert.throws(() => summarize([...rows, rows[0]], account, created, bounds("all"), ASOF), /DUPLICATE/);
  assert.throws(() => summarize([{ ...rows[0], settlement_utc: undefined }], account, created, bounds("all"), ASOF), /TIMESTAMP/);
  assert.throws(() => summarize(rows, { ...account, bankroll_eur: 1000 }, created, bounds("all"), ASOF), /BALANCE_MISMATCH/);
  assert.throws(() => summarize(rows, { ...account, open: 2 }, created, bounds("all"), ASOF), /COUNT_MISMATCH/);
  assert.throws(() => summarize(rows, account, created, bounds("all"), "2026-11-01T09:00:00Z"), /SNAPSHOT_CHANGED/);
});
test("four decimal PnL values are summed without floating point drift", () => {
  const small = [trade("one", "SETTLED", .1, "2026-11-01T01:00:00Z"), trade("two", "SETTLED", .2, "2026-11-01T02:00:00Z")];
  const result = summarize(small, { bankroll_initial_eur: 1000, bankroll_eur: 1000.3 }, created, bounds("all"), ASOF);
  assert.equal(result.net, .3); assert.equal(result.closing, 1000.3);
});
test("totals follow the selected period and wallet without including unavailable balances", () => {
  const first = summarize(rows, account, created, bounds("day", "2026-11-01"), ASOF);
  const second = summarize([], { bankroll_initial_eur: 1000, bankroll_eur: 1000 }, created, bounds("day", "2026-11-01"), ASOF);
  const unavailable = summarize([], { bankroll_initial_eur: 1000, bankroll_eur: 1000 }, "2026-11-02T01:00:00Z", bounds("day", "2026-11-01"), ASOF);
  assert.deepEqual(totalBalance([first, second, unavailable]), {
    available: 2, wallets: 3, opening: 1995, gains: 4.25, losses: 2.5, net: 1.75, closing: 1996.75
  });
  assert.equal(totalBalance([first]).closing, 996.75);
  const week = summarize(rows, account, created, bounds("week", "2026-11-01"), ASOF);
  assert.equal(totalBalance([week, second]).net, -3.25);
  assert.equal(totalBalance([unavailable]).closing, null);
  assert.equal(totalBalance([]).gains, null);
});
test("totals retain the ledger's four decimal precision across wallets", () => {
  const first = { opening: 1000, gains: .1, losses: 0, net: .1, closing: 1000.1 };
  const second = { opening: 1000, gains: .2, losses: 0, net: .2, closing: 1000.2 };
  assert.equal(totalBalance([first, second]).net, .3);
  assert.equal(totalBalance([first, second]).closing, 2000.3);
});
test("experimental account joins append-only settlements and excludes zero-stake observations", () => {
  const journal = [{ id: "F1", kind: "FORECAST", stake_eur: 2.5, forecast_utc: created },
    { id: "F0", kind: "FORECAST", stake_eur: 0 },
    { id: "S1", kind: "SETTLEMENT", forecast_id: "F1", status: "SETTLED", pnl_eur: 1.25, settlement_utc: "2026-11-01T20:00:00Z" }];
  const normalized = experimentalRows(journal);
  assert.equal(normalized.length, 1);
  assert.equal(summarize(normalized, { bankroll_initial_eur: 1000, bankroll_eur: 1001.25 }, created, bounds("all"), ASOF).net, 1.25);
  assert.throws(() => experimentalRows([...journal, { ...journal[2], id: "S2" }]), /DUPLICATE_SETTLEMENT/);
});

test("report and ledgers use one immutable snapshot instead of cached main files", async () => {
  const sha = "a".repeat(40), calls = [], report = { asof_utc: ASOF };
  const reader = async url => {
    calls.push(url);
    return { ok: true, json: async () => calls.length === 1 ? { sha } : report };
  };
  const snapshot = await fetchReport(reader);
  assert.equal(snapshot.ref, sha);
  assert.equal(snapshot.report, report);
  assert.match(calls[1], new RegExp("/" + sha + "/reports/strategy_lab_latest\\.json"));
});

test("public metadata limits retain the reconciled main fallback without extra authorization", async () => {
  const calls = [];
  const snapshot = await fetchReport(async url => {
    calls.push(url);
    return calls.length === 1 ? { ok: false } : { ok: true, json: async () => ({ asof_utc: ASOF }) };
  });
  assert.equal(snapshot.ref, "main");
  assert.match(calls[1], /\/main\/reports\/strategy_lab_latest\.json/);
});
