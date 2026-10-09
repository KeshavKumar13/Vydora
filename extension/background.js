const API = 'http://127.0.0.1:17653';

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'mediafetch-download',
    title: 'Send page to Vydora',
    contexts: ['page', 'link']
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.pageUrl || tab?.url;
  if (!url) return;
  const cfg = await chrome.storage.local.get(['token']);
  if (!cfg.token) return;
  await fetch(`${API}/download`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-MediaFetch-Token': cfg.token},
    body: JSON.stringify({url, mode: 'video', quality: 'best'})
  }).catch(() => {});
});
