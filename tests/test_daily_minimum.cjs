const test=require('node:test'),assert=require('node:assert/strict');
const view=require('../docs/daily-minimum.js');
test('Per-wallet minimum reports actual nonzero PAPER activity and profile',()=>{
 const d={local_date:'2026-11-04',minimum_per_wallet:5,expected_wallets:2,exploratory_stake_eur:.5,wallets:{ONE:{entries:5,exploratory_today:5,profile:'OPPOSITE_VALUE_GAP'},REF:{entries:10,exploratory_today:0,profile:'MARKET_FAVOURITE'}}};
 assert.match(view.describe(d),/2 \/ 2/);assert.match(view.describe(d),/0,50/);
 assert.deepEqual(view.rows(d)[0],['ONE',5,5,5,'Escolha contrária','Mínimo atingido']);
});
test('Missing or partial counts cannot become a claim that the minimum was met',()=>{
 const d={local_date:'2026-11-04',minimum_per_wallet:5,expected_wallets:3,exploratory_stake_eur:.5,wallets:{ONE:{entries:5},TWO:{entries:null}},blocks:{no_fresh_supported_forecast:1}};
 assert.match(view.describe(d),/1 \/ 3/);assert.equal(view.rows(d)[1][1],'—');assert.match(view.rows(d)[1][5],/indisponível/);
 assert.match(view.describe(null),/próximo relatório/);
});
