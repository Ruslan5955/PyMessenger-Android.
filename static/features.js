(() => {
const $ = id => document.getElementById(id);
const aiBackdrop = $('aiBackdrop'), spotifyBackdrop = $('spotifyBackdrop');
function show(el){if(el)el.hidden=false}
function hide(el){if(el)el.hidden=true}
$('aiOpenBtn')?.addEventListener('click',()=>{show(aiBackdrop);$('aiInput')?.focus()});
$('spotifyOpenBtn')?.addEventListener('click',()=>show(spotifyBackdrop));
document.querySelectorAll('[data-feature-close]').forEach(btn=>btn.addEventListener('click',()=>hide($(btn.dataset.featureClose))));
[aiBackdrop,spotifyBackdrop].forEach(el=>el?.addEventListener('click',e=>{if(e.target===el)hide(el)}));
document.addEventListener('keydown',e=>{if(e.key==='Escape'){hide(aiBackdrop);hide(spotifyBackdrop)}});
const aiForm=$('aiForm'), aiInput=$('aiInput'), aiMessages=$('aiMessages'), aiStatus=$('aiStatus');
function addBubble(text,kind){const el=document.createElement('div');el.className='ai-bubble '+kind;el.textContent=text;aiMessages.appendChild(el);aiMessages.scrollTop=aiMessages.scrollHeight;return el}
aiForm?.addEventListener('submit',async e=>{
 e.preventDefault();const question=aiInput.value.trim();if(!question)return;
 addBubble(question,'ai-question');aiInput.value='';const waiting=addBubble('Думаю над ответом','ai-reply ai-loading');aiStatus.textContent='Отправляю запрос помощнику…';
 try{const res=await fetch('/api/assistant',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:question})});const data=await res.json();waiting.remove();if(!res.ok){addBubble(data.error||'Помощник временно недоступен.','ai-reply');aiStatus.textContent='Проверь настройки сервера.';return}addBubble(data.reply,'ai-reply');aiStatus.textContent='Ответ готов.'}
 catch{waiting.remove();addBubble('Не удалось связаться с сервером. Проверь интернет и настройки AI.','ai-reply');aiStatus.textContent='Ошибка соединения.'}
});
aiInput?.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();aiForm.requestSubmit()}});
function parseSpotify(raw){try{const u=new URL(raw);if(!['open.spotify.com','www.open.spotify.com'].includes(u.hostname))return null;const parts=u.pathname.split('/').filter(Boolean);if(parts[0]==='embed')parts.shift();if(!['track','album','playlist','episode','show'].includes(parts[0])||!parts[1]||!/^[A-Za-z0-9]+$/.test(parts[1]))return null;return {type:parts[0],id:parts[1],url:'https://open.spotify.com/'+parts[0]+'/'+parts[1]}}catch{return null}}
$('spotifyForm')?.addEventListener('submit',e=>{e.preventDefault();const data=parseSpotify($('spotifyUrl').value.trim());const player=$('spotifyPlayer');if(!data){player.innerHTML='<div class="spotify-empty"><span>Вставь корректную ссылку на трек, альбом или плейлист Spotify.</span></div>';return}player.innerHTML='';const frame=document.createElement('iframe');frame.title='Spotify '+data.type;frame.loading='lazy';frame.allow='autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture';frame.referrerPolicy='strict-origin-when-cross-origin';frame.src='https://open.spotify.com/embed/'+data.type+'/'+data.id+'?utm_source=generator&theme=0';if(data.type==='track'||data.type==='episode')frame.className='compact-track';player.appendChild(frame);$('spotifyExternal').href=data.url});
})();