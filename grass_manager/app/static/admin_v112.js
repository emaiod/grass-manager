const assignmentsHtmlV112=assignmentsHtml;
assignmentsHtml=function(user={plants:[]}){
  if(user.global_role==='admin'){
    return `<div class="panel small">Gli amministratori possono vedere e gestire tutti gli impianti. Non sono necessarie assegnazioni specifiche.</div>`;
  }
  return assignmentsHtmlV112(user);
};

const editUserV100Original=window.editUserV100;
window.editUserV100=async function(id){
  await editUserV100Original(id);
  const user=usersV100.find(item=>item.id===id);
  const roleSelect=document.querySelector('#modalForm select[name="global_role"]');
  if(!roleSelect)return;
  const assignmentsContainer=[...document.querySelectorAll('#modalBody .span2')].find(node=>node.textContent.includes('Impianti assegnati'));
  const refresh=()=>{
    if(!assignmentsContainer)return;
    if(roleSelect.value==='admin'){
      assignmentsContainer.innerHTML='<b>Impianti assegnati</b><div class="panel small">Gli amministratori accedono automaticamente a tutti gli impianti.</div>';
    }else if(user){
      assignmentsContainer.innerHTML=`<b>Impianti assegnati</b>${assignmentsHtmlV112(user)}`;
    }
  };
  roleSelect.addEventListener('change',refresh);
  refresh();
};

const renderZonesV110Original=renderZonesV110;
renderZonesV110=function(){
  renderZonesV110Original();
  if(me.global_role!=='admin'){
    const toolbar=$('#plantSubContent .toolbar');
    const createButton=toolbar?.querySelector('button');
    if(createButton)createButton.remove();
    const note=toolbar?.querySelector('.small');
    if(note)note.textContent='Le zone sono configurate dall’amministratore. Puoi utilizzare quelle già disponibili.';
  }
};
