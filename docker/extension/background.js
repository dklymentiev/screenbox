// Screenbox Chrome Extension v2.0.1 -- Background Service Worker
// Minimal extension: no content scripts, no persistent DOM modifications, no console output.
// All page interaction via chrome.scripting.executeScript() on demand.

// ===== CONFIG =====
// SCREENBOX_CONFIG_START
let wsPort = 8765;
let desktopId = 'default';
let wsToken = '';
// SCREENBOX_CONFIG_END

let ws = null;
let reconnectInterval = null;
let keepAliveInterval = null;

// ===== WEBSOCKET =====

function connect() {
  if (!wsPort) return;
  if (ws && ws.readyState === WebSocket.OPEN) return;
  if (ws) { try { ws.close(); } catch(e) {} ws = null; }

  const wsUrl = 'ws://127.0.0.1:' + wsPort + '/extension?id=' + desktopId + (wsToken ? '&token=' + wsToken : '');
  try { ws = new WebSocket(wsUrl); } catch(e) { scheduleReconnect(); return; }

  ws.onopen = () => {
    chrome.action.setBadgeText({ text: 'ON' });
    chrome.action.setBadgeBackgroundColor({ color: '#4CAF50' });
    if (reconnectInterval) { clearInterval(reconnectInterval); reconnectInterval = null; }
    if (keepAliveInterval) clearInterval(keepAliveInterval);
    keepAliveInterval = setInterval(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send('{"type":"ping"}'); }, 20000);
    ws.send(JSON.stringify({ type: 'hello', client: 'screenbox-extension' }));
  };

  ws.onmessage = async (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'pong') return;
      const result = await handleCommand(msg);
      if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: 'response', id: msg.id, result }));
    } catch (err) {
      if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: 'error', id: 0, error: err.message }));
    }
  };

  ws.onclose = () => {
    chrome.action.setBadgeText({ text: 'OFF' });
    chrome.action.setBadgeBackgroundColor({ color: '#F44336' });
    if (keepAliveInterval) { clearInterval(keepAliveInterval); keepAliveInterval = null; }
    scheduleReconnect();
  };
  ws.onerror = () => {};
}

function scheduleReconnect() { if (!reconnectInterval) reconnectInterval = setInterval(connect, 2000); }

// ===== COMMAND HANDLER =====

async function handleCommand(msg) {
  switch (msg.type) {
    case 'ping': return { pong: true };
    case 'navigate': return navigateTo(msg.url);
    case 'getTabs': return getTabs();
    case 'switchTab': return switchTab(msg.tabId);
    case 'newTab': return createNewTab(msg.url);
    case 'closeTab': return closeTab(msg.tabId);
    case 'screenshot': return takeScreenshot();
    case 'getPageInfo': return exec(fnPageInfo);
    case 'getSemantics': return getSemantics(msg.compact !== false);
    case 'extract': return extractAtPoint(msg.x, msg.y, msg.maxChars || 5000);
    case 'pageRead': return pageRead(msg.maxChars || 15000);
    case 'viewRead': return viewRead(msg.maxChars || 8000);
    case 'getWindowBounds': return getWindowBounds();
    case 'getContent': return exec(fnGetContent, [msg.selector]);
    case 'getContentAll': return exec(fnGetContentAll, [msg.selector]);
    case 'getElementRect': return exec(fnGetElementRect, [msg.selector]);
    case 'click': return exec(fnClick, [msg.selector]);
    case 'type': return exec(fnType, [msg.selector, msg.text, msg.clear]);
    case 'press': return exec(fnPress, [msg.key]);
    case 'scroll': return exec(fnScroll, [msg.x || 0, msg.y || 0]);
    case 'scrollToElement': return exec(fnScrollToElement, [msg.selector, msg.elementId, msg.options || {}]);
    case 'scrollContainer': return exec(fnScrollContainer, [msg.containerId, msg.x || 0, msg.y || 0, msg.options || {}]);
    case 'select': return exec(fnSelect, [msg.selector, msg.value]);
    case 'waitFor': return exec(fnWaitFor, [msg.selector, msg.timeout || 10000]);
    case 'waitForStable': return exec(fnWaitForStable, [msg.options || {}]);
    case 'verifyPoint': return exec(fnVerifyPoint, [msg.x, msg.y, msg.expectedId || null]);
    case 'execute': return exec(fnExecute, [msg.script]);
    case 'focusWindow': {
      const [t] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (t) await chrome.windows.update(t.windowId, { focused: true });
      return { success: true };
    }
    default: throw new Error('Unknown command: ' + msg.type);
  }
}

// ===== EXEC HELPER =====

async function exec(func, args = []) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab');
  const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args });
  return results[0]?.result || { error: 'No result' };
}

// ===== NAVIGATION + TABS + SCREENSHOT =====

async function navigateTo(url) { const [t] = await chrome.tabs.query({ active: true, currentWindow: true }); await chrome.tabs.update(t.id, { url }); return { success: true, url, title: '' }; }
async function getTabs() { return (await chrome.tabs.query({ currentWindow: true })).map(t => ({ id: t.id, url: t.url, title: t.title, active: t.active })); }
async function switchTab(id) { await chrome.tabs.update(id, { active: true }); return { success: true }; }
async function createNewTab(url) { const t = await chrome.tabs.create({ url: url || 'about:blank' }); return { tabId: t.id, url: t.url }; }
async function closeTab(id) { if (id) await chrome.tabs.remove(id); else { const [t] = await chrome.tabs.query({ active: true, currentWindow: true }); await chrome.tabs.remove(t.id); } return { success: true }; }
async function takeScreenshot() { return { screenshot: await chrome.tabs.captureVisibleTab(null, { format: 'jpeg', quality: 85 }) }; }

// ===== SEMANTICS =====

async function getSemantics(compact) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab');
  const r = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: (c) => {
    const sels = 'a,button,input,select,textarea,[role="button"],[role="link"],[role="tab"],[role="menuitem"],[role="checkbox"],[role="radio"],[role="textbox"],[role="option"],[role="switch"],[onclick],[tabindex]:not([tabindex="-1"])';
    const els = document.querySelectorAll(sels); const res = [];
    for (const el of els) { const r = el.getBoundingClientRect(); if (!r.width || !r.height || r.bottom < 0 || r.top > innerHeight) continue; const s = getComputedStyle(el); if (s.display==='none'||s.visibility==='hidden'||+s.opacity===0) continue; const tag = el.tagName.toLowerCase(); const e = { i: res.length+1, t: el.getAttribute('role')||tag, l: (el.textContent||el.value||el.placeholder||el.getAttribute('aria-label')||'').trim().slice(0,100), r: [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)] }; if (!c) { e.tag=tag; e.id=el.id||undefined; e.href=el.href||undefined; e.type=el.type||undefined; } res.push(e); }
    return { u: location.href, t: document.title, v: [innerWidth, innerHeight], n: res.length, e: res };
  }, args: [compact] });
  return r[0]?.result || { error: 'No result' };
}

// ===== PAGE READ =====

async function pageRead(maxChars) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab');
  const r = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: (limit) => {
    const cands = [document.querySelector('article'), document.querySelector('[role="main"]'), document.querySelector('main'), document.querySelector('.article-body,.caas-body,.post-content,.entry-content,.story-body')].filter(Boolean);
    let best = null, bestLen = 0;
    for (const el of cands) { const l = (el.innerText||'').length; if (l > bestLen && l <= limit*2) { best = el; bestLen = l; } }
    if (!best) { for (const el of document.querySelectorAll('div,section,article')) { const l = (el.innerText||'').length; if (l < 200 || l > limit*3) continue; const t = el.tagName.toLowerCase(); const role = el.getAttribute('role')||''; const cls = (el.className||'').toLowerCase(); if (['nav','header','footer','aside'].includes(t)||['navigation','banner','contentinfo','complementary'].includes(role)||cls.match(/nav|header|footer|sidebar|menu|ad|banner|comment/)) continue; if (l > bestLen) { best = el; bestLen = l; } } }
    if (!best) best = document.body;
    const text = (best.innerText||'').slice(0,limit); const tag = best.tagName.toLowerCase(); let sel = tag; if (best.id) sel='#'+best.id; else if (best.className && typeof best.className==='string') { const c = best.className.trim().split(/\s+/).filter(c=>c&&!c.includes(':')).slice(0,2).join('.'); if (c) sel=tag+'.'+c; }
    return { text, tag, selector: sel, chars: text.length, title: document.title, url: location.href };
  }, args: [maxChars] });
  return r[0]?.result || { error: 'No result' };
}

// ===== VIEW READ =====

async function viewRead(maxChars) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab');
  const r = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: (limit) => {
    const vh = innerHeight, sy = scrollY, dh = document.documentElement.scrollHeight, rem = dh-sy-vh, sl = Math.max(0, Math.round(rem/vh));
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, { acceptNode: n => { const t = n.tagName.toLowerCase(); if (['script','style','noscript','svg','path'].includes(t)) return 2; const s = getComputedStyle(n); if (s.display==='none'||s.visibility==='hidden') return 2; return ['p','h1','h2','h3','h4','h5','h6','li','td','th','blockquote','figcaption','pre','code','label','span','a','div'].includes(t) ? 1 : 3; } });
    const seen = new Set(); let col = '', node;
    while ((node = walker.nextNode())) { const r = node.getBoundingClientRect(); if (r.bottom<0||r.top>vh||r.width<5||r.height<5) continue; const text = (node.innerText||'').trim(); if (!text||text.length<3||seen.has(text)) continue; let dom = false; for (const s of seen) if (s.includes(text)) { dom=true; break; } if (dom) continue; for (const s of seen) if (text.includes(s)) seen.delete(s); seen.add(text); if (col.length+text.length>limit) break; col += (['h1','h2','h3','h4','h5','h6'].includes(node.tagName.toLowerCase()) ? '\n\n'+text+'\n' : '\n'+text); }
    return { text: col.trim(), chars: col.trim().length, scroll: sl>0?'down':'none', remaining: sl>0?'~'+sl+' screens':null, viewport:{width:innerWidth,height:vh}, scrollY:Math.round(sy), docHeight:Math.round(dh), url:location.href };
  }, args: [maxChars] });
  return r[0]?.result || { error: 'No result' };
}

// ===== EXTRACT AT POINT =====

async function extractAtPoint(x, y, maxChars) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab');
  const r = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: (px, py, limit) => {
    let el = document.elementFromPoint(px, py); if (!el) return { error: 'No element', x: px, y: py };
    const stop = new Set(['body','html','header','footer','nav']), prefer = new Set(['article','section','main','aside','blockquote']);
    let best = el, bestLen = (el.innerText||'').length, cur = el;
    while (cur.parentElement) { cur = cur.parentElement; const tag = cur.tagName.toLowerCase(); if (stop.has(tag)) break; const len = (cur.innerText||'').length; if (len > bestLen && len <= limit) { best = cur; bestLen = len; } if (prefer.has(tag) && len <= limit) { best = cur; break; } if (len > limit) break; }
    const text = (best.innerText||'').slice(0,limit); const tag = best.tagName.toLowerCase(); const rect = best.getBoundingClientRect(); let sel = tag; if (best.id) sel='#'+best.id;
    return { text, tag, chars: text.length, selector: sel, rect: [Math.round(rect.x),Math.round(rect.y),Math.round(rect.width),Math.round(rect.height)], point:{x:px,y:py} };
  }, args: [x, y, maxChars] });
  return r[0]?.result || { error: 'No result' };
}

// ===== WINDOW BOUNDS =====

async function getWindowBounds() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error('No active tab');
  const win = await chrome.windows.get(tab.windowId);
  const r = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => ({ viewport:{width:innerWidth,height:innerHeight}, screen:{width:screen.width,height:screen.height}, devicePixelRatio, scrollX, scrollY }) });
  return { window: { top: win.top, left: win.left, width: win.width, height: win.height }, ...(r[0]?.result||{}) };
}

// ===== ON-DEMAND PAGE FUNCTIONS =====

function fnPageInfo() {
  const els = document.querySelectorAll('a,button,input,select,textarea,[role="button"],[onclick]'); const elements = [];
  els.forEach((el, i) => { if (i<100) { const r = el.getBoundingClientRect(); if (r.width>0&&r.height>0) elements.push({ tag:el.tagName.toLowerCase(), id:el.id||null, text:(el.textContent||'').trim().substring(0,50), type:el.type||null, href:el.href||null, rect:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)} }); } });
  return { url:location.href, title:document.title, viewport:{width:innerWidth,height:innerHeight,scrollHeight:document.body.scrollHeight}, elements };
}

function fnGetContent(sel) { if (sel) { const el=document.querySelector(sel); if(!el) return{error:'Not found: '+sel}; return{text:el.innerText,html:el.innerHTML,value:el.value||null}; } return{text:document.body.innerText.substring(0,10000),title:document.title,url:location.href}; }
function fnGetContentAll(sel) { if(!sel) return{elements:[{index:0,text:document.body.innerText.substring(0,50000),tag:'body'}],count:1}; const els=document.querySelectorAll(sel);const r=[];els.forEach((el,i)=>{const t=el.innerText||'';if(t.trim())r.push({index:i,text:t.substring(0,10000),tag:el.tagName.toLowerCase(),id:el.id||null});}); return{elements:r,count:r.length,selector:sel}; }
function fnGetElementRect(sel) { const el=document.querySelector(sel);if(!el)return{error:'Not found: '+sel};const r=el.getBoundingClientRect();return{rect:{x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height,viewportX:r.x,viewportY:r.y},tag:el.tagName.toLowerCase(),text:(el.innerText||el.value||'').substring(0,100)}; }

function fnClick(sel) { const el=document.querySelector(sel);if(!el)return{error:'Not found: '+sel};el.scrollIntoView({behavior:'smooth',block:'center'});const r=el.getBoundingClientRect();const x=r.left+r.width/2,y=r.top+r.height/2;for(const t of['mouseenter','mouseover','mousedown','mouseup','click'])el.dispatchEvent(new MouseEvent(t,{view:window,bubbles:true,cancelable:true,clientX:x,clientY:y}));return{success:true,selector:sel}; }
function fnType(sel,text,clear) { const el=document.querySelector(sel);if(!el)return{error:'Not found: '+sel};el.focus();if(clear){el.value='';el.dispatchEvent(new Event('input',{bubbles:true}));}for(const c of text){el.dispatchEvent(new KeyboardEvent('keydown',{key:c,bubbles:true}));if(el.tagName==='INPUT'||el.tagName==='TEXTAREA')el.value+=c;else if(el.isContentEditable)el.textContent+=c;el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new KeyboardEvent('keyup',{key:c,bubbles:true}));}el.dispatchEvent(new Event('change',{bubbles:true}));return{success:true,selector:sel,text}; }
function fnPress(key) { const m={Enter:{key:'Enter',code:'Enter',keyCode:13},Tab:{key:'Tab',code:'Tab',keyCode:9},Escape:{key:'Escape',code:'Escape',keyCode:27},Backspace:{key:'Backspace',code:'Backspace',keyCode:8},ArrowDown:{key:'ArrowDown',code:'ArrowDown',keyCode:40},ArrowUp:{key:'ArrowUp',code:'ArrowUp',keyCode:38}};const info=m[key]||{key,code:key,keyCode:key.charCodeAt(0)};const t=document.activeElement||document.body;t.dispatchEvent(new KeyboardEvent('keydown',{...info,bubbles:true}));t.dispatchEvent(new KeyboardEvent('keyup',{...info,bubbles:true}));return{success:true,key}; }
function fnScroll(x,y) { scrollBy({left:x,top:y,behavior:'smooth'});return{success:true,scrollX,scrollY}; }

function fnScrollToElement(sel, eid, opts) { const{behavior='smooth',block='center'}=opts;let el=sel?document.querySelector(sel):null;if(!el&&eid){const all=document.querySelectorAll('a,button,input,select,textarea,[role="button"],[tabindex]');el=all[eid-1];}if(!el)return{error:'Not found'};el.scrollIntoView({behavior,block});const r=el.getBoundingClientRect();return{success:true,element:{tag:el.tagName.toLowerCase(),text:(el.textContent||'').slice(0,50).trim()},nowVisible:r.top>=0&&r.bottom<=innerHeight}; }
function fnScrollContainer(cid, x, y, opts) { const{behavior='smooth',absolute=false}=opts;if(cid===1){if(absolute)scrollTo({left:x,top:y,behavior});else scrollBy({left:x,top:y,behavior});return{success:true,containerId:1,scrollX,scrollY};}const sc=[];document.querySelectorAll('*').forEach(el=>{const s=getComputedStyle(el);if((s.overflowY==='auto'||s.overflowY==='scroll')&&el.scrollHeight>el.clientHeight+10)sc.push(el);});const c=sc[cid-2];if(!c)return{error:'Container not found'};if(absolute)c.scrollTo({left:x,top:y,behavior});else c.scrollBy({left:x,top:y,behavior});return{success:true,containerId:cid,scrollTop:Math.round(c.scrollTop)}; }
function fnSelect(sel, val) { const el=document.querySelector(sel);if(!el||el.tagName!=='SELECT')return{error:'SELECT not found'};const opt=Array.from(el.options).find(o=>o.text===val||o.value===val);if(!opt)return{error:'Option not found: '+val};el.value=opt.value;el.dispatchEvent(new Event('change',{bubbles:true}));return{success:true,selector:sel,value:val}; }

function fnWaitFor(sel, timeout) { return new Promise(res => { const s=Date.now(); const ck=()=>{if(document.querySelector(sel))res({success:true,found:true,selector:sel});else if(Date.now()-s>timeout)res({success:false,found:false,error:'Timeout'});else setTimeout(ck,100);}; ck(); }); }
function fnWaitForStable(opts) { const{timeout=5000,checkInterval=100,stableCount=3}=opts; return new Promise(res=>{ const s=Date.now();let lh='',st=0; const ck=()=>{const els=document.querySelectorAll('button,a,input,[role="dialog"],.modal');let h='';els.forEach((el,i)=>{if(i>30)return;const r=el.getBoundingClientRect();h+=r.x+','+r.y+','+r.width+';';});if(h===lh){st++;if(st>=stableCount)return res({stable:true,reason:'dom_stable',took:Date.now()-s});}else st=0;lh=h;if(Date.now()-s>timeout)return res({stable:false,reason:'timeout',took:Date.now()-s});setTimeout(ck,checkInterval);};ck();}); }
function fnVerifyPoint(x, y) { const el=document.elementFromPoint(x,y);if(!el)return{success:false,error:'No element',point:{x,y}};const r=el.getBoundingClientRect();let text=el.value||(el.textContent||'').trim();if(text.length>50)text=text.substring(0,47)+'...';return{success:true,point:{x,y},element:{tag:el.tagName.toLowerCase(),id:el.id||null,text,rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]}}; }
function fnExecute(script) { try{const f=/\b(import|require|fetch|XMLHttpRequest|Worker)\s*\(/i;if(f.test(script))return{success:false,error:'Forbidden API'};return{success:true,result:new Function('"use strict";return('+script+')')()};}catch(e){return{success:false,error:e.message};} }

// ===== INIT =====
chrome.runtime.onInstalled.addListener(() => connect());
connect();
