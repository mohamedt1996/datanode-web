// DataNode Web: many links, direct links by default, downloads 5-10 s apart.
const $ = id => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem('dnw:' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('dnw:' + k, JSON.stringify(v)); } catch {} },
};

let ids = store.get('ids', []);              // jobs this device is following
let started = new Set(store.get('started', []));
let jobs = [], active = null, pollTimer = null, screenTimer = null, objectUrl = null;
const queue = [];                            // ready jobs waiting for their turn to download
let nextAt = 0, pumpTimer = null;

$('auto').checked = store.get('auto', true);
$('relay').checked = store.get('relay', false);
$('auto').onchange = () => { store.set('auto', $('auto').checked); sync(); };
$('relay').onchange = () => { store.set('relay', $('relay').checked); render(); };

async function api(path, data) {
  const r = await fetch(path, {
    method: data ? 'POST' : 'GET',
    headers: data ? { 'Content-Type': 'application/json', 'X-Requested-With': 'DataNodeWeb' } : {},
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
  return r.json();
}

// ---- downloading -------------------------------------------------------------
// Hidden frames keep this page open. Same-origin relay links also get a
// download attribute, so the browser saves the file instead of showing bytes.
function startDownload(job) {
  if ($('relay').checked) {
    const a = document.createElement('a');
    a.href = `/download/${job.id}`;
    a.download = job.name || '';
    a.rel = 'noreferrer';
    document.body.append(a); a.click(); a.remove();
  } else {
    const f = document.createElement('iframe');
    f.hidden = true;
    f.referrerPolicy = 'no-referrer';
    f.src = job.direct;
    document.body.append(f);
    setTimeout(() => f.remove(), 10 * 60 * 1000);
  }
  started.add(job.id);
  store.set('started', [...started]);
}

function pump() {
  clearTimeout(pumpTimer);
  if (!queue.length) return;
  const wait = nextAt - Date.now();
  if (wait > 0) { pumpTimer = setTimeout(pump, wait); return; }
  const job = queue.shift();
  startDownload(job);
  nextAt = Date.now() + 5000 + Math.random() * 5000;   // 5-10 s until the next one
  render();
  if (queue.length) pumpTimer = setTimeout(pump, nextAt - Date.now());
}

function sync() {
  if (!$('auto').checked) return;
  for (const j of jobs)
    if (j.state === 'ready' && !started.has(j.id) && !queue.some(q => q.id === j.id)) queue.push(j);
  pump();
}

// ---- list --------------------------------------------------------------------
const LABEL = { queued: 'Waiting', resolving: 'Preparing', verification: 'Verifying', ready: 'Ready', error: 'Failed', expired: 'Expired' };

function render() {
  const ul = $('jobs');
  ul.replaceChildren();
  let ready = 0, working = 0, failed = 0;
  for (const j of jobs) {
    if (j.state === 'ready') ready++; else if (j.state === 'error' || j.state === 'expired') failed++; else working++;
    const li = document.createElement('li');
    const name = document.createElement('span'); name.className = 'name'; name.textContent = j.name || j.id;
    const st = document.createElement('span'); st.className = 'st ' + j.state;
    const queuedAt = queue.findIndex(q => q.id === j.id);
    st.textContent = j.state === 'ready'
      ? (started.has(j.id) ? 'Started on this device' : queuedAt >= 0 ? `Next in line (#${queuedAt + 1})` : 'Ready')
      : LABEL[j.state] || j.state;
    li.append(name, st);
    if (j.state !== 'ready') {
      const m = document.createElement('p'); m.className = 'msg'; m.textContent = j.message || ''; li.append(m);
    } else {
      const links = document.createElement('div'); links.className = 'links';
      const dl = document.createElement('a');
      dl.className = 'primary'; dl.textContent = started.has(j.id) ? 'Download again' : 'Download now';
      dl.href = '#'; dl.onclick = e => { e.preventDefault(); startDownload(j); render(); };
      const direct = document.createElement('a');
      direct.className = 'direct'; direct.href = j.direct; direct.rel = 'noreferrer'; direct.target = '_blank';
      direct.textContent = 'Direct link ↗'; direct.title = j.direct;
      const via = document.createElement('a');
      via.className = 'via'; via.href = `/download/${j.id}`; via.download = j.name || '';
      via.textContent = 'Through server ↓';
      links.append(dl, direct, via);
      li.append(links);
    }
    ul.append(li);
  }
  $('summary').textContent = jobs.length ? `${ready} ready · ${working} in progress · ${failed} failed` : 'No links yet';
  $('copyall').disabled = !ready;
  $('result').hidden = !jobs.length && !$('message').textContent;
}

async function poll() {
  clearTimeout(pollTimer);
  if (!ids.length) { jobs = []; render(); return; }
  try {
    const r = await api('/api/jobs?ids=' + ids.map(encodeURIComponent).join(','));
    jobs = r.jobs; active = r.active;
    render(); sync();
    const busy = jobs.some(j => ['queued', 'resolving', 'verification'].includes(j.state));
    if (!busy) { clearInterval(screenTimer); $('help').open = false; }
    pollTimer = setTimeout(poll, busy ? 2000 : 8000);
  } catch (e) {
    $('message').textContent = e.message;
    pollTimer = setTimeout(poll, 5000);
  }
}

$('form').onsubmit = async e => {
  e.preventDefault();
  const urls = $('urls').value.split(/\s+/).map(s => s.trim()).filter(Boolean);
  if (!urls.length) return;
  $('submit').disabled = true; $('message').textContent = 'Adding your links…'; $('result').hidden = false;
  try {
    const r = await api('/api/jobs', { urls });
    ids = [...ids, ...r.ids]; store.set('ids', ids);
    $('message').textContent = r.rejected.length
      ? `${r.ids.length} added. Skipped ${r.rejected.length}: ${r.rejected[0].error}`
      : `${r.ids.length} link${r.ids.length === 1 ? '' : 's'} added. Keep this page open.`;
    $('urls').value = '';
    poll();
  } catch (err) { $('message').textContent = err.message; }
  $('submit').disabled = false;
};

$('copyall').onclick = async () => {
  const text = jobs.filter(j => j.state === 'ready').map(j => j.direct).join('\n');
  try { await navigator.clipboard.writeText(text); $('message').textContent = 'Direct links copied.'; }
  catch { $('urls').value = text; $('message').textContent = 'Copying is blocked here; the links are in the box above.'; }
};
$('clear').onclick = () => {
  ids = []; started = new Set(); queue.length = 0;
  store.set('ids', []); store.set('started', []);
  $('message').textContent = ''; poll();
};

// ---- browser help (manual verification) --------------------------------------
async function screen() {
  if (!$('help').open) return;
  if (!active || !ids.includes(active)) { $('screenstatus').textContent = 'No link of yours is in the browser right now.'; $('screen').hidden = true; return; }
  try {
    const r = await fetch(`/api/jobs/${active}/screen`);
    if (!r.ok) throw new Error(await r.text());
    const blob = await r.blob();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(blob);
    $('screen').src = objectUrl; $('screen').hidden = false;
    $('screenstatus').textContent = 'Tap the checkbox if verification is waiting.';
  } catch (e) { $('screenstatus').textContent = e.message; $('screen').hidden = true; }
}
$('help').addEventListener('toggle', () => {
  clearInterval(screenTimer);
  if ($('help').open) { screen(); screenTimer = setInterval(screen, 3000); }
});
$('refresh').onclick = screen;
$('screen').onclick = async e => {
  const box = e.target.getBoundingClientRect();
  try {
    await api(`/api/jobs/${active}/click`, { x: (e.clientX - box.left) / box.width, y: (e.clientY - box.top) / box.height });
    setTimeout(screen, 600);
  } catch (err) { $('screenstatus').textContent = err.message; }
};

poll();
