(function(root){
'use strict';
const reasons={
 NEEDS_100_DISTINCT_EVENTS:'Faltam jogos futuros liquidados',
 NEEDS_100_DISTINCT_MARKET_EVENTS:'Faltam jogos futuros com odds verificadas',
 NEEDS_14_DAY_FORWARD_PERIOD:'Período de avaliação inferior a 14 dias',
 NEEDS_14_DISTINCT_MARKET_DATES:'Faltam datas distintas de avaliação',
 PAIRED_BRIER_EDGE_NOT_ESTABLISHED:'Brier ainda sem melhoria estatística sobre o mercado',
 LOG_LOSS_NOT_BETTER_THAN_MARKET:'Log Loss ainda não supera o mercado',
 CALIBRATION_ERROR_OVER_10PP:'Erro de calibração acima do limite',
 CANDIDATE_NOT_BETTER_THAN_BASE_AND_MARKET:'Ajuste ainda não supera o original e o mercado',
 INSUFFICIENT_HISTORY:'Histórico insuficiente',HOLDOUT_NOT_BETTER:'Sem melhoria no teste histórico reservado',
 CONFLICTING_HISTORY:'Resultados contraditórios no histórico',CONFLICTING_OFFICIAL_OUTCOMES:'Resultados oficiais contraditórios'
};
function market(row){
 const fixed={'1X2':'Resultado 1X2',ML:'Vencedor',BTTS:'Ambas marcam',TOTAL_2_5:'Total de golos 2,5',OVER_2_5:'Mais/menos 2,5 golos',OVER_UNDER_2_5:'Mais/menos 2,5 golos',ASIAN_HOME_MINUS_0_5:'Handicap casa −0,5'};
 if(fixed[row.market])return fixed[row.market];
 const s=row.market_spec;
 if(!s)return row.market;
 const metric={goals:'Golos',corners:'Cantos',offsides:'Foras de jogo',fouls:'Faltas',cards:'Cartões',shots:'Remates',shots_on_target:'Remates à baliza',throw_ins:'Lançamentos'}[s.metric]||s.metric;
 return [metric,s.line,s.team,s.period,s.kind].filter(v=>v!==undefined&&v!==null&&v!=='').join(' · ');
}
function columns(row){return [row.family,row.sport.toUpperCase(),market(row),row.role==='CALIBRATION'?'Ajuste histórico':'Original',row.events+' / '+row.required_events,row.dates+' / '+row.required_dates,row.remaining_events+' jogos · '+row.remaining_dates+' datas',row.eligible?'Elegível para os restantes critérios PAPER':(row.blocks||[]).map(v=>reasons[v]||v).join('; ')||'Em avaliação'];}
function runtime(status){
 if(!status)return 'Diagnóstico de atualização disponível no próximo relatório.';
 const labels={scanner:'Scanner',calendar:'Calendário',laboratory:'Laboratório'};
 const states={FRESH:'atualizado',STALE:'atrasado',UNAVAILABLE:'indisponível',FUTURE_TIMESTAMP:'hora inválida'};
 return Object.entries(status.components||{}).map(([k,v])=>labels[k]+': '+(states[v.status]||v.status)+(Number.isFinite(v.age_minutes)?' ('+Math.round(v.age_minutes)+' min)':'')).join(' · ')+'. Limite: '+status.maximum_age_minutes+' min. '+(status.needs_scanner?'Novas posições bloqueadas até atualizar scanner e calendário.':'Fontes dentro do prazo nesta execução.')+' Recuperação do scanner: '+({success:'concluída',failure:'falhou',skipped:'dispensada',not_requested:'não solicitada'}[status.scanner_recovery_outcome]||'não solicitada')+'.';
}
const api={columns,market,runtime};
if(typeof module==='object'&&module.exports)module.exports=api;
else root.V3ValidationProgress=api;
})(typeof globalThis!=='undefined'?globalThis:this);
