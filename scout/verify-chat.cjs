'use strict';
const {chromium}=require('/tmp/bx-pw-check/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const url='https://sshi.tail4d8e5a.ts.net:8491/dashboard/';
(async()=>{
 const browser=await chromium.launch({headless:true});
 const p=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[],posts=[],results=[];
 p.on('pageerror',e=>errors.push(e.message));
 p.on('request',r=>{if(r.method()==='POST'){posts.push(r.url());assert(r.url().endsWith('/api/chat'),'Unexpected mutation');}});
 assert.equal((await p.goto(url+'#agent-chat')).status(),200);
 await p.waitForSelector('[data-agent-tab]');
 async function ask(agent,question){
  await p.locator('[data-agent-tab="'+agent+'"]').click();
  await p.waitForFunction(()=>document.querySelector('#chat-status').textContent==='Ready to chat');
  const before=await p.locator('.from-agent').count();
  await p.locator('#chat-input').fill(question);await p.locator('#chat-send').click();
  await p.waitForFunction(n=>document.querySelectorAll('.from-agent').length>n,before,{timeout:150000});
  const reply=await p.locator('.from-agent .chat-message-text').last().textContent();
  assert(!reply.includes('could not reply'),'Worker failed: '+reply);
  assert(reply.length>300,'Substantive reply expected');
  results.push({agent,words:reply.split(/\s+/).length,reply_received:true});console.log(JSON.stringify(results.at(-1)));
  return reply;
 }
 await ask('europe','Tell me the full history of this watch, its design and the delivery plan in detail.');
 const first=await p.locator('.from-agent .chat-message-text').last().textContent();
 await p.reload();await p.waitForFunction(()=>document.querySelectorAll('.from-agent').length===1);
 assert.equal(await p.locator('.from-agent .chat-message-text').last().textContent(),first);
 await ask('europe','Following that history, explain how the fox connects to the brand, and whether delivery is guaranteed.');
 await ask('us','Why are you out of stock, and how long would delivery of the watch from the US take?');
 assert.equal(await p.locator('.from-user').count(),1);
 await ask('australia','Tell me about the matcha watch design and how fast delivery from Australia would be.');
 assert.equal(await p.locator('.from-user').count(),1);
 await p.locator('[data-agent-tab="europe"]').click();
 await p.waitForFunction(()=>document.querySelectorAll('.from-agent').length===2);
 await p.locator('#agent-chat').scrollIntoViewIfNeeded();
 await p.screenshot({path:path.join(__dirname,'chat-desktop.png'),fullPage:true});
 for(const width of [1440,1000,768,600,390,320]){
  await p.setViewportSize({width,height:900});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Overflow '+width);
 }
 await p.setViewportSize({width:390,height:844});await p.screenshot({path:path.join(__dirname,'chat-mobile.png'),fullPage:true});
 assert.equal((await p.goto('https://sshi.tail4d8e5a.ts.net:8491/dashboard/#agent-chat')).status(),200);
 await p.waitForSelector('#chat-input');assert.equal(await p.locator('[data-agent-tab]').count(),3);
 assert.deepEqual(errors,[]);
 const report={url,results,persistent_after_reload:true,isolated_agent_threads:true,widths:[1440,1000,768,600,390,320],tailscale_verified:true,no_financial_requests:true,chat_posts:posts.length,page_errors:errors};
 fs.writeFileSync(path.join(__dirname,'chat-verification.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
