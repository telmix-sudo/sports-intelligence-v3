(function(root){
'use strict';
const arms={VALUE_GAP:'Diferença para o mercado',OPPOSITE_VALUE_GAP:'Escolha contrária',MODEL_FAVOURITE:'Favorito do modelo',MARKET_FAVOURITE:'Favorito do mercado'};
const statuses={SOURCE_VERSION_RETIRED:'Versão anterior; recolha encerrada',COLLECTING:'A recolher',WAITING_RESULTS:'A aguardar resultados',REVIEW_REQUIRED:'Amostra fechada; exige revisão',COHORT_RESOLVED_INSUFFICIENT_DECISIVE_DATA:'Amostra fechada; dados decisivos insuficientes'};
const number=(v,d=2)=>Number.isFinite(v)?v.toLocaleString('pt-PT',{minimumFractionDigits:d,maximumFractionDigits:d}):'—';
function audit(a){
 if(!a)return 'A auditoria das escolhas será publicada na próxima execução.';
 const m=a.models||{},v=a.official_verification||{};
 return 'Modelos: '+m.wins+' vitórias e '+m.losses+' derrotas em '+m.positions+' posições, mas apenas '+m.distinct_events+' jogos distintos. '+a.shared_auxiliary_positions+' posições copiaram previsões auxiliares em '+a.shared_auxiliary_distinct_events+' jogos. Verificação dos pagamentos: '+a.arithmetic_rows_checked+' registos; '+(a.issues||[]).length+' divergências. Resultados consultados por ID e equipas na fonte: '+(v.verified_model_events??0)+' / '+(v.model_events_total??0)+' jogos dos modelos. '+(v.pending_model_events?'Existem verificações por concluir.':'');
}
function intro(t){
 if(!t)return 'A aguardar o início do teste prospetivo.';
 return 'Teste sem stake: '+t.forecasts_total+' previsões emparelhadas em '+t.distinct_events+' jogos distintos; '+t.settled_total+' avaliadas, '+t.voided_total+' anuladas e '+t.pending_total+' pendentes. Cada grupo exige pelo menos '+t.minimum_events+' jogos e '+t.minimum_dates+' datas. Estas observações não alteram saldos; as apostas PAPER da meta diária são registadas separadamente nas carteiras. O resultado em euros simula 1 € por seleção às odds guardadas, antes de comissões e execução.';
}
function cohortRows(trial,sport='ALL'){
 return (trial?.streams||[]).filter(s=>sport==='ALL'||s.sport===sport).flatMap(s=>Object.entries(s.arms).map(([arm,a])=>[
 s.family+(s.model_code_sha256?' · '+s.model_code_sha256.slice(0,6):''),s.sport.toUpperCase(),root.V3ValidationProgress?root.V3ValidationProgress.market(s):s.market,arms[arm]||arm,
 s.settled+' / '+trial.minimum_events,s.dates+' / '+trial.minimum_dates,s.pending,a.n?a.wins+' / '+(a.n-a.wins):'—',a.n?number(a.unit_pnl_eur)+' €':'—',a.n?number(a.roi_pct)+'%':'—',statuses[s.status]||s.status]));
}
function selectionName(f,i){
 if(f.market==='1X2')return [f.home,'Empate',f.away][i];
 if(f.market==='ML')return [f.home,f.away][i];
 const kind=f.market_spec?.kind;
 if(f.market==='BTTS'||kind==='BTTS')return ['Sim','Não'][i];
 if(f.market==='OVER_UNDER_2_5'||kind==='TOTAL')return ['Mais','Menos'][i];
 return ['Casa com handicap','Fora com handicap'][i];
}
function choiceRows(t){return (t?.latest_choices||[]).flatMap(f=>Object.entries(f.selection_trace.arms).map(([arm,a])=>[
 f.home+' × '+f.away,f.market,arms[arm]||arm,selectionName(f,a.selection_index),number(a.model_probability*100,1)+'%',number(a.market_probability*100,1)+'%',number(a.odds)]));}
const api={audit,intro,cohortRows,choiceRows};
if(typeof module==='object'&&module.exports)module.exports=api;else root.V3SelectionView=api;
})(typeof globalThis!=='undefined'?globalThis:this);
