(() => {
const cfg = window.PYMESSENGER || {};
let activeChat = cfg.selectedChat;
let lastMessageId = 0;
let pollTimer = null;
let mediaRecorder = null;
let recordedChunks = [];
let selectedGroupUsers = new Map();
let peerConnection = null;
let localStream = null;
let callMode = 'audio';
let incomingOffer = null;
let lastCallSignalId = 0;
let queuedIceCandidates = [];
let callBusy = false;
let activeCallChat = null;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const initials = s => (s || '?').trim().slice(0,1).toUpperCase();
const avatarHtml = (avatar, name, cls='message-avatar') => `<div class="avatar ${cls}">${avatar ? `<img src="/uploads/avatars/${encodeURIComponent(avatar)}">` : esc(initials(name))}</div>`;
function timeLabel(iso) { try { return new Date(iso).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); } catch { return ''; } }
function showError(message) { alert(message || 'Что-то пошло не так.'); }

async function loadChats() {
  const list = $('chatList');
  if (!list) return;
  try {
    const res = await fetch('/api/chats');
    if (!res.ok) return;
    const chats = await res.json();
    const oldSelected = activeChat;
    list.innerHTML = chats.map(c => `<a class="chat-item ${Number(c.id)===Number(oldSelected)?'selected':''}" data-chat-id="${c.id}" href="/messenger?chat=${c.id}">
      ${avatarHtml(c.avatar,c.title,'chat-avatar')}<div class="chat-meta"><div class="chat-title-row"><strong>${esc(c.title)}</strong></div><span class="preview">${esc(c.preview || c.subtitle)}</span></div></a>`).join('') || '<div class="empty-side">Пока нет чатов.<br>Нажми ＋, чтобы начать.</div>';
    if (oldSelected && !chats.some(c => Number(c.id)===Number(oldSelected))) {
      window.location.href = '/messenger';
    }
    const selected = chats.find(c => Number(c.id)===Number(activeChat));
    if (selected) {
      const title = $('headerTitle'), sub = $('headerSub'), avatar = $('headerAvatar');
      if (title) title.textContent = selected.title;
      if (sub) sub.textContent = selected.subtitle;
      if (avatar) avatar.innerHTML = selected.avatar ? `<img src="/uploads/avatars/${encodeURIComponent(selected.avatar)}">` : (selected.is_group ? '👥' : esc(initials(selected.title)));
    }
  } catch {}
}
function renderMessage(m) {
  const mine = Number(m.sender_id) === Number(cfg.userId);
  let attachment = '';
  if (m.attachment) {
    const url = `/uploads/files/${encodeURIComponent(m.attachment)}`;
    if (m.attachment_kind === 'image') {
      attachment = `<a class="attachment" href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="${esc(m.attachment_name)}"></a>`;
    } else if (m.attachment_kind === 'audio') {
      attachment = `<div class="attachment"><audio controls preload="none" src="${url}"></audio><a href="${url}" download="${esc(m.attachment_name)}">${esc(m.attachment_name)}</a></div>`;
    } else {
      attachment = `<a class="attachment" href="${url}" download="${esc(m.attachment_name)}"><span class="file-chip"><span>📎</span><span>${esc(m.attachment_name)}<small>Нажми, чтобы скачать</small></span></span></a>`;
    }
  }
  return `<div class="message-row ${mine?'mine':''}" data-message-id="${m.id}">
    ${mine?'':avatarHtml(m.avatar,m.display_name)}
    <div class="bubble">${mine?'':`<div class="sender">${esc(m.display_name)} · @${esc(m.username)}</div>`}
      ${m.body ? `<div class="message-text">${esc(m.body)}</div>` : ''}${attachment}
      <div class="message-time">${timeLabel(m.created_at)}</div></div></div>`;
}
async function fetchMessages(initial=false) {
  if (!activeChat || !$('messages')) return;
  try {
    const res = await fetch(`/api/messages/${activeChat}?after=${initial?0:lastMessageId}`);
    if (res.status === 404) {
      // The selected chat may have been deleted or the current account is no longer a member.
      if (pollTimer) clearInterval(pollTimer);
      activeChat = null;
      window.location.replace('/messenger');
      return;
    }
    if (!res.ok) {
      console.error('Could not load messages:', res.status, await res.text().catch(() => ''));
      return;
    }
    const rows = await res.json();
    const box = $('messages');
    if (initial) { box.innerHTML = ''; lastMessageId = 0; }
    if (rows.length) {
      if (box.querySelector('.loading')) box.innerHTML = '';
      const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 130;
      rows.forEach(m => { box.insertAdjacentHTML('beforeend', renderMessage(m)); lastMessageId = Math.max(lastMessageId, Number(m.id)); });
      if (initial || nearBottom) box.scrollTop = box.scrollHeight;
      loadChats();
    } else if (initial) {
      box.innerHTML = '<div class="loading">Пока нет сообщений. Напиши первым 👋</div>';
    }
  } catch {}
}
async function submitMessage(body='', file=null) {
  if (!activeChat) return;
  const form = new FormData();
  if (body.trim()) form.append('body', body.trim());
  if (file) form.append('file', file, file.name || 'voice-message.webm');
  try {
    const res = await fetch(`/api/messages/${activeChat}`, {method:'POST', body:form});
    const data = await res.json();
    if (!res.ok) return showError(data.error || 'Не удалось отправить сообщение.');
    if ($('messageInput')) $('messageInput').value = '';
    if ($('fileInput')) $('fileInput').value = '';
    await fetchMessages();
  } catch { showError('Не удалось связаться с сервером.'); }
}

function callStatus(text) { const el=$('callStatus'); if(el) el.textContent=text; }
function showCallPanel() { if($('callBackdrop')) $('callBackdrop').hidden=false; }
function hideCallPanel() { if($('callBackdrop')) $('callBackdrop').hidden=true; }
async function sendCallSignal(kind,payload='') {
 const chatId=activeCallChat||activeChat;if(!chatId)return;
 const res=await fetch('/api/calls/'+chatId+'/signal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,payload:typeof payload==='string'?payload:JSON.stringify(payload)})});
 const data=await res.json().catch(()=>({}));if(!res.ok)throw new Error(data.error||'Не удалось отправить сигнал звонка.');
}
async function createPeerConnection(mode) {
 const r=await fetch('/api/calls/config');if(!r.ok)throw new Error('Не удалось получить настройки звонка.');
 const config=await r.json();peerConnection=new RTCPeerConnection({iceServers:config.iceServers||[{urls:'stun:stun.l.google.com:19302'}]});
 peerConnection.onicecandidate=e=>{if(e.candidate)sendCallSignal('ice',e.candidate.toJSON()).catch(()=>{});};
 peerConnection.onconnectionstatechange=()=>{if(!peerConnection)return;if(peerConnection.connectionState==='connected')callStatus('Соединение установлено');if(['failed','disconnected'].includes(peerConnection.connectionState))callStatus('Соединение прервано. Проверьте интернет/NAT.');};
 peerConnection.ontrack=e=>{const stream=e.streams&&e.streams[0];if(mode==='video'&&$('remoteVideo')){$('remoteVideo').srcObject=stream;$('callVideos').hidden=false;}else if($('remoteAudio'))$('remoteAudio').srcObject=stream;};
}
async function getCallMedia(mode) {
 if(!navigator.mediaDevices?.getUserMedia)throw new Error('Микрофон и камера доступны только через HTTPS или localhost.');
 localStream=await navigator.mediaDevices.getUserMedia({audio:true,video:mode==='video'});
 if(mode==='video'){$('callVideos').hidden=false;$('localVideo').srcObject=localStream;}
 localStream.getTracks().forEach(t=>peerConnection.addTrack(t,localStream));
}
async function flushIce(){if(!peerConnection?.remoteDescription)return;for(const cand of queuedIceCandidates.splice(0)){try{await peerConnection.addIceCandidate(cand);}catch{}}}
async function startCall(mode) {
 if(!cfg.isDirect||!activeChat||callBusy)return;
 callBusy=true;callMode=mode;activeCallChat=activeChat;incomingOffer=null;queuedIceCandidates=[];
 showCallPanel();$('callTitle').textContent=mode==='video'?'Видеозвонок':'Голосовой звонок';$('acceptCallBtn').hidden=true;$('declineCallBtn').hidden=true;$('hangupCallBtn').hidden=false;
 callStatus('Запрашиваем доступ к '+(mode==='video'?'камере и микрофону…':'микрофону…'));
 try{await createPeerConnection(mode);await getCallMedia(mode);const offer=await peerConnection.createOffer();await peerConnection.setLocalDescription(offer);await sendCallSignal('offer',{description:peerConnection.localDescription.toJSON(),mode});callStatus('Звоним собеседнику… ожидаем ответа');}
 catch(e){showError(e.message||'Не удалось начать звонок.');await endCall(false);}
}
async function acceptCall() {
 if(!incomingOffer||callBusy)return;callBusy=true;callMode=incomingOffer.mode||'audio';activeCallChat=Number(incomingOffer.chat_id);
 $('acceptCallBtn').hidden=true;$('declineCallBtn').hidden=true;$('hangupCallBtn').hidden=false;callStatus('Подключаемся к собеседнику…');
 try{await createPeerConnection(callMode);await getCallMedia(callMode);await peerConnection.setRemoteDescription(incomingOffer.description);await flushIce();const answer=await peerConnection.createAnswer();await peerConnection.setLocalDescription(answer);await sendCallSignal('answer',{description:peerConnection.localDescription.toJSON()});incomingOffer=null;}
 catch(e){showError(e.message||'Не удалось ответить на звонок.');await endCall(true);}
}
async function endCall(notify=true) {
 const chatId=activeCallChat||incomingOffer?.chat_id||activeChat;const shouldNotify=notify&&Boolean(chatId)&&(callBusy||Boolean(incomingOffer));callBusy=false;
 if(shouldNotify){try{await fetch('/api/calls/'+chatId+'/signal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'hangup',payload:''})});}catch{}}
 if(peerConnection){peerConnection.onicecandidate=null;peerConnection.close();peerConnection=null;}if(localStream){localStream.getTracks().forEach(t=>t.stop());localStream=null;}
 if($('localVideo'))$('localVideo').srcObject=null;if($('remoteVideo'))$('remoteVideo').srcObject=null;if($('remoteAudio'))$('remoteAudio').srcObject=null;if($('callVideos'))$('callVideos').hidden=true;
 if($('hangupCallBtn'))$('hangupCallBtn').hidden=true;if($('acceptCallBtn'))$('acceptCallBtn').hidden=true;if($('declineCallBtn'))$('declineCallBtn').hidden=true;
 incomingOffer=null;queuedIceCandidates=[];activeCallChat=null;callStatus('Звонок завершён');setTimeout(hideCallPanel,700);
}
async function handleCallSignal(signal) {
 if(signal.kind==='offer'){
  if(callBusy||incomingOffer){try{await fetch('/api/calls/'+signal.chat_id+'/signal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'decline',payload:''})});}catch{}return;}
  let offerData;try{offerData=JSON.parse(signal.payload);}catch{return;}
  incomingOffer={chat_id:Number(signal.chat_id),description:offerData.description,mode:offerData.mode==='video'?'video':'audio'};activeCallChat=Number(signal.chat_id);callMode=incomingOffer.mode;
  showCallPanel();$('callTitle').textContent=incomingOffer.mode==='video'?'Входящий видеозвонок':'Входящий голосовой звонок';callStatus((signal.from_name||'Собеседник')+' звонит вам');
  $('acceptCallBtn').hidden=false;$('declineCallBtn').hidden=false;$('hangupCallBtn').hidden=true;return;
 }
 if(Number(signal.chat_id)!==Number(activeCallChat))return;
 if(signal.kind==='answer'&&peerConnection){try{const answer=JSON.parse(signal.payload);await peerConnection.setRemoteDescription(answer.description);await flushIce();callStatus('Ответ получен. Соединяем…');}catch{callStatus('Не удалось обработать ответ собеседника.');}}
 else if(signal.kind==='ice'){try{const candidate=new RTCIceCandidate(JSON.parse(signal.payload));if(peerConnection?.remoteDescription)await peerConnection.addIceCandidate(candidate);else queuedIceCandidates.push(candidate);}catch{}}
 else if(signal.kind==='hangup'||signal.kind==='decline'){callStatus(signal.kind==='decline'?'Собеседник отклонил звонок':'Собеседник завершил звонок');await endCall(false);}
}
async function pollCallSignals(){try{const res=await fetch('/api/calls/poll?after='+lastCallSignalId);if(!res.ok)return;const data=await res.json();for(const signal of data.signals||[]){lastCallSignalId=Math.max(lastCallSignalId,Number(signal.id));await handleCallSignal(signal);}lastCallSignalId=Math.max(lastCallSignalId,Number(data.last_id||0));}catch{}}

function openModal(group=false) {
  const backdrop = $('modalBackdrop');
  if (!backdrop) return;
  backdrop.hidden = false;
  $('singleChatPane').hidden = group;
  $('groupPane').hidden = !group;
  $('modalTitle').textContent = group ? 'Новая группа' : 'Новый чат';
  $('modalHint').textContent = group ? 'Придумай название и добавь участников.' : 'Найди пользователя по логину или имени.';
  if (group) { selectedGroupUsers.clear(); renderSelectedUsers(); $('groupTitle').value=''; $('groupSearch').value=''; $('groupResults').innerHTML=''; }
  else { $('userSearch').value=''; $('searchResults').innerHTML=''; setTimeout(()=>$('userSearch').focus(),50); }
}
function closeModal() { if ($('modalBackdrop')) $('modalBackdrop').hidden=true; }
async function searchUsers(q, target, group=false) {
  if (!target) return;
  if (q.trim().length < 2) { target.innerHTML = ''; return; }
  try {
    const res = await fetch('/api/users/search?q='+encodeURIComponent(q));
    const users = await res.json();
    target.innerHTML = users.length ? users.map(u => `<button class="result-user" data-user-id="${u.id}" data-user-name="${esc(u.display_name)}" data-user-login="${esc(u.username)}" data-avatar="${esc(u.avatar||'')}">
      ${avatarHtml(u.avatar,u.display_name,'result-avatar')}<span><strong>${esc(u.display_name)}</strong><small>@${esc(u.username)}</small></span></button>`).join('') : '<div class="search-empty">Никого не нашли.</div>';
    target.querySelectorAll('.result-user').forEach(btn => btn.addEventListener('click', async () => {
      const id = Number(btn.dataset.userId);
      if (group) {
        if (selectedGroupUsers.has(id)) selectedGroupUsers.delete(id);
        else selectedGroupUsers.set(id,{id,display_name:btn.dataset.userName,username:btn.dataset.userLogin,avatar:btn.dataset.avatar});
        renderSelectedUsers();
        btn.classList.toggle('chosen',selectedGroupUsers.has(id));
      } else {
        const r = await fetch('/api/chats/direct',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:id})});
        const d = await r.json();
        if (!r.ok) return showError(d.error);
        window.location.href='/messenger?chat='+d.chat_id;
      }
    }));
  } catch { target.innerHTML = '<div class="search-empty">Ошибка поиска. Проверь соединение.</div>'; }
}
function renderSelectedUsers() {
  const el = $('groupSelected'); if (!el) return;
  el.innerHTML = [...selectedGroupUsers.values()].map(u=>`<span class="selected-chip">${esc(u.display_name)} <button data-remove="${u.id}" type="button">×</button></span>`).join('');
  el.querySelectorAll('[data-remove]').forEach(b=>b.addEventListener('click',()=>{selectedGroupUsers.delete(Number(b.dataset.remove));renderSelectedUsers();}));
}
async function createGroup() {
  const title = $('groupTitle').value.trim();
  if (!title) return showError('Введи название группы.');
  try {
    const res = await fetch('/api/chats/group',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,user_ids:[...selectedGroupUsers.keys()]})});
    const data = await res.json();
    if (!res.ok) return showError(data.error);
    window.location.href='/messenger?chat='+data.chat_id;
  } catch { showError('Не удалось создать группу.'); }
}
async function startRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
    return showError('Запись голоса недоступна в этом браузере. Используй HTTPS или localhost и разреши доступ к микрофону.');
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => { if (e.data && e.data.size) recordedChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t=>t.stop());
      const blob = new Blob(recordedChunks,{type:mediaRecorder.mimeType || 'audio/webm'});
      if (blob.size) {
        const ext = (blob.type.includes('ogg')?'ogg':blob.type.includes('mp4')?'m4a':'webm');
        const file = new File([blob],`voice-${Date.now()}.${ext}`,{type:blob.type});
        await submitMessage('',file);
      }
      $('recordingNote').hidden=true; $('voiceBtn').classList.remove('recording'); $('voiceBtn').textContent='🎙';
    };
    mediaRecorder.start();
    $('recordingNote').hidden=false; $('voiceBtn').classList.add('recording'); $('voiceBtn').textContent='■';
  } catch { showError('Нет доступа к микрофону. Проверь разрешения браузера.'); }
}
function stopRecording(send=true) {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    if (!send) recordedChunks=[];
    mediaRecorder.stop();
  }
}
document.addEventListener('DOMContentLoaded',()=>{
  $('newChatBtn')?.addEventListener('click',()=>openModal(false));
  $('welcomeNew')?.addEventListener('click',()=>openModal(false));
  $('newGroupBtn')?.addEventListener('click',()=>openModal(true));
  $('modalClose')?.addEventListener('click',closeModal);
  $('modalBackdrop')?.addEventListener('click',e=>{if(e.target===$('modalBackdrop'))closeModal();});
  $('userSearch')?.addEventListener('input',e=>searchUsers(e.target.value,$('searchResults'),false));
  $('groupSearch')?.addEventListener('input',e=>searchUsers(e.target.value,$('groupResults'),true));
  $('createGroupBtn')?.addEventListener('click',createGroup);
  $('chatSearch')?.addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('.chat-item').forEach(el=>el.style.display=el.innerText.toLowerCase().includes(q)?'':'none');});
  $('refreshBtn')?.addEventListener('click',()=>fetchMessages(true));
  $('voiceCallBtn')?.addEventListener('click',()=>startCall('audio'));
  $('videoCallBtn')?.addEventListener('click',()=>startCall('video'));
  $('acceptCallBtn')?.addEventListener('click',acceptCall);
  $('declineCallBtn')?.addEventListener('click',()=>endCall(true));
  $('hangupCallBtn')?.addEventListener('click',()=>endCall(true));
  setInterval(pollCallSignals,900);pollCallSignals();
  $('composer')?.addEventListener('submit',e=>{e.preventDefault();submitMessage($('messageInput').value);});
  $('attachBtn')?.addEventListener('click',()=>$('fileInput').click());
  $('fileInput')?.addEventListener('change',e=>{const file=e.target.files[0];if(file)submitMessage('',file);});
  $('voiceBtn')?.addEventListener('click',()=>{if(mediaRecorder && mediaRecorder.state==='recording')stopRecording(true);else startRecording();});
  $('cancelRecordBtn')?.addEventListener('click',()=>stopRecording(false));
  $('messageInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('composer').requestSubmit();}});
  if(activeChat){fetchMessages(true);pollTimer=setInterval(()=>{fetchMessages(false);},1800);loadChats();}
});
})();