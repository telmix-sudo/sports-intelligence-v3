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
const api={profile,rows,describe};if(typeof module==='object'&&module.exports)module.exports=api;else root.V3DailyMinimum=api;
})(typeof globalThis!=='undefined'?globalThis:this);
