const API='http://127.0.0.1:17653';
const $=id=>document.getElementById(id);
async function getTab(){return (await chrome.tabs.query({active:true,currentWindow:true}))[0];}
async function cfg(){return await chrome.storage.local.get(['token']);}
async function call(path, body){
  const c=await cfg();
  if(!c.token) throw new Error('Set your API token in Extension settings.');
  const r=await fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json','X-MediaFetch-Token':c.token},body:JSON.stringify(body)});
  const j=await r.json(); if(!r.ok) throw new Error(j.error||`HTTP ${r.status}`); return j;
}
async function health(){try{const r=await fetch(API+'/health'); return r.ok}catch{return false}}
(async()=>{
  $('state').textContent=(await health())?'Desktop app connected.':'Desktop app not running.';
})();
$('send').onclick=async()=>{try{const t=await getTab();const j=await call('/download',{url:t.url,mode:'video',quality:'best'});$('result').textContent=`Queued download #${j.id}. Open Vydora to watch progress.`}catch(e){$('result').textContent=e.message}};
$('analyze').onclick=async()=>{try{const t=await getTab();$('result').textContent='Analyzing…';const j=await call('/analyze',{url:t.url});$('result').textContent=`${j.title||'Untitled'}\n${j.formats?.length||0} formats available.`}catch(e){$('result').textContent=e.message}};
