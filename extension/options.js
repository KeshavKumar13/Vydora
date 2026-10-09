const token=document.getElementById('token'),msg=document.getElementById('msg');
chrome.storage.local.get(['token']).then(x=>token.value=x.token||'');
document.getElementById('save').onclick=async()=>{await chrome.storage.local.set({token:token.value.trim()});msg.textContent=' Saved.';setTimeout(()=>msg.textContent='',1500)};
