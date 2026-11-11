const test=require('node:test'),assert=require('node:assert/strict');
const view=require('../docs/selection-experiment.js');
test('Zero results remain unavailable and zero-stake samples never become wallet balances',()=>{
 const t={forecasts_total:1,distinct_events:1,settled_total:0,voided_total:0,pending_total:1,minimum_events:100,minimum_dates:14,streams:[{family:'M',sport:'nba',market:'ML',settled:0,dates:0,pending:1,status:'COLLECTING',arms:{VALUE_GAP:{n:0,wins:0,unit_pnl_eur:0,roi_pct:null}}}]};
 assert.match(view.intro(t),/não alteram saldos/);
 assert.deepEqual(view.cohortRows(t)[0].slice(7,10),['—','—','—']);
 assert.equal(view.cohortRows(t,'nhl').length,0);
});
test('Policy choices show original probabilities for the actual selected outcome',()=>{
 const row={home:'Home',away:'Away',market:'ML',selection_trace:{arms:{VALUE_GAP:{selection_index:0,model_probability:.45,market_probability:.2,odds:4.9},OPPOSITE_VALUE_GAP:{selection_index:1,model_probability:.55,market_probability:.8,odds:1.25}}}};
 const rows=view.choiceRows({latest_choices:[row]});
 assert.equal(rows[0][3],'Home');assert.equal(rows[1][3],'Away');
 assert.match(rows[0][4],/45/);assert.match(rows[1][5],/80/);
});
test('Audit explicitly separates positions, distinct games and incomplete verification',()=>{
 const t=view.audit({models:{wins:37,losses:162,positions:199,distinct_events:23},shared_auxiliary_positions:116,shared_auxiliary_distinct_events:11,arithmetic_rows_checked:240,issues:[],official_verification:{verified_model_events:12,model_events_total:23,pending_model_events:11}});
 assert.match(t,/199 posições, mas apenas 23 jogos/);assert.match(t,/12 \/ 23/);assert.match(t,/por concluir/);
});
test('Value shortlist shows the price and excludes expired selections',()=>{
 const forecast_utc='2026-11-11T09:00:00Z',valid_until_utc='2026-11-11T10:00:00Z';
 const shortlist={rows:[{home:'Casa',away:'Fora',market:'1X2',selection_index:1,odds:4,
  model_probability:.34,conservative_ev:.12,family:'MODELO',forecast_utc,valid_until_utc}]};
 const [row]=view.valueRows(shortlist,Date.parse(forecast_utc));
 assert.equal(row[2],'Empate');assert.equal(row[5],'25,0%');assert.equal(row[6],'12,0%');
 assert.deepEqual(view.valueRows(shortlist,Date.parse(valid_until_utc)),[]);
});
