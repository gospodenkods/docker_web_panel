let token=localStorage.getItem('dockpilot_token')||'', currentPage='dashboard', dashboardTimer=null, metricHistory=[], lastMetric=null;
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
function showPage(p){if(dashboardTimer){clearInterval(dashboardTimer);dashboardTimer=null}currentPage=p;const names={dashboard:'Обзор',containers:'Контейнеры',images:'Образы',backups:'Резервные копии',networks:'Сети',quick:'Быстрый запуск'};$('#pageTitle').textContent=names[p];window['load_'+p]()}
function refreshCurrent(){showPage(currentPage)}
function modal(html){$('#modalBody').innerHTML=html;$('#modal').classList.remove('hidden')}
function closeModal(){$('#modal').classList.add('hidden')}
function toast(msg){alert(msg)}
async function load_dashboard(){
 const d=await api('/api/dashboard'); const c=d.counts,e=d.engine;
 $('#content').innerHTML=`<div class="cards">
 ${[['Контейнеры',c.containers],['Запущено',c.running],['Остановлено',c.stopped],['Образы',c.images],['Сети',c.networks]].map(x=>`<div class=card><div class=muted>${x[0]}</div><div class=metric>${x[1]}</div></div>`).join('')}
 </div><div class="monitor-grid">
 <div class=panel><h3>CPU</h3><div class=chart-value id=cpuValue>—</div><canvas class=chart id=cpuChart width=600 height=180></canvas></div>
 <div class=panel><h3>Сеть</h3><div class=chart-value id=netValue>—</div><canvas class=chart id=netChart width=600 height=180></canvas></div>
 <div class=panel><h3>Диск</h3><div class=chart-value id=diskValue>—</div><canvas class=chart id=diskChart width=600 height=180></canvas></div>
 </div><div class="panel" style="margin-top:16px"><h3>Docker Engine</h3><p><b>${esc(e.name||'-')}</b></p><p class=muted>Версия ${esc(e.server_version||'-')} · ${esc(e.os||'-')} · ${Number(e.cpus)||0} CPU · ${fmtBytes(e.memory)}</p></div>`;
 metricHistory=[];lastMetric=null;await updateDashboardMetrics();dashboardTimer=setInterval(()=>{if(currentPage==='dashboard')updateDashboardMetrics()},3000);
}
async function updateDashboardMetrics(){try{const m=await api('/api/metrics'),elapsed=lastMetric?Math.max((m.timestamp-lastMetric.timestamp)/1000,.001):0,rx=elapsed?(m.network.bytes_recv-lastMetric.network.bytes_recv)/elapsed:0,tx=elapsed?(m.network.bytes_sent-lastMetric.network.bytes_sent)/elapsed:0;lastMetric=m;metricHistory.push({cpu:m.cpu_percent,rx:Math.max(rx,0),tx:Math.max(tx,0),disk:m.disk.percent});if(metricHistory.length>30)metricHistory.shift();if(!$('#cpuChart'))return;$('#cpuValue').textContent=`${m.cpu_percent.toFixed(1)}%`;$('#netValue').textContent=`↓ ${fmtBytes(rx)}/с · ↑ ${fmtBytes(tx)}/с`;$('#diskValue').textContent=`${fmtBytes(m.disk.used)} / ${fmtBytes(m.disk.total)} (${m.disk.percent.toFixed(1)}%)`;drawChart($('#cpuChart'),metricHistory.map(x=>x.cpu),'#5b8cff',100);drawChart($('#netChart'),metricHistory.map(x=>x.rx+x.tx),'#29c38d');drawChart($('#diskChart'),metricHistory.map(x=>x.disk),'#f1a33c',100)}catch(e){if($('#cpuValue'))$('#cpuValue').textContent=e.message}}
function drawChart(canvas,values,color,fixedMax=0){const ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height,p=14,max=fixedMax||Math.max(...values,1);ctx.clearRect(0,0,w,h);ctx.strokeStyle='#27314d';ctx.lineWidth=1;for(let i=0;i<4;i++){const y=p+(h-p*2)*i/3;ctx.beginPath();ctx.moveTo(p,y);ctx.lineTo(w-p,y);ctx.stroke()}if(!values.length)return;ctx.strokeStyle=color;ctx.lineWidth=3;ctx.beginPath();values.forEach((v,i)=>{const x=values.length===1?p:p+(w-p*2)*i/(values.length-1),y=h-p-(h-p*2)*Math.min(v/max,1);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()}
async function load_containers(){
 const a=await api('/api/containers');
 $('#content').innerHTML=`<div class=toolbar><div><button onclick="newContainer()">+ Новый контейнер</button></div><div><button class=secondary onclick="prune('containers')">Очистить остановленные</button></div></div>
 <table><thead><tr><th>Имя</th><th>Образ</th><th>Статус</th><th>Сети / порты</th><th>Действия</th></tr></thead><tbody>${a.map(x=>`<tr><td><b>${esc(x.name)}</b><br><span class=muted>${esc(x.id)}</span></td><td>${esc(x.image||'')}</td><td><span class="status ${x.status}">${x.status}</span></td><td>${x.networks.map(esc).join(', ')||'-'}<br><span class=muted>${Object.entries(x.ports||{}).map(([k,v])=>`${esc(k)} → ${(v||[]).map(z=>esc(z.HostPort)).join(',')}`).join('<br>')}</span></td><td class=actions>
 ${x.status==='running'?`<button class=secondary onclick="act('${x.id}','stop')">Стоп</button><button class=secondary onclick="act('${x.id}','restart')">Рестарт</button>`:`<button onclick="act('${x.id}','start')">Старт</button>`}
 <button class=secondary onclick="logs(decodeURIComponent('${encodeURIComponent(x.id)}'),decodeURIComponent('${encodeURIComponent(x.name)}'))">Логи</button><button class=secondary onclick="stats(decodeURIComponent('${encodeURIComponent(x.id)}'),decodeURIComponent('${encodeURIComponent(x.name)}'))">Статистика</button><button class=secondary onclick="environmentView(decodeURIComponent('${encodeURIComponent(x.id)}'),decodeURIComponent('${encodeURIComponent(x.name)}'))">.env</button><button class=danger onclick="removeContainer('${x.id}')">Удалить</button></td></tr>`).join('')}</tbody></table>`;
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
async function environmentView(id,name){try{const d=await api(`/api/containers/${id}/environment`),rows=d.variables.map(v=>`<tr><td><b>${esc(v.key)}</b></td><td><code class=env-value data-value="${esc(v.value)}">••••••••</code></td><td><button class=secondary onclick="toggleEnvValue(this)">Показать</button></td></tr>`).join('');modal(`<h2>Runtime .env: ${esc(name)}</h2><div class="warning">${esc(d.warning)}</div><p class=muted>Показаны переменные, с которыми Docker запустил контейнер. Исходный файл Compose .env может отличаться.</p><table class=env-table><thead><tr><th>Переменная</th><th>Значение</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan=3>Переменные отсутствуют</td></tr>'}</tbody></table>`)}catch(e){toast(e.message)}}
function toggleEnvValue(button){const value=button.closest('tr').querySelector('.env-value'),shown=button.textContent==='Скрыть';value.textContent=shown?'••••••••':value.dataset.value;button.textContent=shown?'Показать':'Скрыть'}
async function load_images(){const a=await api('/api/images');$('#content').innerHTML=`<div class=toolbar><div><button onclick="pullDialog()">↓ Скачать из Docker Hub</button></div><div><button class=secondary onclick="prune('images')">Очистить dangling</button></div></div><table><thead><tr><th>Теги</th><th>ID</th><th>Размер</th><th></th></tr></thead><tbody>${a.map(x=>`<tr><td>${x.tags.map(esc).join('<br>')||'без тега'}</td><td>${x.id}</td><td>${fmtBytes(x.size)}</td><td><button class=danger onclick="removeImage('${x.id}')">Удалить</button></td></tr>`).join('')}</tbody></table>`}
function pullDialog(){modal(`<h2>Скачать образ</h2><form onsubmit="pullImage(event)"><input id=pull_name placeholder="Например: nginx:alpine" required><button>Скачать</button></form>`)}
async function pullImage(e){e.preventDefault();try{await api('/api/images/pull',{method:'POST',body:JSON.stringify({image:pull_name.value})});closeModal();load_images()}catch(e){toast(e.message)}}
async function removeImage(id){if(!confirm('Удалить образ?'))return;try{await api(`/api/images/${encodeURIComponent(id)}?force=true`,{method:'DELETE'});load_images()}catch(e){toast(e.message)}}
async function load_backups(){
 const [d,containers]=await Promise.all([api('/api/backups'),api('/api/containers')]),s=d.settings,running=containers.filter(x=>x.status==='running');
 const next=s.next_run?new Date(s.next_run*1000).toLocaleString():'—',last=s.last_run?new Date(s.last_run*1000).toLocaleString():'—';
 const history=d.history.filter((x,i,a)=>a.findIndex(y=>y.id===x.id)===i);
 $('#content').innerHTML=`<div class=grid>
 <div class=panel><h3>Ручное копирование</h3><p class=muted>Создаётся переносимый снимок Docker-образа с текущим writable layer контейнера.</p>
 <div class=backup-list>${running.map(x=>`<label><input class=backup-container type=checkbox value="${esc(x.id)}" checked> <b>${esc(x.name)}</b> <span class=muted>${esc(x.image||'')}</span></label>`).join('')||'<p>Нет запущенных контейнеров</p>'}</div>
 <button onclick="runBackup()" ${s.running||!running.length?'disabled':''}>${s.running?'Копирование выполняется…':'Создать копию'}</button></div>
 <div class=panel><h3>Расписание и хранилище</h3><form class=form-grid onsubmit="saveBackupSettings(event)">
 <label><input id=b_enabled type=checkbox style="width:auto" ${s.enabled?'checked':''}> Включить расписание</label>
 <label>Период, часов<input id=b_interval type=number min=1 max=8760 value="${Number(s.interval_hours)||24}"></label>
 <label>Хранилище<select id=b_target onchange="toggleWebdavFields()"><option value=local ${s.target==='local'?'selected':''}>Подключённый том / локально</option><option value=webdav ${s.target==='webdav'?'selected':''}>WebDAV</option></select></label>
 <label>Подкаталог в /backups<input id=b_local value="${esc(s.local_subdir||'')}" placeholder="Например: daily"></label>
 <div id=webdavFields class="full form-grid">
 <input class=full id=b_webdav_url value="${esc(s.webdav_url||'')}" placeholder="https://cloud.example.com/remote.php/dav/files/user">
 <input id=b_webdav_username value="${esc(s.webdav_username||'')}" placeholder="WebDAV логин">
 <input id=b_webdav_password type=password placeholder="${s.webdav_password_set?'Пароль сохранён (оставьте пустым)':'WebDAV пароль'}">
 <input id=b_webdav_path value="${esc(s.webdav_path||'dockpilot')}" placeholder="Каталог: dockpilot">
 <button type=button class=secondary onclick="testWebdav()">Проверить WebDAV</button></div>
 <button class=full>Сохранить настройки</button></form><p class=muted>Последний запуск: ${last}<br>Следующий: ${next}</p></div></div>
 <div class=panel style="margin-top:16px"><h3>История</h3><table><thead><tr><th>Время</th><th>Тип / хранилище</th><th>Статус</th><th>Файлы</th></tr></thead><tbody>${history.map(x=>`<tr><td>${esc(new Date(x.started_at).toLocaleString())}</td><td>${x.reason==='schedule'?'По расписанию':'Вручную'} / ${esc(x.target)}</td><td><span class="status ${x.status}">${esc(x.status)}</span>${x.error?`<br><span class=error>${esc(x.error)}</span>`:''}</td><td>${(x.files||[]).map(f=>`${esc(f.container)} (${f.size?fmtBytes(f.size):'WebDAV'})`).join('<br>')||'—'}</td></tr>`).join('')||'<tr><td colspan=4>Копий пока нет</td></tr>'}</tbody></table></div>`;
 toggleWebdavFields();
}
function backupPayload(){return{enabled:b_enabled.checked,interval_hours:Number(b_interval.value),target:b_target.value,local_subdir:b_local.value,webdav_url:b_webdav_url.value,webdav_username:b_webdav_username.value,webdav_password:b_webdav_password.value||null,webdav_path:b_webdav_path.value}}
function toggleWebdavFields(){if($('#webdavFields'))$('#webdavFields').classList.toggle('hidden',$('#b_target').value!=='webdav')}
async function saveBackupSettings(e){e.preventDefault();try{await api('/api/backups/settings',{method:'PUT',body:JSON.stringify(backupPayload())});toast('Настройки сохранены');load_backups()}catch(e){toast(e.message)}}
async function testWebdav(){try{const p=backupPayload();await api('/api/backups/webdav/test',{method:'POST',body:JSON.stringify(p)});toast('WebDAV доступен')}catch(e){toast(e.message)}}
async function runBackup(){const ids=[...document.querySelectorAll('.backup-container:checked')].map(x=>x.value);if(!ids.length)return toast('Выберите контейнеры');try{await api('/api/backups/run',{method:'POST',body:JSON.stringify({container_ids:ids})});toast('Резервное копирование запущено');setTimeout(load_backups,1000)}catch(e){toast(e.message)}}
async function load_networks(){const a=await api('/api/networks');$('#content').innerHTML=`<div class=toolbar><button onclick="networkDialog()">+ Создать сеть</button><button class=secondary onclick="prune('networks')">Очистить неиспользуемые</button></div><table><thead><tr><th>Имя</th><th>Драйвер</th><th>Параметры</th><th>Контейнеры</th><th></th></tr></thead><tbody>${a.map(x=>`<tr><td><b>${esc(x.name)}</b><br><span class=muted>${esc(x.id)}</span></td><td>${esc(x.driver||'')}</td><td>${x.internal?'internal ':''}${x.attachable?'attachable':''}<br><span class=muted>${x.subnets.map(s=>esc(s.Subnet||'')).join(', ')}</span></td><td>${x.containers.length}</td><td><button class=danger onclick="removeNetwork('${x.id}')">Удалить</button></td></tr>`).join('')}</tbody></table>`}
function networkDialog(){modal(`<h2>Новая сеть</h2><form class=form-grid onsubmit="createNetwork(event)"><input id=n_name placeholder="Имя" required><select id=n_driver><option value="bridge">bridge</option></select><input id=n_subnet placeholder="Subnet: 172.30.0.0/16"><input id=n_gateway placeholder="Gateway: 172.30.0.1"><label><input id=n_internal type=checkbox style="width:auto"> Internal</label><button class=full>Создать</button></form>`)}
async function createNetwork(e){e.preventDefault();try{await api('/api/networks',{method:'POST',body:JSON.stringify({name:n_name.value,driver:n_driver.value,subnet:n_subnet.value||null,gateway:n_gateway.value||null,internal:n_internal.checked})});closeModal();load_networks()}catch(e){toast(e.message)}}
async function removeNetwork(id){if(!confirm('Удалить сеть?'))return;try{await api(`/api/networks/${id}`,{method:'DELETE'});load_networks()}catch(e){toast(e.message)}}
async function load_quick(){const t=await api('/api/templates');$('#content').innerHTML=`<div class=grid>${Object.entries(t).map(([k,v])=>`<div class="card template"><h3>${esc(v.label)}</h3><p class=muted>${esc(v.description)}</p><code>${esc(v.image)}</code><br><br><button onclick="deploy('${k}')">Запустить</button></div>`).join('')}</div>`}
async function deploy(t){const name=prompt('Имя контейнера (можно оставить пустым):','');try{const d=await api('/api/quick-deploy',{method:'POST',body:JSON.stringify({template:t,name:name||null})});if(d.generated_credentials){modal(`<h2>Контейнер запущен</h2><p>Сохраните пароль сейчас. Повторно он не показывается.</p><p><b>${esc(d.generated_credentials.variable)}</b></p><pre>${esc(d.generated_credentials.password)}</pre>`)}else{toast('Контейнер запущен')}load_containers()}catch(e){toast(e.message)}}
async function prune(kind){if(!confirm('Выполнить очистку?'))return;try{await api('/api/prune',{method:'POST',body:JSON.stringify({kind})});refreshCurrent()}catch(e){toast(e.message)}}
function esc(s){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
boot();
