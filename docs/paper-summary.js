/* Funded wallet positions are separate from zero-stake scanner observations. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.V3Papers=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  function count(value){
    if(!Number.isSafeInteger(value)||value<0)throw Error('Contagem de posições indisponível');
    return value;
  }
  function summarize(report){
    const accounts=report?.accounts;
    if(!accounts||typeof accounts!=='object'||Array.isArray(accounts)||!Object.keys(accounts).length)
      throw Error('Relatório das carteiras indisponível');
    const asof=report.asof_utc;
    if(typeof asof!=='string'||!Number.isFinite(Date.parse(asof)))throw Error('Data das carteiras indisponível');
    const experimental=report.market_research?.experimental_account;
    if(!experimental||typeof experimental.created!=='boolean')throw Error('Estado da conta experimental indisponível');
    let open=Object.values(accounts).reduce((n,a)=>n+count(a?.open),0);
    const experimentalOpen=count(experimental.open);
    if(!experimental.created&&experimentalOpen!==0)throw Error('Conta experimental inconsistente');
    open=count(open+experimentalOpen);
    const wallets=Object.keys(accounts).length+(experimental.created?1:0);
    const events=report.aggregate_risk?.event_position_counts;
    let distinctEvents=null;
    if(events&&typeof events==='object'&&!Array.isArray(events)){
      const entries=Object.entries(events);
      if(entries.every(([,n])=>Number.isSafeInteger(n)&&n>=0)&&entries.reduce((n,[,v])=>n+v,0)===open)
        distinctEvents=entries.filter(([,n])=>n>0).length;
    }
    return {open,wallets,asof,experimentalOpen,distinctEvents};
  }
  function describe(summary){
    const stamp=new Intl.DateTimeFormat('pt-PT',{timeZone:'Europe/Lisbon',dateStyle:'short',timeStyle:'short'}).format(new Date(summary.asof));
    return summary.open+' posições PAPER abertas em '+summary.wallets+' carteiras'+
      (summary.distinctEvents===null?'':' · '+summary.distinctEvents+' jogos distintos')+
      '. Relatório das carteiras: '+stamp+' (Lisboa). Inclui a conta experimental quando criada; o mesmo jogo pode repetir-se entre carteiras.';
  }
  async function fetchSummary(url,fetcher){
    const response=await (fetcher||fetch)(url+'?t='+Date.now(),{cache:'no-store'});
    if(!response.ok)throw Error('HTTP '+response.status);
    let report;
    try{report=await response.json()}catch{throw Error('Relatório das carteiras inválido')}
    return summarize(report);
  }
  return {summarize,describe,fetchSummary};
});
