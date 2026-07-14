let adminUsersCache=[];
let subscriptionsCache=[];
let plansCache=[];

const originalOpenPageV041=openPage;
openPage=function(name){
  originalOpenPageV041(name);
  if(name==='subscriptions') loadSubscriptions();
};

function assignmentEditor(user={plants:[]}){
  const assigned=new Map((user.plants||[]).map(p=>[Number(p.plant_id||p.id),p.role]));
  return plants.map(p=>{
    const role=assigned.get(Number(p.id))||'owner';
    return `<div class="list-item"><label><input type="checkbox" name="plant_${p.id}" ${assigned.has(Number(p.id))?'checked':''}> ${esc(p.name)}</label><select name="role_${p.id}"><option value="owner" ${role==='owner'?'selected':''}>Proprietario</option><option value="gardener" ${role==='gardener'?'selected':''}>Giardiniere</option><option value="maintainer" ${role==='maintainer'?'selected':''}>Manutentore</option><option value="viewer" ${role==='viewer'?'selected':''}>Solo lettura</option></select></div>`;
  }).join('');
}

loadUsers=async function(){
  if(!plants.length) plants=await api('api/plants');
  adminUsersCache=await api('api/users');
  $('#usersList').innerHTML=`<div class="list">${adminUsersCache.map(u=>`<div class="list-item"><div><b>${esc(u.display_name)}</b> <span class="badge ${u.active?'ok':'error'}">${u.active?'ATTIVO':'DISATTIVATO'}</span><div class="small">${esc(u.username)} · ${esc(u.email||'nessuna email')} · ${u.plants.map(p=>`${esc(p.name)} (${esc(p.role)})`).join(', ')||'nessun impianto'}</div></div><div class="actions"><button class="secondary" onclick="editUserV041(${u.id})">Modifica</button><button class="danger" onclick="deleteUserV041(${u.id})">Elimina</button></div></div>`).join('')}</div>`;
};

window.editUserV041=function(userId){
  const u=adminUsersCache.find(x=>x.id===userId);
  if(!u) return;
  modal('Modifica utente',`<div class="form-grid"><label>Nome visualizzato<input name="display_name" value="${esc(u.display_name)}" required></label><label>Username<input name="username" value="${esc(u.username)}" required></label><label>Email<input name="email" type="email" value="${esc(u.email||'')}"></label><label>Nuova password<input name="password" type="password" minlength="8" placeholder="Lascia vuoto per non cambiarla"></label><label>Ruolo globale<select name="global_role"><option value="user" ${u.global_role==='user'?'selected':''}>Utente</option><option value="admin" ${u.global_role==='admin'?'selected':''}>Amministratore</option></select></label><label><input type="checkbox" name="active" ${u.active?'checked':''}> Account attivo</label><div class="span2"><b>Assegnazioni</b>${assignmentEditor(u)}</div></div>`,async o=>{
    const assignments=plants.filter(p=>o[`plant_${p.id}`]==='on').map(p=>({plant_id:p.id,role:o[`role_${p.id}`]}));
    await api(`api/admin/users/${userId}`,{method:'PUT',body:JSON.stringify({...o,active:o.active==='on',assignments})});
    await loadUsers();
  });
};

window.deleteUserV041=async function(userId){
  const u=adminUsersCache.find(x=>x.id===userId);
  if(!u||!confirm(`Eliminare definitivamente l'utente ${u.display_name}? Verranno rimosse anche sessioni, assegnazioni e abbonamento.`)) return;
  try{await api(`api/admin/users/${userId}`,{method:'DELETE'});await loadUsers()}catch(err){alert(err.message)}
};

function subscriptionStatusLabel(status){
  return ({trial:'PROVA',active:'ATTIVO',past_due:'SCADUTO',suspended:'SOSPESO',cancelled:'ANNULLATO',expired:'SCADUTO'})[status]||'NON CONFIGURATO';
}
function subscriptionStatusClass(status){return ['active','trial'].includes(status)?'ok':'error'}

async function loadSubscriptions(){
  [subscriptionsCache,plansCache]=await Promise.all([api('api/admin/subscriptions'),api('api/plans')]);
  const paying=subscriptionsCache.filter(u=>u.billing_required);
  const free=subscriptionsCache.filter(u=>!u.billing_required&&u.plants.length);
  $('#subscriptionPlans').innerHTML=plansCache.map(p=>`<article class="card"><div class="status-row"><div><h3>${esc(p.name)}</h3><div class="small">Codice: ${esc(p.code)}</div></div><strong>€ ${Number(p.monthly_price).toFixed(2)}/mese</strong></div><p class="small">${Object.entries(p.features||{}).filter(([,v])=>v===true).map(([k])=>esc(k)).join(' · ')||'Funzioni configurabili'}</p><button class="secondary" onclick="editPlanV041('${p.code}')">Modifica piano</button></article>`).join('');
  $('#subscriptionsList').innerHTML=`<div class="list">${paying.map(subscriptionRowV041).join('')||'<p>Nessun proprietario con abbonamento.</p>'}</div>`;
  $('#freeUsersList').innerHTML=`<div class="list">${free.map(u=>`<div class="list-item"><div><b>${esc(u.display_name)}</b><div class="small">Accesso gratuito · ${u.plants.map(p=>`${esc(p.name)} (${esc(p.role)})`).join(', ')}</div></div><span class="badge ok">GRATUITO</span></div>`).join('')||'<p>Nessun operatore gratuito assegnato.</p>'}</div>`;
}

function subscriptionRowV041(u){
  return `<div class="list-item"><div><b>${esc(u.display_name)}</b> <span class="badge ${subscriptionStatusClass(u.subscription_status)}">${subscriptionStatusLabel(u.subscription_status)}</span><div class="small">${esc(u.username)} · ${u.plants.map(p=>esc(p.name)).join(', ')||'nessun impianto'}</div><div class="small">Piano: ${esc(u.plan_name||'non configurato')} · € ${Number(u.monthly_price||0).toFixed(2)}/mese · Scadenza: ${fmtDate(u.current_period_end)}</div></div><div class="actions"><button class="secondary" onclick="editSubscriptionV041(${u.id})">Gestisci</button><button onclick="recordPaymentV041(${u.id})">Registra pagamento</button></div></div>`;
}

window.editSubscriptionV041=function(userId){
  const u=subscriptionsCache.find(x=>x.id===userId);
  if(!u) return;
  modal('Gestisci abbonamento',`<div class="form-grid"><label>Piano<select name="plan_code">${plansCache.map(p=>`<option value="${p.code}" ${p.code===u.plan_code?'selected':''}>${esc(p.name)} · € ${Number(p.monthly_price).toFixed(2)}</option>`).join('')}</select></label><label>Stato<select name="status">${['trial','active','past_due','suspended','cancelled','expired'].map(s=>`<option value="${s}" ${s===u.subscription_status?'selected':''}>${subscriptionStatusLabel(s)}</option>`).join('')}</select></label><label>Canone mensile<input name="monthly_price" type="number" step="0.01" value="${Number(u.monthly_price||0).toFixed(2)}"></label><label>Provider<input name="provider" value="${esc(u.provider||'manual')}"></label><label>Inizio periodo<input name="current_period_start" type="datetime-local" value="${toLocalInputV041(u.current_period_start)}"></label><label>Fine periodo<input name="current_period_end" type="datetime-local" value="${toLocalInputV041(u.current_period_end)}"></label><label>Grazia fino al<input name="grace_until" type="datetime-local" value="${toLocalInputV041(u.grace_until)}"></label><label><input type="checkbox" name="cancel_at_period_end" ${u.cancel_at_period_end?'checked':''}> Annulla a fine periodo</label><label class="span2">Note<textarea name="notes">${esc(u.notes||'')}</textarea></label></div>`,async o=>{
    const payload={...o,monthly_price:Number(o.monthly_price),cancel_at_period_end:o.cancel_at_period_end==='on',current_period_start:o.current_period_start||null,current_period_end:o.current_period_end||null,grace_until:o.grace_until||null};
    await api(`api/admin/users/${userId}/subscription`,{method:'PUT',body:JSON.stringify(payload)});await loadSubscriptions();
  });
};

window.recordPaymentV041=function(userId){
  const u=subscriptionsCache.find(x=>x.id===userId);
  if(!u) return;
  const start=new Date(),end=new Date(start);end.setMonth(end.getMonth()+1);
  modal('Registra pagamento',`<div class="form-grid"><label>Importo<input name="amount" type="number" step="0.01" value="${Number(u.monthly_price||0).toFixed(2)}" required></label><label>Stato<select name="status"><option value="paid">Pagato</option><option value="pending">In attesa</option><option value="failed">Fallito</option><option value="refunded">Rimborsato</option></select></label><label>Provider<input name="provider" value="manual"></label><label>Valuta<input name="currency" value="EUR"></label><label>Inizio periodo<input name="period_start" type="datetime-local" value="${toLocalInputV041(start.toISOString())}"></label><label>Fine periodo<input name="period_end" type="datetime-local" value="${toLocalInputV041(end.toISOString())}"></label></div>`,async o=>{await api(`api/admin/users/${userId}/payments`,{method:'POST',body:JSON.stringify({...o,amount:Number(o.amount),period_start:o.period_start||null,period_end:o.period_end||null})});await loadSubscriptions()});
};

window.editPlanV041=function(code){
  const p=plansCache.find(x=>x.code===code);if(!p)return;
  modal('Modifica piano',`<div class="form-grid"><label>Nome<input name="name" value="${esc(p.name)}" required></label><label>Prezzo mensile<input name="monthly_price" type="number" step="0.01" value="${Number(p.monthly_price).toFixed(2)}" required></label><label><input type="checkbox" name="manual_start" ${p.features.manual_start?'checked':''}> Avvio manuale</label><label><input type="checkbox" name="scheduling" ${p.features.scheduling?'checked':''}> Programmazioni</label><label><input type="checkbox" name="weather_automation" ${p.features.weather_automation?'checked':''}> Automazioni meteo</label><label><input type="checkbox" name="advanced_alerts" ${p.features.advanced_alerts?'checked':''}> Avvisi avanzati</label><label>Giorni storico<input name="history_days" type="number" min="0" value="${Number(p.features.history_days||0)}"></label></div>`,async o=>{const features={manual_start:o.manual_start==='on',scheduling:o.scheduling==='on',weather_automation:o.weather_automation==='on',advanced_alerts:o.advanced_alerts==='on',history_days:Number(o.history_days||0)};await api(`api/admin/plans/${code}`,{method:'PUT',body:JSON.stringify({name:o.name,monthly_price:Number(o.monthly_price),features,active:true})});await loadSubscriptions()});
};

function toLocalInputV041(value){if(!value)return'';const d=new Date(value);if(Number.isNaN(d.getTime()))return String(value).slice(0,16);const local=new Date(d.getTime()-d.getTimezoneOffset()*60000);return local.toISOString().slice(0,16)}
