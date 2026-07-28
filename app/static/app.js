let token=localStorage.getItem('dockpilot_token')||'', currentPage='dashboard';
const $=s=>document.querySelector(s);
const fmtBytes=n=>{if(!n)return'0 B';const u=['B','KB','MB','GB','TB'];let i=Math.floor(Math.log(n)/Math.log(1024));return`${(n/1024**i).toFixed(1)} ${u[i]}`};
async function api(path,opt={}){
  opt.headers={...(opt.headers||{}),'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})};
  const r=await fetch(path,opt); let data={}; try{data=await r.json()}catch{}
  if(r.status===401){logout();throw Error(data.detail||'Требуется авторизация')}
  if(!r.ok)throw Error(data.detail||'Ошибка запроса'); return data;
}
async function doLogin(e){e.preventDefault();try{const d=await api('/api/login',{method:'POST',body:JSON.stringify({username:$('#username').value,password:$('#password').value})});token=d.access_token;localStorage.setItem('dockpilot_token',token);boot()}catch(e){$('#loginError').textContent=e.message}}
function logout(){token='';localStorage.removeItem('dockpilot_token');$('#app').classList.add('hidden');$('#login').classList.remove('hidden')}
function boot(){if(!token)return logout();$('#login').classList.add('hidden');$('#app').classList.remove('hidden');showPage('dashboard')}
function showPage(p){currentPage=p;const names={dashboard:'Обзор',containers:'Контейнеры',images:'Образы',networks:'Сети',quick:'Быстрый запуск'};$('#pageTitle').textContent=names[p];window['load_'+p]()}
function refreshCurrent(){showPage(currentPage)}
function modal(html){$('#modalBody').innerHTML=html;$('#modal').classList.remove('hidden')}
function closeModal(){$('#modal').classList.add('hidden')}
function toast(msg){alert(msg)}
async function load_dashboard(){
 const d=await api('/api/dashboard'); const c=d.counts,e=d.engine;
 $('#content').innerHTML=`<div class="cards">
 ${[['Контейнеры',c.containers],['Запущено',c.running],['Остановлено',c.stopped],['Образы',c.images],['Сети',c.networks]].map(x=>`<div class=card><div class=muted>${x[0]}</div><div class=metric>${x[1]}</div></div>`).join('')}
 </div><div class="panel" style="margin-top:16px"><h3>Docker Engine</h3><p><b>${esc(e.name||'-')}</b></p><p class=muted>Версия ${esc(e.server_version||'-')} · ${esc(e.os||'-')} · ${Number(e.cpus)||0} CPU · ${fmtBytes(e.memory)}</p></div>`;
}
async function load_containers(){
 const a=await api('/api/containers');
 $('#content').innerHTML=`<div class=toolbar><div><button onclick="newContainer()">+ Новый контейнер</button></div><div><button class=secondary onclick="prune('containers')">Очистить остановленные</button></div></div>
 <table><thead><tr><th>Имя</th><th>Образ</th><th>Статус</th><th>Сети / порты</th><th>Действия</th></tr></thead><tbody>${a.map(x=>`<tr><td><b>${esc(x.name)}</b><br><span class=muted>${esc(x.id)}</span></td><td>${esc(x.image||'')}</td><td><span class="status ${x.status}">${x.status}</span></td><td>${x.networks.map(esc).join(', ')||'-'}<br><span class=muted>${Object.entries(x.ports||{}).map(([k,v])=>`${esc(k)} → ${(v||[]).map(z=>esc(z.HostPort)).join(',')}`).join('<br>')}</span></td><td class=actions>
 ${x.status==='running'?`<button class=secondary onclick="act('${x.id}','stop')">Стоп</button><button class=secondary onclick="act('${x.id}','restart')">Рестарт</button>`:`<button onclick="act('${x.id}','start')">Старт</button>`}
 <button class=secondary onclick="logs(decodeURIComponent('${encodeURIComponent(x.id)}'),decodeURIComponent('${encodeURIComponent(x.name)}'))">Логи</button><button class=secondary onclick="stats(decodeURIComponent('${encodeURIComponent(x.id)}'),decodeURIComponent('${encodeURIComponent(x.name)}'))">Статистика</button><button class=danger onclick="removeContainer('${x.id}')">Удалить</button></td></tr>`).join('')}</tbody></table>`;
}
function newContainer(){modal(`<h2>Новый контейнер</h2><form class=form-grid onsubmit="createContainer(event)">
<input id=f_image placeholder="Образ: nginx:alpine" required><input id=f_name placeholder="Имя контейнера" required>
<input id=f_ports placeholder='Порты JSON: {"80/tcp":8080}'><input id=f_env placeholder='Переменные JSON: {"KEY":"value"}'>
<input id=f_network placeholder="Сеть (необязательно)"><select id=f_restart><option>unless-stopped</option><option>always</option><option>on-failure</option><option>no</option></select>
<input class=full id=f_command placeholder="Команда (необязательно)"><button class=full>Скачать образ и запустить</button></form>`)}
async function createContainer(e){e.preventDefault();try{await api('/api/containers',{method:'POST',body:JSON.stringify({image:f_image.value,name:f_name.value,ports:f_ports.value?JSON.parse(f_ports.value):{},environment:f_env.value?JSON.parse(f_env.value):{},network:f_network.value||null,restart_policy:f_restart.value,command:f_command.value||null})});closeModal();load_containers()}catch(e){toast(e.message)}}
async function act(id,a){try{await api(`/api/containers/${id}/${a}`,{method:'POST'});load_containers()}catch(e){toast(e.message)}}
async function removeContainer(id){if(!confirm('Удалить контейнер?'))return;try{await api(`/api/containers/${id}?force=true`,{method:'DELETE'});load_containers()}catch(e){toast(e.message)}}
async function logs(id,name){const d=await api(`/api/containers/${id}/logs?tail=500`);modal(`<h2>Логи: ${esc(name)}</h2><pre>${esc(d.logs)}</pre>`)}
async function stats(id,name){try{const d=await api(`/api/containers/${id}/stats`);modal(`<h2>Мониторинг: ${esc(name)}</h2><p>CPU: <b>${Number(d.cpu_percent)||0}%</b></p><div class=bar><span style="width:${Math.min(d.cpu_percent,100)}%"></span></div><p>RAM: <b>${fmtBytes(d.memory_usage)} / ${fmtBytes(d.memory_limit)} (${d.memory_percent}%)</b></p><div class=bar><span style="width:${d.memory_percent}%"></span></div>`)}catch(e){toast(e.message)}}
async function load_images(){const a=await api('/api/images');$('#content').innerHTML=`<div class=toolbar><div><button onclick="pullDialog()">↓ Скачать из Docker Hub</button></div><div><button class=secondary onclick="prune('images')">Очистить dangling</button></div></div><table><thead><tr><th>Теги</th><th>ID</th><th>Размер</th><th></th></tr></thead><tbody>${a.map(x=>`<tr><td>${x.tags.map(esc).join('<br>')||'без тега'}</td><td>${x.id}</td><td>${fmtBytes(x.size)}</td><td><button class=danger onclick="removeImage('${x.id}')">Удалить</button></td></tr>`).join('')}</tbody></table>`}
function pullDialog(){modal(`<h2>Скачать образ</h2><form onsubmit="pullImage(event)"><input id=pull_name placeholder="Например: nginx:alpine" required><button>Скачать</button></form>`)}
async function pullImage(e){e.preventDefault();try{await api('/api/images/pull',{method:'POST',body:JSON.stringify({image:pull_name.value})});closeModal();load_images()}catch(e){toast(e.message)}}
async function removeImage(id){if(!confirm('Удалить образ?'))return;try{await api(`/api/images/${encodeURIComponent(id)}?force=true`,{method:'DELETE'});load_images()}catch(e){toast(e.message)}}
async function load_networks(){const a=await api('/api/networks');$('#content').innerHTML=`<div class=toolbar><button onclick="networkDialog()">+ Создать сеть</button><button class=secondary onclick="prune('networks')">Очистить неиспользуемые</button></div><table><thead><tr><th>Имя</th><th>Драйвер</th><th>Параметры</th><th>Контейнеры</th><th></th></tr></thead><tbody>${a.map(x=>`<tr><td><b>${esc(x.name)}</b><br><span class=muted>${esc(x.id)}</span></td><td>${esc(x.driver||'')}</td><td>${x.internal?'internal ':''}${x.attachable?'attachable':''}<br><span class=muted>${x.subnets.map(s=>esc(s.Subnet||'')).join(', ')}</span></td><td>${x.containers.length}</td><td><button class=danger onclick="removeNetwork('${x.id}')">Удалить</button></td></tr>`).join('')}</tbody></table>`}
function networkDialog(){modal(`<h2>Новая сеть</h2><form class=form-grid onsubmit="createNetwork(event)"><input id=n_name placeholder="Имя" required><select id=n_driver><option value="bridge">bridge</option></select><input id=n_subnet placeholder="Subnet: 172.30.0.0/16"><input id=n_gateway placeholder="Gateway: 172.30.0.1"><label><input id=n_internal type=checkbox style="width:auto"> Internal</label><button class=full>Создать</button></form>`)}
async function createNetwork(e){e.preventDefault();try{await api('/api/networks',{method:'POST',body:JSON.stringify({name:n_name.value,driver:n_driver.value,subnet:n_subnet.value||null,gateway:n_gateway.value||null,internal:n_internal.checked})});closeModal();load_networks()}catch(e){toast(e.message)}}
async function removeNetwork(id){if(!confirm('Удалить сеть?'))return;try{await api(`/api/networks/${id}`,{method:'DELETE'});load_networks()}catch(e){toast(e.message)}}
async function load_quick(){const t=await api('/api/templates');$('#content').innerHTML=`<div class=grid>${Object.entries(t).map(([k,v])=>`<div class="card template"><h3>${esc(v.label)}</h3><p class=muted>${esc(v.description)}</p><code>${esc(v.image)}</code><br><br><button onclick="deploy('${k}')">Запустить</button></div>`).join('')}</div>`}
async function deploy(t){const name=prompt('Имя контейнера (можно оставить пустым):','');try{const d=await api('/api/quick-deploy',{method:'POST',body:JSON.stringify({template:t,name:name||null})});if(d.generated_credentials){modal(`<h2>Контейнер запущен</h2><p>Сохраните пароль сейчас. Повторно он не показывается.</p><p><b>${esc(d.generated_credentials.variable)}</b></p><pre>${esc(d.generated_credentials.password)}</pre>`)}else{toast('Контейнер запущен')}load_containers()}catch(e){toast(e.message)}}
async function prune(kind){if(!confirm('Выполнить очистку?'))return;try{await api('/api/prune',{method:'POST',body:JSON.stringify({kind})});refreshCurrent()}catch(e){toast(e.message)}}
function esc(s){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
boot();
