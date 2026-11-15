(function(root){
'use strict';
const labels={VALUE_GAP:'Diferença para o mercado',OPPOSITE_VALUE_GAP:'Escolha contrária',MODEL_FAVOURITE:'Favorito do modelo',MARKET_FAVOURITE:'Favorito do mercado',VALIDATED_MODEL_OR_MARKET_FAVOURITE:'Modelo validado ou favorito do mercado'};
function profile(v){return labels[v]||v||'—';}
function rows(data){return Object.entries(data?.wallets||{}).map(([name,w])=>[name.replaceAll('_',' '),Number.isSafeInteger(w.entries)?w.entries:'—',data.minimum_per_wallet,Number.isSafeInteger(w.exploratory_today)?w.exploratory_today:'—',profile(w.profile),Number.isSafeInteger(w.entries)?w.entries>=data.minimum_per_wallet?'Mínimo atingido':Math.max(0,data.minimum_per_wallet-w.entries)+' por abrir':'Contagem indisponível']);}
function describe(data){
 if(!data?.wallets)return 'O próximo relatório apresentará a execução do mínimo diário.';
 const values=Object.values(data.wallets),complete=values.filter(w=>Number.isSafeInteger(w.entries)&&w.entries>=data.minimum_per_wallet).length;
 const stake=Number.isFinite(data.exploratory_stake_eur)?data.exploratory_stake_eur.toLocaleString('pt-PT',{minimumFractionDigits:2})+' €':'valor indisponível';
 return 'Dia '+data.local_date+': mínimo atingido em '+complete+' / '+data.expected_wallets+' carteiras. Testes diários com '+stake+' fictícios por entrada. '+(data.selection_guard?'Sem modelo validado, usa-se o favorito do mercado. ':'')+(complete===data.expected_wallets?'':'Impedimentos: '+(Object.entries(data.blocks||{}).map(([k,v])=>k+': '+v).join(' · ')||'a verificar'));
}
function comparisonRows(report){
 if(!report?.total?.registered)return [];
 const pnl=new Intl.NumberFormat('pt-PT',{style:'currency',currency:'EUR'});
 const scores=(rule,n)=>n?[rule.wins,rule.losses,pnl.format(rule.net_eur)]:['—','—','—'];
 return [['Total',report.total],...Object.entries(report.wallets)].map(([name,w])=>[
  name.replaceAll('_',' '),w.settled,w.pending,...scores(w.market,w.settled),...scores(w.mixed,w.settled)
 ]);
}
function comparisonStatus(report){
 if(!report?.total)return 'A aguardar o primeiro relatório com o teste.';
 const t=report.total;
 if(!t.registered)return 'O teste está ativo. A primeira comparação será registada na próxima entrada elegível.';
 return 'Entradas acompanhadas ('+t.registered+') · Jogos distintos com resultado ('+t.distinct_settled_games+
  ') · Jogos com escolhas diferentes ('+t.distinct_changed_games+') · Entradas anuladas ('+t.voided+').';
}
const api={profile,rows,describe,comparisonRows,comparisonStatus};if(typeof module==='object'&&module.exports)module.exports=api;else root.V3DailyMinimum=api;
})(typeof globalThis!=='undefined'?globalThis:this);
