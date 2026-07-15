function programFormV111(program=null){
  const selectedSteps=new Map((program?.steps||[]).map(step=>[Number(step.zone_id),Number(step.duration_minutes)]));
  const weekdays=program?.weekdays||[];
  const starts=(program?.start_times||[]).join(', ');
  const pumps=entities.filter(e=>['switch','input_boolean'].includes(e.domain));
  const weathers=entities.filter(e=>e.domain==='weather');
  return `<div class="form-grid responsive-program-form">
    <label>Nome<input name="name" value="${esc(program?.name||'')}" required></label>
    <label class="inline-check"><input type="checkbox" name="enabled" ${program?.enabled===0?'':'checked'}> Abilitato</label>
    <div class="span2"><b>Giorni</b><div class="checkboxes responsive-checks">${dayNames.map((day,index)=>`<label><input type="checkbox" name="day_${index}" ${weekdays.includes(index)?'checked':''}>${day}</label>`).join('')}</div></div>
    <label class="span2">Orari separati da virgola<input name="start_times" value="${esc(starts)}" placeholder="06:00, 20:30"></label>
    <label>Evento solare<select name="solar_event"><option value="">Nessuno</option><option value="sunrise" ${program?.solar_event==='sunrise'?'selected':''}>Alba</option><option value="sunset" ${program?.solar_event==='sunset'?'selected':''}>Tramonto</option></select></label>
    <label>Offset minuti<input name="solar_offset" type="number" value="${Number(program?.solar_offset||0)}"></label>
    <label>Pompa<select name="pump_entity"><option value="">Nessuna</option>${pumps.map(e=>`<option value="${esc(e.entity_id)}" ${program?.pump_entity===e.entity_id?'selected':''}>${esc(e.name)}</option>`).join('')}</select></label>
    <label>Meteo<select name="weather_entity"><option value="">Nessuno</option>${weathers.map(e=>`<option value="${esc(e.entity_id)}" ${program?.weather_entity===e.entity_id?'selected':''}>${esc(e.name)}</option>`).join('')}</select></label>
    <label>Attesa tra zone (s)<input name="inter_zone_seconds" type="number" min="0" value="${Number(program?.inter_zone_seconds??5)}"></label>
    <label class="inline-check"><input type="checkbox" name="skip_rain" ${program?.skip_rain?'checked':''}> Salta in caso di pioggia</label>
    <div class="span2"><b>Zone</b><div class="program-zone-list">${currentDetails.zones.map(zone=>`<div class="program-zone-row"><label class="inline-check"><input type="checkbox" name="zone_${zone.id}" ${selectedSteps.has(Number(zone.id))?'checked':''}> <span>${esc(zone.name)}</span></label><label class="duration-field"><span>Durata</span><input name="duration_${zone.id}" type="number" min="1" value="${selectedSteps.get(Number(zone.id))||10}"><span>min</span></label></div>`).join('')}</div></div>
  </div>`;
}

function programPayloadV111(values){
  const weekdays=dayNames.map((_,index)=>values[`day_${index}`]==='on'?index:null).filter(value=>value!==null);
  const steps=currentDetails.zones.filter(zone=>values[`zone_${zone.id}`]==='on').map(zone=>({zone_id:zone.id,duration_minutes:Number(values[`duration_${zone.id}`])}));
  return {
    name:values.name,
    enabled:values.enabled==='on',
    weekdays,
    start_times:(values.start_times||'').split(',').map(value=>value.trim()).filter(Boolean),
    solar_event:values.solar_event||null,
    solar_offset:Number(values.solar_offset||0),
    pump_entity:values.pump_entity||null,
    weather_entity:values.weather_entity||null,
    inter_zone_seconds:Number(values.inter_zone_seconds||0),
    skip_rain:values.skip_rain==='on',
    steps
  };
}

programModal=async function(){
  await ensureEntities();
  modal('Nuovo programma',programFormV111(),async values=>{
    await api(`api/plants/${currentPlant.id}/programs`,{method:'POST',body:JSON.stringify(programPayloadV111(values))});
    await refreshPlantV110('programs');
  });
};
window.programModal=programModal;

window.editProgramV111=async function(programId){
  await ensureEntities();
  const program=currentDetails.programs.find(item=>item.id===programId);
  if(!program)return;
  modal('Modifica programma',programFormV111(program),async values=>{
    await api(`api/plants/${currentPlant.id}/programs/${programId}`,{method:'PUT',body:JSON.stringify(programPayloadV111(values))});
    await refreshPlantV110('programs');
  });
};

renderProgramsV110=function(){
  $('#plantSubContent').innerHTML=`<div class="toolbar"><div><h3>Programmi</h3><p class="small">La pompa e il meteo vengono associati automaticamente quando selezionati.</p></div><button onclick="programModal()">Nuovo programma</button></div><div class="grid">${currentDetails.programs.map(program=>{const status=programStatus(program),starts=[...program.start_times,program.solar_event?`${program.solar_event==='sunrise'?'Alba':'Tramonto'} ${program.solar_offset>=0?'+':''}${program.solar_offset} min`:null].filter(Boolean);return `<article class="card"><span class="badge ${status.cls} program-badge">${status.badge}</span><h3>${esc(program.name)}</h3><div class="schedule ${status.badge==='ERRORE'?'error':''}"><b>${esc(status.msg)}</b><div class="small">Giorni: ${program.weekdays.map(index=>dayNames[index]).join(' · ')||'nessuno'}</div><div class="small">Partenze: ${starts.join(' · ')||'nessuna'}</div></div><p>${program.steps.map(step=>`${esc(step.zone_name)}: ${step.duration_minutes} min`).join('<br>')}</p><div class="actions wrap-actions"><button onclick="startProgram(${program.id})">Avvia</button><button class="secondary" onclick="editProgramV111(${program.id})">Modifica</button><button class="danger" onclick="deleteProgramV110(${program.id})">Elimina</button></div></article>`}).join('')||'<p>Nessun programma configurato.</p>'}</div>`;
};
