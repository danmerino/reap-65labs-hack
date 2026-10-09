(()=>{
 'use strict';
 const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let session=localStorage.getItem('scout-item-chat-session');
 if(!/^[0-9a-f-]{36}$/.test(session||'')){session=crypto.randomUUID();localStorage.setItem('scout-item-chat-session',session);}
 let agent=null,loading=false,sending=false,current=null,lastMessages='',lastSuggestions='';
 const drafts={},names={europe:'Europe agent',us:'US agent',australia:'Australia agent'};
 function render(d){
  current=d;
  const signature=JSON.stringify(d.messages),box=$('chat-messages');
  if(signature!==lastMessages){
   const atBottom=box.scrollHeight-box.scrollTop-box.clientHeight<70;
   box.innerHTML=d.messages.length?d.messages.map(m=>'<article class="chat-message '+(m.role==='user'?'from-user':'from-agent')+'"><div class="chat-message-meta"><strong>'+esc(m.role==='user'?'You':d.agent_name)+'</strong><time>'+esc(new Date(m.created_at).toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'}))+'</time></div><div class="chat-message-text">'+esc(m.text).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/^#{1,3} (.+)$/gm,'<strong class="chat-section-title">$1</strong>')+'</div>'+(m.model?'<small class="chat-message-source">Pi agent · Based on the item dossier</small>':'')+'</article>').join(''):'<div class="chat-welcome"><strong>There’s a story behind this item.</strong><p>Ask this agent about the watch’s history, the collaboration, the sourcing plan or how fast delivery could be.</p><small>Detailed watch dossier · '+d.dossier_words.toLocaleString()+' words</small></div>';
   if(atBottom||!lastMessages)box.scrollTop=box.scrollHeight;
   lastMessages=signature;
  }
  const suggestions=JSON.stringify(d.suggested_questions);
  if(suggestions!==lastSuggestions){
   $('chat-suggestions').innerHTML=d.suggested_questions.map(q=>'<button type="button" class="chat-suggestion">'+esc(q)+'</button>').join('');
   document.querySelectorAll('.chat-suggestion').forEach(b=>b.onclick=()=>{$('chat-input').value=b.textContent;$('chat-input').focus();});
   lastSuggestions=suggestions;
  }
  $('chat-send').disabled=sending||d.busy;$('chat-input').disabled=sending||d.busy;
  $('chat-status').textContent=sending?'Sending…':d.busy?'The agent is reading the dossier and replying…':'Ready to chat';
 }
 async function load(){
  if(!agent||loading)return;loading=true;const requestedAgent=agent;
  try{
   const r=await fetch('api/chat?session_id='+encodeURIComponent(session)+'&agent_id='+requestedAgent);
   if(!r.ok)throw Error('Chat is temporarily unavailable');
   const result=await r.json();if(agent===requestedAgent)render(result);
  }catch(e){if(agent===requestedAgent)$('chat-status').textContent=e.message;}
  finally{loading=false;}
 }
 window.scoutChat={select(next){
  if(agent===next)return;
  if(agent)drafts[agent]=$('chat-input').value;
  agent=next;current=null;lastMessages='';lastSuggestions='';
  $('chat-title').textContent='Chat with '+names[agent];$('chat-input').value=drafts[agent]||'';
  $('chat-messages').innerHTML='<div class="chat-welcome">Loading your conversation…</div>';
  $('chat-suggestions').innerHTML='';$('chat-status').textContent='Loading conversation…';
  $('chat-input').disabled=false;$('chat-send').disabled=false;load();
 }};
 $('chat-form').addEventListener('submit',async e=>{
  e.preventDefault();const text=$('chat-input').value.trim();if(!text||sending||current?.busy)return;
  const target=agent,pendingKey='scout-chat-pending-'+session+'-'+target;
  let previous;try{previous=JSON.parse(localStorage.getItem(pendingKey));}catch{}
  const requestId=previous?.message===text?previous.request_id:crypto.randomUUID();
  const body={session_id:session,agent_id:target,request_id:requestId,message:text};
  localStorage.setItem(pendingKey,JSON.stringify(body));sending=true;
  $('chat-send').disabled=true;$('chat-status').textContent='Sending…';
  try{
   const r=await fetch('api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-Scout-Request':'1'},body:JSON.stringify(body)});
   const response=await r.json();if(!r.ok)throw Error(response.error||'Message was not accepted');
   localStorage.removeItem(pendingKey);drafts[target]='';
   if(agent===target){$('chat-input').value='';await load();}
  }catch(error){if(agent===target)$('chat-status').textContent=error.message;}
  finally{sending=false;if(agent===target){$('chat-send').disabled=!!current?.busy;$('chat-input').disabled=!!current?.busy;}}
 });
 $('chat-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('chat-form').requestSubmit();}});
 setInterval(load,1200);
})();
