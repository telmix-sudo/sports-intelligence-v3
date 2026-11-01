(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root.document) root.V3Balance = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const TZ = "Europe/Lisbon";
  const RAW = "https://raw.githubusercontent.com/telmix-sudo/sports-intelligence-v3/";
  const HEAD = "https://api.github.com/repos/telmix-sudo/sports-intelligence-v3/commits/main";
  const dayFormatter = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
  const euro = new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" });
  const dateFormatter = new Intl.DateTimeFormat("pt-PT", { timeZone: "UTC", dateStyle: "short" });

  function instant(value) {
    if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) throw Error("MISSING_TIMESTAMP");
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) throw Error("INVALID_TIMESTAMP");
    return date;
  }
  function localDay(value) {
    const date = value instanceof Date ? value : instant(value);
    const parts = Object.fromEntries(dayFormatter.formatToParts(date).map(p => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }
  function calendarDate(day) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw Error("INVALID_DATE");
    const date = new Date(day + "T00:00:00Z");
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== day) throw Error("INVALID_DATE");
    return date;
  }
  function addDays(day, count) {
    const date = calendarDate(day);
    date.setUTCDate(date.getUTCDate() + count);
    return date.toISOString().slice(0, 10);
  }
  function bounds(period, value) {
    if (period === "all") return { start: null, end: null };
    const day = period === "month" ? value + "-01" : value;
    const date = calendarDate(day);
    if (period === "day") return { start: day, end: addDays(day, 1) };
    if (period === "week") {
      const start = addDays(day, -((date.getUTCDay() + 6) % 7));
      return { start, end: addDays(start, 7) };
    }
    if (period === "month") {
      date.setUTCMonth(date.getUTCMonth() + 1);
      return { start: day, end: date.toISOString().slice(0, 10) };
    }
    throw Error("INVALID_PERIOD");
  }
  function units(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) throw Error("INVALID_AMOUNT");
    return Math.round(value * 10000);
  }
  function experimentalRows(journal) {
    const seen = new Set(), forecasts = new Map(), settlements = new Map();
    for (const row of journal) {
      if (!row.id || seen.has(row.id)) throw Error("DUPLICATE_JOURNAL_ID");
      seen.add(row.id);
      if (row.kind === "FORECAST") forecasts.set(row.id, row);
      if (row.kind === "SETTLEMENT") {
        if (settlements.has(row.forecast_id)) throw Error("DUPLICATE_SETTLEMENT");
        settlements.set(row.forecast_id, row);
      }
    }
    for (const id of settlements.keys()) if (!forecasts.has(id)) throw Error("UNKNOWN_FORECAST");
    return [...forecasts.values()].filter(row => units(row.stake_eur) > 0).map(row => {
      const settled = settlements.get(row.id);
      return { ...row, status: settled ? settled.status : "OPEN", settlement_utc: settled?.settlement_utc,
        pnl_eur: settled ? settled.pnl_eur : undefined };
    });
  }
  function summarize(rows, account, creation, range, asof) {
    const asofDate = instant(asof), asofDay = localDay(asofDate);
    const initial = units(account.bankroll_initial_eur);
    const createdDay = creation ? localDay(creation) : null;
    const seen = new Set();
    let before = 0, gains = 0, losses = 0, wins = 0, defeats = 0, voided = 0, totalPnl = 0;
    let totalSettled = 0, totalOpen = 0, totalVoided = 0;
    for (const row of rows) {
      if (!row.id || seen.has(row.id)) throw Error("DUPLICATE_LEDGER_ID");
      seen.add(row.id);
      const stake = units(row.stake_eur);
      if (stake <= 0) throw Error("INVALID_PAPER_STAKE");
      if (instant(row.forecast_utc) > asofDate) throw Error("SNAPSHOT_CHANGED");
      if (row.status === "OPEN") { totalOpen++; continue; }
      if (row.status !== "SETTLED" && row.status !== "VOID") throw Error("UNKNOWN_STATUS");
      const stamp = instant(row.settlement_utc), day = localDay(stamp), pnl = units(row.pnl_eur);
      if (stamp > asofDate) throw Error("SNAPSHOT_CHANGED");
      if (row.status === "VOID" && pnl !== 0) throw Error("INVALID_VOID");
      if (row.status === "VOID") totalVoided++; else { totalPnl += pnl; totalSettled++; }
      if (range.start && day < range.start) before += pnl;
      if ((!range.start || day >= range.start) && (!range.end || day < range.end)) {
        if (row.status === "VOID") voided++;
        else if (pnl > 0) { gains += pnl; wins++; }
        else if (pnl < 0) { losses += -pnl; defeats++; }
      }
    }
    if (initial + totalPnl !== units(account.bankroll_eur)) throw Error("BALANCE_MISMATCH");
    for (const [key, actual] of [["open", totalOpen], ["settled", totalSettled], ["voided", totalVoided]]) {
      if (account[key] !== undefined && account[key] !== actual) throw Error("COUNT_MISMATCH");
    }
    const unavailable = (range.start && range.start > asofDay) ? "future"
      : (createdDay && range.end && createdDay >= range.end) ? "not_created" : null;
    return { opening: unavailable ? null : (initial + before) / 10000,
      gains: unavailable ? null : gains / 10000, losses: unavailable ? null : losses / 10000,
      net: unavailable ? null : (gains - losses) / 10000,
      closing: unavailable ? null : (initial + before + gains - losses) / 10000,
      wins, defeats, voided, open: totalOpen, unavailable };
  }
  function money(value, signed = false) {
    if (value === null) return "—";
    const rounded = Math.abs(value) < .005 ? 0 : value;
    return (signed && rounded > 0 ? "+" : "") + euro.format(rounded);
  }
  function name(value) { return value === "EXPERIMENTAL_MULTI_MARKET" ? "Experiência multimercado" : value.replace(/_/g, " "); }
  function dayLabel(day) { return dateFormatter.format(calendarDate(day)); }
  function periodLabel(period, range, value) {
    if (period === "all") return "Todo o histórico";
    if (period === "day") return "Dia " + dayLabel(range.start);
    if (period === "week") return "Semana de " + dayLabel(range.start) + " a " + dayLabel(addDays(range.end, -1));
    return new Intl.DateTimeFormat("pt-PT", { timeZone: "UTC", month: "long", year: "numeric" }).format(calendarDate(value + "-01"));
  }
  function parseJournal(text) { return text.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line)); }

  async function fetchReport(fetcher = fetch) {
    let ref = "main";
    try {
      const head = await fetcher(HEAD + "?t=" + Date.now(), { cache: "no-store", signal: AbortSignal.timeout(8000) });
      if (head.ok) {
        const commit = await head.json();
        if (/^[a-f0-9]{40}$/.test(commit.sha)) ref = commit.sha;
      }
    } catch (_) { /* Public metadata can be unavailable; reconciliation still protects the fallback. */ }
    const response = await fetcher(RAW + ref + "/reports/strategy_lab_latest.json?t=" + Date.now(),
      { cache: "no-store", signal: AbortSignal.timeout(20000) });
    if (!response.ok) throw Error("Não foi possível carregar o relatório");
    return { report: await response.json(), ref };
  }

  function mount(container) {
    const get = id => container.querySelector("#" + id);
    const period = get("balancePeriod"), day = get("balanceDay"), month = get("balanceMonth"), wallet = get("balanceWallet");
    let state = null, generation = 0;
    day.value = localDay(new Date()); month.value = day.value.slice(0, 7);
    function settings() { return { range: bounds(period.value, period.value === "month" ? month.value : day.value) }; }
    function render() {
      get("balanceDayField").hidden = period.value === "month" || period.value === "all";
      get("balanceMonthField").hidden = period.value !== "month";
      get("balanceDayLabel").textContent = period.value === "week" ? "Um dia da semana" : "Dia";
      get("balancePrevious").disabled = get("balanceNext").disabled = period.value === "all";
      if (!state) return;
      try {
        const { range } = settings();
        const entries = state.entries.filter(item => wallet.value === "all" || item.id === wallet.value);
        const results = entries.map(item => ({ ...item, result: summarize(item.rows, item.account, item.created, range, state.asof) }));
        const fragment = document.createDocumentFragment();
        for (const item of results) {
          const tr = document.createElement("tr"), first = document.createElement("th");
          first.scope = "row"; first.className = "balance-wallet-name";
          const title = document.createElement("span"), detail = document.createElement("small");
          title.textContent = name(item.id);
          detail.textContent = item.result.unavailable === "future" ? "Período por iniciar"
            : item.result.unavailable === "not_created" ? "Carteira ainda não criada neste período"
            : item.result.wins + (item.result.wins === 1 ? " vitória · " : " vitórias · ")
              + item.result.defeats + (item.result.defeats === 1 ? " derrota · " : " derrotas · ")
              + item.result.voided + (item.result.voided === 1 ? " anulada" : " anuladas");
          first.append(title, detail); tr.append(first);
          for (const key of ["opening", "gains", "losses", "net", "closing"]) {
            const td = document.createElement("td"), value = item.result[key];
            td.textContent = money(key === "losses" && value !== null ? -value : value, key === "net");
            if (key === "gains" && value > 0 || key === "net" && value > 0) td.className = "positive";
            if (key === "losses" && value > 0 || key === "net" && value < 0) td.className = "negative";
            if (key === "net" || key === "closing") td.classList.add("number");
            tr.append(td);
          }
          fragment.append(tr);
        }
        get("balanceRows").replaceChildren(fragment);
        get("balanceCaption").textContent = periodLabel(period.value, range, month.value) + " · valores fictícios em euros";
        const singles = results.length === 1;
        get("balanceHighlights").hidden = !singles;
        get("balanceMovement").hidden = !singles;
        if (singles) {
          const result = results[0].result;
          get("balanceOpening").textContent = money(result.opening);
          get("balanceClosing").textContent = money(result.closing);
          get("balanceGains").textContent = money(result.gains);
          get("balanceLosses").textContent = money(result.losses === null ? null : -result.losses);
          const net = get("balanceNet"); net.textContent = money(result.net, true);
          net.className = result.net > 0 ? "positive" : result.net < 0 ? "negative" : "";
        }
        const date = new Intl.DateTimeFormat("pt-PT", { timeZone: TZ, dateStyle: "short", timeStyle: "short" }).format(instant(state.asof));
        get("balanceStatus").textContent = "Dados até " + date + " (Portugal) · " + results.length + (singles ? " carteira" : " carteiras") + ".";
        get("balanceOpen").textContent = singles ? results[0].result.open + " posições atualmente abertas nesta carteira, excluídas dos ganhos e perdas realizados."
          : "Cada linha apresenta uma carteira independente. Os mesmos jogos podem aparecer em várias carteiras; os saldos não são somados.";
      } catch (_) {
        get("balanceStatus").textContent = "Não foi possível confirmar este balanço. Atualiza o quadro para obter registos consistentes.";
        get("balanceRows").replaceChildren(); get("balanceHighlights").hidden = true; get("balanceMovement").hidden = true;
      }
    }
    period.addEventListener("change", render);
    for (const input of [day, month, wallet]) input.addEventListener("change", render);
    function step(direction) {
      try {
        if (period.value === "month") {
          const date = calendarDate(month.value + "-01"); date.setUTCMonth(date.getUTCMonth() + direction);
          month.value = date.toISOString().slice(0, 7);
        } else day.value = addDays(day.value, direction * (period.value === "week" ? 7 : 1));
        render();
      } catch (_) { get("balanceStatus").textContent = "Seleciona uma data válida."; }
    }
    get("balancePrevious").addEventListener("click", () => step(-1));
    get("balanceNext").addEventListener("click", () => step(1));
    get("balanceToday").addEventListener("click", () => { period.value = "day"; day.value = localDay(new Date()); month.value = day.value.slice(0, 7); render(); });
    async function read(path, stamp, ref) {
      const response = await fetch(RAW + ref + "/" + path + "?t=" + stamp, { cache: "no-store", signal: AbortSignal.timeout(20000) });
      if (!response.ok) throw Error("LOAD_FAILED");
      return parseJournal(await response.text());
    }
    async function updateReport(report, ref = "main") {
      const version = ++generation;
      if (state?.asof === report.asof_utc) { render(); return; }
      get("balanceStatus").textContent = "A confirmar os movimentos das carteiras…";
      try {
        if (ref !== "main" && !/^[a-f0-9]{40}$/.test(ref)) throw Error("INVALID_SNAPSHOT");
        const stamp = Date.now(), range = { start: null, end: null };
        const entries = await Promise.all(Object.entries(report.accounts || {}).map(async ([id, account]) => {
          if (!/^[A-Z0-9_]+$/.test(id)) throw Error("INVALID_WALLET");
          const rows = await read("data/strategy_ledgers/" + id.toLowerCase() + ".jsonl", stamp, ref);
          const created = report.registered_strategies?.[id]?.created_utc;
          summarize(rows, account, created, range, report.asof_utc);
          return { id, account, created, rows };
        }));
        if (!entries.length) throw Error("NO_WALLETS");
        const experimental = report.market_research?.experimental_account;
        if (experimental?.created) {
          const rows = experimentalRows(await read("data/market_research_v3.jsonl", stamp, ref));
          const account = { bankroll_initial_eur: experimental.initial_bankroll_eur, bankroll_eur: experimental.bankroll_eur, open: experimental.open };
          const created = rows.map(row => row.forecast_utc).sort()[0];
          summarize(rows, account, created, range, report.asof_utc);
          entries.push({ id: "EXPERIMENTAL_MULTI_MARKET", account, created, rows });
        }
        if (version !== generation) return;
        state = { asof: report.asof_utc, entries };
        const selected = wallet.value;
        const options = [new Option("Todas as carteiras", "all"), ...entries.map(item => new Option(name(item.id), item.id))];
        wallet.replaceChildren(...options);
        wallet.value = entries.some(item => item.id === selected) ? selected : "all";
        render();
      } catch (_) {
        if (version !== generation) return;
        get("balanceStatus").textContent = state ? "Não foi possível atualizar. Mantidos os dados anteriores; tenta novamente em Atualizar."
          : "Não foi possível confirmar os movimentos. Tenta novamente em Atualizar; não são apresentados valores parciais.";
      }
    }
    render();
    return { updateReport };
  }
  return { localDay, bounds, summarize, experimentalRows, fetchReport, mount };
});
