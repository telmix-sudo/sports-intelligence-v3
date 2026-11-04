const test=require('node:test'),assert=require('node:assert/strict');
const view=require('../docs/validation-progress.js');
test('Progress states exact market line and separate future evidence',()=>{
 const row={family:'NBA-MODEL',sport:'nba',market:'ML',role:'CALIBRATION',events:2,required_events:100,dates:1,required_dates:14,remaining_events:98,remaining_dates:13,eligible:false,blocks:['NEEDS_100_DISTINCT_MARKET_EVENTS']};
 assert.deepEqual(view.columns(row).slice(3,7),['Ajuste histórico','2 / 100','1 / 14','98 jogos · 13 datas']);
 assert.match(view.columns(row)[7],/odds verificadas/);
 assert.match(view.market({market:'STAT_hash',market_spec:{metric:'corners',line:9.5,period:'REGULATION',team:'HOME'}}),/Cantos · 9.5 · HOME · REGULATION/);
});
test('Missing freshness never appears as fresh and stale sources disclose blocking',()=>{
 assert.match(view.runtime(null),/próximo relatório/);
 assert.match(view.runtime({components:{scanner:{status:'STALE',age_minutes:180}},needs_scanner:true,maximum_age_minutes:90,scanner_recovery_outcome:'failure'}),/Novas posições bloqueadas.*falhou/);
});
