(() => {
const cfg = window.PYMESSENGER || {};
let activeChat = cfg.selectedChat;
let lastMessageId = 0;
let pollTimer = null;
let mediaRecorder = null;
let recordedChunks = [];
let selectedGroupUsers = new Map();
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
    if (!res.ok) return;
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
  $('composer')?.addEventListener('submit',e=>{e.preventDefault();submitMessage($('messageInput').value);});
  $('attachBtn')?.addEventListener('click',()=>$('fileInput').click());
  $('fileInput')?.addEventListener('change',e=>{const file=e.target.files[0];if(file)submitMessage('',file);});
  $('voiceBtn')?.addEventListener('click',()=>{if(mediaRecorder && mediaRecorder.state==='recording')stopRecording(true);else startRecording();});
  $('cancelRecordBtn')?.addEventListener('click',()=>stopRecording(false));
  $('messageInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('composer').requestSubmit();}});
  if(activeChat){fetchMessages(true);pollTimer=setInterval(()=>{fetchMessages(false);},1800);loadChats();}
});
})();