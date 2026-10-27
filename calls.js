(() => {
// Звонки 1 на 1 (аудио и видео) через WebRTC.
// Служебные сообщения идут через сервер (опрос раз в секунду), звук и видео — напрямую между устройствами.
const cfg = window.PYMESSENGER || {};
const $ = id => document.getElementById(id);

let iceServers = [{ urls: 'stun:stun.l.google.com:19302' }];
let lastId = null;          // последний обработанный сигнал (null = ещё не синхронизировались)
let polling = false;
let state = 'idle';         // idle | calling | ringing | connecting | active
let chatId = null;
let peerName = '';
let pc = null;
let localStream = null;
let pendingOffer = null;
let pendingIce = [];        // кандидаты, пришедшие раньше, чем мы готовы их принять
let iceQueue = [];          // наши кандидаты, пока не отправлен offer/answer
let sigReady = false;
let ringTimer = null;
let tickTimer = null;
let connectedAt = 0;
let muted = false;

async function sendSignal(chat, kind, payload) {
  try {
    await fetch(`/api/calls/${chat}/signal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, payload: payload ? JSON.stringify(payload) : '' })
    });
  } catch {}
}

function showUI(name, status, { incoming = false } = {}) {
  $('callOverlay').hidden = false;
  $('callName').textContent = name || '';
  $('callStatus').textContent = status || '';
  $('acceptCallBtn').hidden = !incoming;
  $('muteCallBtn').hidden = incoming;
}
function setStatus(text) { const el = $('callStatus'); if (el) el.textContent = text; }

function cleanup(message) {
  clearTimeout(ringTimer); clearInterval(tickTimer);
  if (pc) {
    pc.onicecandidate = null; pc.ontrack = null; pc.onconnectionstatechange = null;
    try { pc.close(); } catch {}
    pc = null;
  }
  if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
  ['remoteVideo', 'localVideo', 'remoteAudio'].forEach(id => { const el = $(id); if (el) { el.srcObject = null; el.classList.remove('on'); } });
  state = 'idle'; chatId = null; pendingOffer = null; pendingIce = []; iceQueue = []; sigReady = false; muted = false;
  const mb = $('muteCallBtn'); if (mb) { mb.classList.remove('muted'); mb.textContent = '🎙'; }
  if (message) {
    setStatus(message);
    $('acceptCallBtn').hidden = true;
    setTimeout(() => { if (state === 'idle') $('callOverlay').hidden = true; }, 1600);
  } else {
    $('callOverlay').hidden = true;
  }
}

async function getMedia(video) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error('insecure');
  }
  if (video) {
    try { return await navigator.mediaDevices.getUserMedia({ audio: true, video: { facingMode: 'user' } }); }
    catch { /* нет камеры или нет доступа — звоним только голосом */ }
  }
  return navigator.mediaDevices.getUserMedia({ audio: true, video: false });
}

function mediaErrorText(err) {
  if (err && err.message === 'insecure') return 'Звонки работают только на сайте с https. Открой мессенджер по https-адресу.';
  return 'Нет доступа к микрофону. Разреши микрофон для приложения в настройках телефона.';
}

function showLocalPreview() {
  const lv = $('localVideo');
  if (localStream && localStream.getVideoTracks().length) { lv.srcObject = localStream; lv.classList.add('on'); }
}

function flushIceQueue() {
  sigReady = true;
  iceQueue.forEach(c => sendSignal(chatId, 'ice', c));
  iceQueue = [];
}

function createPeer() {
  pc = new RTCPeerConnection({ iceServers });
  localStream.getTracks().forEach(t => pc.addTrack(t, localStream));
  pc.onicecandidate = e => {
    if (!e.candidate) return;
    const c = e.candidate.toJSON();
    if (sigReady) sendSignal(chatId, 'ice', c); else iceQueue.push(c);
  };
  pc.ontrack = e => {
    const stream = e.streams[0] || new MediaStream([e.track]);
    $('remoteAudio').srcObject = stream;
    if (e.track.kind === 'video') {
      const rv = $('remoteVideo');
      rv.muted = true;            // звук играет через remoteAudio, чтобы не было эха
      rv.srcObject = stream; rv.classList.add('on');
    }
  };
  pc.onconnectionstatechange = () => {
    if (!pc) return;
    const s = pc.connectionState;
    if (s === 'connected' && state !== 'active') {
      state = 'active'; connectedAt = Date.now(); clearTimeout(ringTimer);
      clearInterval(tickTimer);
      tickTimer = setInterval(() => {
        const sec = Math.floor((Date.now() - connectedAt) / 1000);
        setStatus('Разговор ' + String(Math.floor(sec / 60)).padStart(2, '0') + ':' + String(sec % 60).padStart(2, '0'));
      }, 1000);
    } else if (s === 'failed') {
      endCall(true, 'Не удалось соединиться. Возможно, нужен TURN-сервер.');
    } else if (s === 'closed') {
      endCall(false, 'Звонок завершён');
    }
  };
}

async function startCall(video) {
  if (state !== 'idle' || !cfg.selectedChat) return;
  chatId = cfg.selectedChat;
  peerName = ($('headerTitle') && $('headerTitle').textContent) || '';
  state = 'calling';
  showUI(peerName, 'Запрашиваем микрофон…');
  try { localStream = await getMedia(video); }
  catch (err) { cleanup(); alert(mediaErrorText(err)); return; }
  showLocalPreview();
  try {
    createPeer();
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await sendSignal(chatId, 'offer', { type: 'offer', sdp: pc.localDescription.sdp, video: localStream.getVideoTracks().length > 0 });
    flushIceQueue();
    setStatus('Вызываем…');
    ringTimer = setTimeout(() => { if (state === 'calling') endCall(true, 'Нет ответа'); }, 45000);
  } catch {
    endCall(true, 'Не удалось начать звонок');
  }
}

async function acceptCall() {
  if (state !== 'ringing' || !pendingOffer) return;
  clearTimeout(ringTimer);
  const offer = pendingOffer;
  setStatus('Запрашиваем микрофон…');
  try { localStream = await getMedia(!!offer.video); }
  catch (err) {
    sendSignal(chatId, 'decline', null);
    cleanup(); alert(mediaErrorText(err)); return;
  }
  state = 'connecting';
  showUI(peerName, 'Соединяем…');
  showLocalPreview();
  try {
    createPeer();
    await pc.setRemoteDescription({ type: 'offer', sdp: offer.sdp });
    for (const c of pendingIce) { try { await pc.addIceCandidate(c); } catch {} }
    pendingIce = [];
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    await sendSignal(chatId, 'answer', { type: 'answer', sdp: pc.localDescription.sdp });
    flushIceQueue();
  } catch {
    endCall(true, 'Не удалось принять звонок');
  }
}

function endCall(notify, message) {
  if (state === 'idle') return;
  if (notify && chatId) sendSignal(chatId, 'hangup', null);
  cleanup(message || 'Звонок завершён');
}

function hangupPressed() {
  if (state === 'ringing') { sendSignal(chatId, 'decline', null); cleanup(); }
  else endCall(true, 'Звонок завершён');
}

function toggleMute() {
  if (!localStream) return;
  muted = !muted;
  localStream.getAudioTracks().forEach(t => { t.enabled = !muted; });
  const mb = $('muteCallBtn');
  mb.classList.toggle('muted', muted);
  mb.textContent = muted ? '🔇' : '🎙';
}

async function handleSignal(sig) {
  let payload = null;
  try { payload = sig.payload ? JSON.parse(sig.payload) : null; } catch {}
  switch (sig.kind) {
    case 'offer': {
      if (!payload || !payload.sdp) return;
      if (state !== 'idle') { sendSignal(sig.chat_id, 'decline', { reason: 'busy' }); return; }
      state = 'ringing'; chatId = sig.chat_id; peerName = sig.from_name; pendingOffer = payload; pendingIce = [];
      showUI(peerName, payload.video ? 'Входящий видеозвонок' : 'Входящий звонок', { incoming: true });
      try { if (navigator.vibrate) navigator.vibrate([300, 200, 300]); } catch {}
      ringTimer = setTimeout(() => { if (state === 'ringing') { sendSignal(chatId, 'decline', null); cleanup('Пропущенный звонок'); } }, 45000);
      break;
    }
    case 'answer': {
      if (state !== 'calling' || sig.chat_id !== chatId || !pc || !payload) return;
      clearTimeout(ringTimer);
      state = 'connecting'; setStatus('Соединяем…');
      try {
        await pc.setRemoteDescription({ type: 'answer', sdp: payload.sdp });
        for (const c of pendingIce) { try { await pc.addIceCandidate(c); } catch {} }
        pendingIce = [];
      } catch { endCall(true, 'Ошибка соединения'); }
      break;
    }
    case 'ice': {
      if (state === 'idle' || sig.chat_id !== chatId || !payload) return;
      if (pc && pc.remoteDescription) { try { await pc.addIceCandidate(payload); } catch {} }
      else pendingIce.push(payload);
      break;
    }
    case 'hangup': {
      if (state === 'idle' || sig.chat_id !== chatId) return;
      cleanup(state === 'ringing' ? 'Пропущенный звонок' : 'Собеседник завершил звонок');
      break;
    }
    case 'decline': {
      if (state === 'idle' || sig.chat_id !== chatId) return;
      const busy = payload && payload.reason === 'busy';
      cleanup(busy ? 'Абонент занят' : 'Абонент отклонил звонок');
      break;
    }
  }
}

async function pollSignals() {
  if (polling) return;
  polling = true;
  try {
    const res = await fetch('/api/calls/poll?after=' + (lastId === null ? -1 : lastId));
    if (!res.ok) return;
    const data = await res.json();
    if (lastId === null) { lastId = data.last_id; return; }
    for (const sig of data.signals) { lastId = sig.id; await handleSignal(sig); }
  } catch {} finally { polling = false; }
}

document.addEventListener('DOMContentLoaded', async () => {
  if (!$('callOverlay')) return;
  $('audioCallBtn')?.addEventListener('click', () => startCall(false));
  $('videoCallBtn')?.addEventListener('click', () => startCall(true));
  $('acceptCallBtn').addEventListener('click', acceptCall);
  $('hangupCallBtn').addEventListener('click', hangupPressed);
  $('muteCallBtn').addEventListener('click', toggleMute);
  try {
    const res = await fetch('/api/calls/config');
    if (res.ok) { const d = await res.json(); if (d.iceServers && d.iceServers.length) iceServers = d.iceServers; }
  } catch {}
  await pollSignals();
  setInterval(pollSignals, 1000);
});

// Если страницу закрыли посреди звонка, сообщим собеседнику.
window.addEventListener('pagehide', () => {
  if (state !== 'idle' && chatId && navigator.sendBeacon) {
    const kind = state === 'ringing' ? 'decline' : 'hangup';
    navigator.sendBeacon(`/api/calls/${chatId}/signal`, new Blob([JSON.stringify({ kind, payload: '' })], { type: 'application/json' }));
  }
});
})();
