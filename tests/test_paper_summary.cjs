'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {summarize,describe,fetchSummary}=require('../docs/paper-summary.js');

function report(){
  return {asof_utc:'2026-11-02T23:33:35+00:00',v3_papers_with_stake:0,
    accounts:Object.fromEntries(Array.from({length:12},(_,i)=>['wallet_'+i,{open:[6,9,10].includes(i)?7:8}])),
    market_research:{experimental_account:{created:true,open:8}},
    aggregate_risk:{event_position_counts:Object.fromEntries([13,13,13,13,13,9,12,1,1,13].map((n,i)=>['event_'+i,n]))}};
}

test('scanner zero does not hide 93 wallet positions plus 8 experimental positions',()=>{
  const x=summarize(report());
  assert.equal(x.open,101);assert.equal(x.wallets,13);assert.equal(x.experimentalOpen,8);
  assert.equal(x.distinctEvents,10);
  assert.match(describe(x),/101 posições PAPER abertas em 13 carteiras/);
  assert.match(describe(x),/10 jogos distintos/);
  assert.match(describe(x),/02\/11\/26, 23:33/);
});

test('genuine empty wallets display zero while an uncreated account adds no wallet',()=>{
  const r=report();Object.values(r.accounts).forEach(a=>a.open=0);
  r.market_research.experimental_account={created:false,open:0};r.aggregate_risk.event_position_counts={};
  assert.equal(summarize(r).open,0);assert.equal(summarize(r).wallets,12);
  assert.equal(summarize(r).distinctEvents,0);
});

test('missing data and invalid counts are unavailable instead of a fabricated zero',()=>{
  for(const invalid of [null,{},Object.assign(report(),{accounts:{}}),Object.assign(report(),{market_research:{}})])
    assert.throws(()=>summarize(invalid));
  for(const value of [undefined,null,'8',-1,NaN,1.5]){
    const r=report();r.accounts.wallet_0.open=value;assert.throws(()=>summarize(r));
  }
  const r=report();r.market_research.experimental_account={created:false,open:8};
  assert.throws(()=>summarize(r),/inconsistente/);
});

test('unreconciled event metadata does not become a misleading independent-game count',()=>{
  const r=report();r.aggregate_risk.event_position_counts={wrong_count:2};
  const summary=summarize(r);assert.equal(summary.open,101);assert.equal(summary.distinctEvents,null);
  assert.doesNotMatch(describe(summary),/jogos distintos/);
});

test('a missing report timestamp is not presented as current data',()=>{
  for(const asof_utc of [undefined,'bad-date',null])assert.throws(()=>summarize(Object.assign(report(),{asof_utc})));
});

test('wallet fetching uses the wallet report with a cache-busting query and no-store',async()=>{
  const calls=[];
  const x=await fetchSummary('https://example.invalid/strategy_lab_latest.json',async(url,options)=>{
    calls.push({url,options});return {ok:true,json:async()=>report()};
  });
  assert.equal(x.open,101);assert.match(calls[0].url,/strategy_lab_latest\.json\?t=\d+$/);
  assert.equal(calls[0].options.cache,'no-store');
});

test('HTTP failure and invalid JSON cannot fall back to scanner zero',async()=>{
  await assert.rejects(fetchSummary('SYNTHETIC',async()=>({ok:false,status:503})),/HTTP 503/);
  await assert.rejects(fetchSummary('SYNTHETIC',async()=>({ok:true,json:async()=>{throw Error('SYNTHETIC_RAW_BODY')}})),/Relatório das carteiras inválido/);
});
