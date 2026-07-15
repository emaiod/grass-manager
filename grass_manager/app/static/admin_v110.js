const originalModalV110=modal;
modal=function(title,html,onSave){
  originalModalV110(title,html,onSave);
  const dlg=$('#modal');
  dlg.classList.toggle('modal-wide',title.startsWith('Storico pagamenti'));
  dlg.querySelectorAll('[data-modal-cancel]').forEach(btn=>btn.onclick=()=>dlg.close());
};

async function refreshPlantV110(sub){
  currentDetails=await api(`api/plants/${currentPlant.id}/details`);
  renderSub(sub);
}

loadPlants=async function(){
  plants=await api('api/plants');
  $('#plantsList').innerHTML=plants.map(p=>`<article class="card"><div class="status-row"><div><h3>${esc(p.name)}</h3><p>${esc(p.customer_name||'')}</p><p class="small">${esc(p.address||'')}</p></div><span class="badge ${p.enabled?'ok':'error'}">${p.enabled?'ATTIVO':'DISABILITATO'}</span></div><div class="actions"><button onclick="openPlant(${p.id})">Gestisci</button>${me.global_role==='admin'?`<button class="danger" onclick="deletePlantV110(${p.id})">Elimina</button>`:''}</div></article>`).join('')||'<p>Nessun impianto.</p>';
};

window.deletePlantV110=async function(id){
  const p=plants.find(x=>x.id===id);
  if(!p||!confirm(`Eliminare definitivamente l’impianto ${p.name}? Verranno eliminate anche zone, programmi, storico e assegnazioni.`))return;
  try{await api(`api/plants/${id}`,{method:'DELETE'});await loadPlants();await loadDashboard()}catch(e){alert(e.message)}
};

const originalOpenPlantV110=openPlant;
openPlant=async function(id){
  await originalOpenPlantV110(id);
  if(me.global_role==='admin'){
    const toolbar=$('#plantHeader .toolbar');
    if(toolbar&&!toolbar.querySelector('.delete-plant-detail')){
      const btn=document.createElement('button');btn.className='danger delete-plant-detail';btn.textContent='Elimina impianto';btn.onclick=()=>deletePlantV110(id);toolbar.appendChild(btn);
    }
  }
};
window.openPlant=openPlant;

renderSub=function(name){
  $$('.tabs button').forEach(x=>x.classList.toggle('active',x.dataset.sub===name));
  const d=currentDetails;
  if(name==='overview'){
    $('#plantSubContent').innerHTML=`<div class="stats two-stats"><div class="stat"><strong>${d.zones.length}</strong><span>Zone</span></div><div class="stat"><strong>${d.programs.length}</strong><span>Programmi</span></div></div><div class="panel"><h3>Dati impianto</h3><p>${esc(d.plant.customer_name||'Nessun cliente')} · ${esc(d.plant.address||'Nessun indirizzo')}</p><p class="small">Stato: ${d.plant.enabled?'attivo':'disabilitato'} · Fuso orario: ${esc(d.plant.timezone)}</p></div>`;
    return;
  }
  if(name==='zones')renderZonesV110();
  if(name==='programs')renderProgramsV110();
};

function renderZonesV110(){
  $('#plantSubContent').innerHTML=`<div class="toolbar"><div><h3>Zone</h3><p class="small">Valvole e sensori vengono associati automaticamente all’impianto.</p></div><button onclick="zoneModalV110()">Nuova zona</button></div><div class="list">${currentDetails.zones.map(z=>`<div class="list-item"><div><b>${esc(z.name)}</b><div class="small">${esc(z.valve_entity)}${z.moisture_entity?` · umidità: ${esc(z.moisture_entity)}`:''} · max ${z.max_minutes} min</div></div><button class="danger" onclick="deleteZoneV110(${z.id})">Elimina</button></div>`).join('')||'<p>Nessuna zona configurata.</p>'}</div>`;
}

window.zoneModalV110=async function(){
  await ensureEntities();
  const valves=entities.filter(e=>['switch','valve','input_boolean'].includes(e.domain));
  const sensors=entities.filter(e=>e.domain==='sensor');
  modal('Nuova zona',`<div class="form-grid compact-form"><label>Nome<input name="name" required></label><label>Valvola<select name="valve_entity" required>${valves.map(e=>`<option value="${esc(e.entity_id)}">${esc(e.name)} · ${esc(e.entity_id)}</option>`).join('')}</select></label><label>Sensore umidità<select name="moisture_entity"><option value="">Nessuno</option>${sensors.map(e=>`<option value="${esc(e.entity_id)}">${esc(e.name)}</option>`).join('')}</select></label><label>Soglia umidità<input name="moisture_max" type="number" step="0.1"></label><label>Durata massima<input name="max_minutes" type="number" value="60" min="1"></label></div>`,async o=>{await api(`api/plants/${currentPlant.id}/zones`,{method:'POST',body:JSON.stringify({...o,moisture_entity:o.moisture_entity||null,moisture_max:o.moisture_max?Number(o.moisture_max):null,max_minutes:Number(o.max_minutes)})});await refreshPlantV110('zones')});
};

window.deleteZoneV110=async function(id){
  const z=currentDetails.zones.find(x=>x.id===id);if(!z||!confirm(`Eliminare la zona ${z.name}? Sarà rimossa anche dai programmi che la utilizzano.`))return;
  try{await api(`api/plants/${currentPlant.id}/zones/${id}`,{method:'DELETE'});await refreshPlantV110('zones')}catch(e){alert(e.message)}
};

function renderProgramsV110(){
  $('#plantSubContent').innerHTML=`<div class="toolbar"><div><h3>Programmi</h3><p class="small">La pompa e il meteo vengono associati automaticamente quando selezionati.</p></div><button onclick="programModal()">Nuovo programma</button></div><div class="grid">${currentDetails.programs.map(p=>{const s=programStatus(p),starts=[...p.start_times,p.solar_event?`${p.solar_event==='sunrise'?'Alba':'Tramonto'} ${p.solar_offset>=0?'+':''}${p.solar_offset} min`:null].filter(Boolean);return `<article class="card"><span class="badge ${s.cls} program-badge">${s.badge}</span><h3>${esc(p.name)}</h3><div class="schedule ${s.badge==='ERRORE'?'error':''}"><b>${esc(s.msg)}</b><div class="small">Giorni: ${p.weekdays.map(i=>dayNames[i]).join(' · ')||'nessuno'}</div><div class="small">Partenze: ${starts.join(' · ')||'nessuna'}</div></div><p>${p.steps.map(x=>`${esc(x.zone_name)}: ${x.duration_minutes} min`).join('<br>')}</p><div class="actions"><button onclick="startProgram(${p.id})">Avvia</button><button class="danger" onclick="deleteProgramV110(${p.id})">Elimina</button></div></article>`}).join('')||'<p>Nessun programma configurato.</p>'}</div>`;
}

window.deleteProgramV110=async function(id){
  const p=currentDetails.programs.find(x=>x.id===id);if(!p||!confirm(`Eliminare il programma ${p.name}?`))return;
  try{await api(`api/plants/${currentPlant.id}/programs/${id}`,{method:'DELETE'});await refreshPlantV110('programs')}catch(e){alert(e.message)}
};
