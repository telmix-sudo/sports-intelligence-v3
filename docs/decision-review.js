"use strict";
async function renderDecisionReview(ref) {
  const panel = document.getElementById("decisionReview");
  const body = panel.querySelector("tbody");
  const summary = panel.querySelector("[data-summary]");
  const forward = panel.querySelector("[data-forward]");
  try {
    const url = "https://raw.githubusercontent.com/telmix-sudo/sports-intelligence-v3/" + ref + "/reports/decision_review_latest.json";
    const response = await fetch(url, {cache:"no-store", signal:AbortSignal.timeout(20000)});
    if (!response.ok) throw Error("Relatório indisponível");
    const data = await response.json();
    const first = data.first_decisions;
    summary.textContent = data.all_positions.positions + " posições nas 13 carteiras correspondem a " + first.positions +
      " decisões distintas em " + first.events + " jogos. Para cada jogo, mercado, linha e seleção, conta a primeira entrada registada. Com 1 € fictício por decisão liquidada, o resultado seria " + fmt(first.unit_pnl) + " €.";
    const groups = [["Meta diária","daily"],["Valor validado à entrada","validated"],["Carteira de referência","reference"],["Outras experiências e histórico","other"]];
    body.replaceChildren(...groups.map(([name,key]) => {
      const g = data.by_purpose[key], row = document.createElement("tr");
      for (const value of [name,g.open,g.settled,g.settled ? fmt(g.pnl_eur)+" €" : "—",fmt(g.roi_pct)]) row.append(cell(value));
      return row;
    }));
    const nhl = data.forward.nhl, football = data.forward.football;
    forward.textContent = "Desde " + datePT(data.forward_start_utc) + " · NHL, vencedor: " + nhl.settled + "/" + nhl.events +
      " jogos liquidados. Modelo " + fmt(nhl.original_units) + " €, favorito do mercado " + fmt(nhl.market_units) +
      " €. Futebol 1X2: " + football.settled + "/" + football.events + " jogos liquidados. Estratégia original " +
      fmt(football.original_units) + " €, variante com odds abaixo de 3,00 " + fmt(football.filtered_units) +
      " €. Seleções a partir de 3,00 em observação: " + football.observed_only + ". Comparações teóricas a 1 € por seleção.";
  } catch {
    body.replaceChildren();
    summary.textContent = "O relatório de decisões está temporariamente indisponível.";
    forward.textContent = "";
  }
}
