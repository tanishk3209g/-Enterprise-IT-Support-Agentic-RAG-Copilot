const chat = document.getElementById('chat');
const form = document.getElementById('chatForm');
const question = document.getElementById('question');
const trace = document.getElementById('trace');
const sourceUsed = document.getElementById('sourceUsed');
const approvalCard = document.getElementById('approvalCard');
const approvalTitle = document.getElementById('approvalTitle');
const approvalCategory = document.getElementById('approvalCategory');
const approvalPriority = document.getElementById('approvalPriority');
const approvalDescription = document.getElementById('approvalDescription');
const ticketsList = document.getElementById('ticketsList');
let pendingThreadId = null;

function escapeHtml(s=''){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function formatText(s=''){return escapeHtml(s).replace(/\n/g,'<br>');}
function addMessage(role, text, source='', citations=[], verification=null, ticketId=null){
  const wrap=document.createElement('div'); wrap.className=`message ${role}`;
  const citeHtml=citations.length?`<div class="citations"><strong>Sources</strong><br>${citations.map(c=>c.url?`<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.title)}</a>`:escapeHtml(c.title)).join('<br>')}</div>`:'';
  const verificationHtml = verification ? (
    verification.passed
      ? `<div class="verification-box"><span class="verified-badge">Verified</span> Ticket #${escapeHtml(ticketId||'unknown')} | ${escapeHtml(verification.evidence?.stored?.category || '')} / ${escapeHtml(verification.evidence?.stored?.priority || '')}</div>`
      : `<div class="verification-box"><span class="failed-badge">Not verified</span> No ticket was trusted without a read-back check.</div>`
  ) : '';
  wrap.innerHTML=`<div class="avatar">AI</div><div class="bubble">${formatText(text)}${source?`<div class="answer-source">Source: ${escapeHtml(source)}</div>`:''}${verificationHtml}${citeHtml}</div>`;
  chat.appendChild(wrap); chat.scrollTop=chat.scrollHeight;
}
function renderTrace(items=[]){trace.innerHTML=items.length?items.map(x=>`<div class="trace-item">${escapeHtml(x)}</div>`).join(''):'<div class="empty">No trace.</div>'};
function renderTickets(items=[]){
  if(!items.length){ ticketsList.innerHTML='<div class="empty">No tickets yet.</div>'; return; }
  ticketsList.innerHTML = items.slice(0,5).map(t=>`<div class="ticket-item"><strong>#${escapeHtml(t.id)}</strong><div>${escapeHtml(t.title)}</div><small>${escapeHtml(t.category)} · ${escapeHtml(t.priority)}</small></div>`).join('');
}
async function loadTickets(){
  try{
    const r=await fetch('/api/tickets'); const d=await r.json(); if(!r.ok) throw new Error(d.detail||'Could not load tickets'); renderTickets(d.tickets || []);
  }catch(error){ ticketsList.innerHTML=`<div class="empty">${escapeHtml(error.message)}</div>`; }
}
function showApprovalCard(draft={}){
  approvalTitle.value = draft.title || '';
  approvalCategory.value = draft.category || 'network';
  approvalPriority.value = draft.priority || 'medium';
  approvalDescription.value = draft.description || '';
  approvalCard.classList.remove('hidden');
}
function hideApprovalCard(){ approvalCard.classList.add('hidden'); }
async function submitApproval(approved){
  if(!pendingThreadId){ return; }
  const draft = {
    title: approvalTitle.value.trim(),
    category: approvalCategory.value,
    priority: approvalPriority.value,
    description: approvalDescription.value.trim(),
  };
  const res=await fetch('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({thread_id: pendingThreadId, approved, edits: draft})});
  const data=await res.json(); if(!res.ok) throw new Error(data.detail||'Approval failed');
  hideApprovalCard();
  pendingThreadId = null;
  if(data.answer){ addMessage('assistant', data.answer, data.source_used, [], data.verification, data.ticket_id); }
  renderTrace(data.trace||[]); sourceUsed.textContent = data.source_used || 'ticket';
  if(data.ticket_id){ loadTickets(); }
  return data;
}
async function askAgent(q){
  addMessage('user',q); question.value=''; renderTrace(['Running LangGraph workflow...']); sourceUsed.textContent='Running';
  const btn=form.querySelector('button'); btn.disabled=true;
  try{
    const res=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
    const data=await res.json(); if(!res.ok) throw new Error(data.detail||'Request failed');
    if(data.status === 'needs_approval'){
      pendingThreadId = data.thread_id;
      showApprovalCard(data.ticket_draft || {});
      renderTrace(data.trace||['Waiting for approval']);
      sourceUsed.textContent='Needs approval';
      addMessage('assistant', 'Please review the ticket draft and approve or reject before creating it.', 'pending_approval');
      return;
    }
    addMessage('assistant',data.answer,data.source_used,data.citations||[], data.verification || null, data.ticket_id || null); renderTrace(data.trace||[]); sourceUsed.textContent=data.source_used;
    if(data.ticket_id){ loadTickets(); }
  }catch(e){addMessage('assistant',`Error: ${e.message}`); renderTrace(['Request failed']); sourceUsed.textContent='Error';}
  finally{btn.disabled=false;}
}
form.addEventListener('submit',e=>{e.preventDefault(); const q=question.value.trim(); if(q) askAgent(q);});
document.querySelectorAll('.example').forEach(b=>b.addEventListener('click',()=>askAgent(b.textContent.trim())));
document.getElementById('approveTicket').addEventListener('click',()=>submitApproval(true).catch(e=>{ addMessage('assistant', `Approval error: ${e.message}`); renderTrace(['Approval failed']); }));
document.getElementById('rejectTicket').addEventListener('click',()=>submitApproval(false).catch(e=>{ addMessage('assistant', `Approval error: ${e.message}`); renderTrace(['Approval failed']); }));

const modal=document.getElementById('uploadModal');
document.getElementById('openUpload').onclick=()=>modal.classList.remove('hidden');
document.getElementById('closeUpload').onclick=()=>modal.classList.add('hidden');
document.getElementById('uploadBtn').onclick=async()=>{
  const file=document.getElementById('fileInput').files[0]; const key=document.getElementById('adminKey').value; const status=document.getElementById('uploadStatus');
  if(!file){status.textContent='Choose a file first.'; return;}
  status.textContent='Indexing document...'; const fd=new FormData(); fd.append('file',file);
  try{const r=await fetch('/api/ingest',{method:'POST',headers:{'X-Admin-Key':key},body:fd}); const d=await r.json(); if(!r.ok) throw new Error(d.detail||'Upload failed'); status.textContent=`Indexed ${d.file}: ${d.chunks} chunks.`; loadTickets();}catch(e){status.textContent=`Error: ${e.message}`;}
};
loadTickets();
