'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short=v=>v?v.slice(0,8)+'…'+v.slice(-6):'Not provisioned';
const fmt=v=>v===null||v===undefined?'Unknown':Number(v).toLocaleString('en-US',{maximumFractionDigits:6});
const clock=t=>new Date(t).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit'});
let selected=['europe','us','australia'].includes(location.hash.slice(1))?location.hash.slice(1):'europe',data=null,loading=false,acting=false,lastFeed='',lastAgent='',scrollNext=false,actionError='';
function explorer(value,type,label){const pattern=type==='tx'?/^0x[0-9a-fA-F]{64}$/:/^0x[0-9a-fA-F]{40}$/;return pattern.test(value||'')?'<a href="https://explorer-sepolia.inkonchain.com/'+type+'/'+value+'" target="_blank" rel="noopener noreferrer">'+esc(label)+' ↗</a>':'<span>'+esc(label)+'</span>';}
const sources={demo_inventory:'Demo inventory',pi_ai:'Pi agent',kwal_api:'Card API',blockchain:'Blockchain',controller:'Controller'};
function render(){
 window.scoutChat?.select(selected);
 const a=data.agents.find(a=>a.id===selected),w=data.workflow,c=data.card_demo||{},p=w.payment||{};
 const price=Number(w.quote?.price_test_usdc||0),capacity=Number(c.card?.spendable_test_usdc||0),shortfall=Math.max(0,price-capacity);
 const busy=data.jobs.some(j=>['queued','running'].includes(j.status));
 $('agent-tabs').innerHTML=data.agents.map((agent,i)=>'<button class="agent-tab '+(agent.id===selected?'selected':'')+'" data-agent-tab="'+agent.id+'" aria-selected="'+(agent.id===selected)+'"><span class="tab-icon">0'+(i+1)+'</span><span><strong>'+esc(agent.name)+'</strong><small>'+({found:'Inventory found',out_of_stock:'Out of stock',watching:'Watching for stock'}[agent.inventory])+'</small><em>'+esc(agent.live?({ready:'Funded · ready to pay',discovering:'Preparing purchase',needs_funding:'Needs funding',funding:'Funding in progress',submitted:'Payment submitted',authorizing:'Authorizing',settling:'Settling',settled:'Payment settled',quote_needed:'Quote needs a check',reconcile:'Needs a check'}[w.stage]||w.stage):'Preview · card not provisioned')+'</em></span><i class="tab-dot '+agent.inventory+'"></i></button>').join('');
 document.querySelectorAll('[data-agent-tab]').forEach(b=>b.onclick=()=>{selected=b.dataset.agentTab;window.history.replaceState(null,'','#'+selected);actionError='';lastFeed='';scrollNext=true;render();});
 $('agent-name').textContent=a.name;$('agent-market').textContent=a.market+' / '+(a.live?'Live sandbox card':'Preview agent');$('agent-description').textContent=a.detail;
 $('agent-inventory-status').textContent={found:'INVENTORY FOUND',out_of_stock:'OUT OF STOCK',watching:'WATCHING'}[a.inventory];$('agent-inventory-status').className='tag '+(a.inventory==='found'?'green':'amber');
 $('inventory-image').src='assets/'+a.image;$('inventory-image').alt=a.product;$('inventory-name').textContent=a.product;
 $('inventory-detail').textContent=a.inventory==='found'?'One unit ready for the demo. Payment uses a supported substitute item.':a.detail;
 $('listing-price').textContent='S$'+a.retail_sgd;$('purchase-price').textContent=a.live?(w.quote?fmt(price)+' test USDC':'Preparing quote'):'Unavailable';
 const checkingFund=a.live&&w.stage==='reconcile'&&!p.payment_id;
 const funded=a.live&&['ready','submitted','authorizing','settling','settled','reconcile'].includes(w.stage);
 $('fund-button').textContent=checkingFund?'Check funding':a.live?'Fund '+fmt(shortfall)+' test USDC':'Fund agent';$('fund-button').hidden=funded&&!checkingFund;$('fund-button').disabled=!a.live||acting||busy||(!checkingFund&&w.stage!=='needs_funding');
 const checking=a.live&&w.stage==='reconcile'&&!!p.payment_id;
 $('pay-button').disabled=!a.live||acting||busy||(!checking&&(w.stage!=='ready'||c.stale||c.card?.funding_state!=='ready'||capacity<price));
 $('pay-button').textContent=!a.live?'Pay':checking?'Check existing payment ↗':w.stage==='settled'?'Payment settled ✓':'Pay '+fmt(price)+' test USDC ↗';
 $('prepare-button').hidden=!a.live||!['quote_needed','settled'].includes(w.stage);$('prepare-button').textContent=w.stage==='settled'?'Buy again ↗':'Check quote again';$('prepare-button').disabled=acting||busy;
 $('funding-hint').textContent=!a.live?'This preview agent has no issued card.':w.stage==='ready'?'Already funded. Fund is skipped; you can Pay.':w.stage==='settled'?'Sandbox purchase settled. One demo unit was added. Click Buy again for another purchase.':busy?'The agent is working. Follow the activity stream.':w.error||(c.stale?'Card refresh delayed. Fund will verify the current quote and balance before transferring.':'Fund adds only the amount missing from this quote.');
 $('substitute-note').textContent='Sandbox purchase uses a fixed substitute catalog item. No goods ship; inventory is simulated.';
 $('card-agent-label').textContent='SCOUT / AGENT 0'+(data.agents.indexOf(a)+1);$('card-network').textContent=a.live?'INK SEPOLIA · TEST':'PREVIEW';
 $('credit-card').classList.toggle('preview-card',!a.live);
 $('card-spendable').innerHTML=a.live?esc(fmt(c.card?.spendable_test_usdc))+' <span>test USDC</span>':'Not provisioned';
 $('live-card-state').textContent=a.live?(c.card?.status||'Checking'):'NO LIVE CARD';
 $('card-footnote').textContent=a.live?'API-reported card status. Card number is not exposed.':'This agent’s card and vault have not been created.';
 $('treasury-balance').textContent=c.wallet?fmt(c.wallet.balance_test_usdc)+' test USDC in the owner wallet':'Wallet observation pending';
 $('agent-addresses').innerHTML=a.live?'<div><small>Owner wallet · '+esc(fmt(c.wallet?.balance_test_usdc))+' test USDC</small>'+explorer(c.wallet?.address,'address',short(c.wallet?.address))+'</div><div><small>Vault · '+esc(fmt(c.vault?.balance_test_usdc))+' test USDC</small>'+explorer(c.vault?.address,'address',short(c.vault?.address))+'</div><details><summary>Full public addresses & card ID</summary><p>Owner: '+esc(c.wallet?.address)+'</p><p>Vault: '+esc(c.vault?.address)+'</p><p>Card ID: '+esc(c.card?.card_id)+'</p></details>':'<p>Owner wallet, vault and blockchain history are unavailable for this preview agent.</p>';
 $('payment-headline').textContent=!a.live?(a.inventory==='out_of_stock'?'No inventory. No payment attempted.':'Watching for stock.'):({ready:'Funded. Ready for a purchase.',discovering:'Preparing the fixed sandbox purchase…',needs_funding:'Awaiting funding. Click Fund to continue.',funding:'Funding the vault…',submitted:'Payment submitted. Waiting for authorization.',authorizing:'Card authorization recorded.',settling:'Waiting for settlement.',settled:'Sandbox payment settled.',reconcile:'Checking the existing operation.',quote_needed:'Quote preparation needs another check.'}[w.stage]||'Preparing funding.');
 $('action-status').textContent=actionError||(a.live?(w.error||'Inventory is from the demo JSON. Pi messages, card results and blockchain receipts are labelled below.'):'Preview scenario: '+a.detail);
 const events=data.events.filter(e=>e.agent_id===selected),signature=JSON.stringify(events),stream=$('activity-stream');
 if(signature!==lastFeed||lastAgent!==selected){
  const atBottom=stream.scrollHeight-stream.scrollTop-stream.clientHeight<70;
  stream.innerHTML=events.length?events.map(e=>{const facts=e.facts||{};return '<article class="stream-event '+esc(e.source)+'" data-event-id="'+e.id+'"><div class="stream-event-top"><strong>'+esc(e.actor)+(e.recipient?' <span>→ '+esc(e.recipient)+'</span>':'')+'</strong><time datetime="'+esc(e.occurred_at)+'">'+esc(clock(e.occurred_at))+'</time></div><p>'+esc(e.text)+'</p><div class="stream-evidence"><span>'+esc(sources[e.source]||e.source)+'</span>'+(facts.transaction_hash?explorer(facts.transaction_hash,'tx','View transaction'):'')+(facts.payment_id?'<code>Payment '+esc(short(facts.payment_id))+'</code>':'')+(facts.card_transaction_id?'<code>Card transaction '+esc(short(facts.card_transaction_id))+'</code>':'')+'</div>'+(facts.from?'<details><summary>Transfer addresses</summary><p>From: '+esc(facts.from)+'</p><p>To: '+esc(facts.to)+'</p></details>':'')+'</article>';}).join(''):'<div class="stream-empty">No activity recorded for this agent.</div>';
  if(atBottom||scrollNext||lastAgent!==selected)stream.scrollTop=stream.scrollHeight;
  lastFeed=signature;lastAgent=selected;scrollNext=false;
 }
 $('observation-time').textContent=c.checked_at?(c.stale?'Last observed ':'Card checked ')+clock(c.checked_at):'Waiting for card observation';
 const history=a.live?(c.chain_history||[]):[];
 $('chain-network').textContent=a.live?'INK SEPOLIA · ACTUAL TEST TRANSFERS':'NO VAULT PROVISIONED';
 $('chain-history').innerHTML=history.length?'<div class="chain-table">'+history.map(row=>'<article class="chain-row"><div><small>'+esc(clock(row.occurred_at))+' · Block '+row.block+'</small><strong>'+esc(row.direction==='incoming'?'Vault funded':'Vault transfer')+'</strong></div><div><small>Amount</small><strong>'+esc(fmt(row.amount_test_usdc))+' test USDC</strong></div><div><small>From → To</small>'+explorer(row.from,'address',short(row.from))+'<span> → </span>'+explorer(row.to,'address',short(row.to))+'</div><div>'+explorer(row.transaction_hash,'tx','View receipt')+'</div></article>').join('')+'</div>':'<div class="chain-empty">'+(a.live?'Waiting for verified transfer history. Card authorization is shown in the activity stream.':'This preview agent has no on-chain transfers.')+'</div>';
}
async function load(){if(loading)return;loading=true;try{const r=await fetch('api/agent-demo');if(!r.ok)throw Error('Agent workspace unavailable');data=await r.json();render();}catch(e){$('action-status').textContent=e.message;}finally{loading=false;}}
async function action(name){if(!data||acting)return;acting=true;actionError='';scrollNext=true;render();try{const r=await fetch('api/agent-demo',{method:'POST',headers:{'Content-Type':'application/json','X-Scout-Request':'1'},body:JSON.stringify({action:name,agent_id:selected,cycle_id:data.workflow.cycle_id})});const result=await r.json();if(!r.ok)throw Error(result.error||'Action unavailable');await load();}catch(e){actionError=e.message;$('action-status').textContent=e.message;}finally{acting=false;if(data)render();}}
$('fund-button').onclick=()=>action('fund');$('pay-button').onclick=()=>action('pay');$('prepare-button').onclick=()=>action(data?.workflow.stage==='settled'?'repurchase':'prepare');
load();setInterval(load,1000);
