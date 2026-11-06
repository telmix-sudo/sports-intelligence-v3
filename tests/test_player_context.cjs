const test=require('node:test'),assert=require('node:assert/strict');
const v=require('../docs/player-context.js');
test('Missing player data and probabilities never become healthy players or zero probabilities',()=>{
 const r=v.rows({events:[{home:'A',away:'B',sport:'nba',status:'NO_CONTEXT',candidate_probability:null}]})[0];
 assert.equal(r[6],'Indisponível');assert.equal(r[7],'Indisponível');assert.equal(r[4],'—');
 assert.equal(v.names({injury_report:false,players:{}},'OUT'),'Não publicado');
 assert.equal(v.names({injury_report:true,players:{}},'OUT'),'Nenhum reportado');
});
test('A fitted shadow model is distinguished from actual selection influence',()=>{
 const e={home:'A',away:'B',sport:'nba',status:'SHADOW_MODEL',base_probability:[.5,.5],candidate_probability:[.6,.4],influences_daily_selection:false};
 const r=v.rows({events:[e]})[0];assert.match(r[7],/60.0%/);assert.equal(r[9],'Sem influência nas escolhas');
 assert.match(v.summary(null),/aguardar/);
});
