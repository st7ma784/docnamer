'use strict';

// ── State ─────────────────────────────────────────────────────────────────────

const state = {
  config:           JSON.parse(localStorage.getItem('docnamer_config') || '{}'),
  mailOk:           false,
  currentJobId:     null,
  evtSource:        null,
  reviewed:         JSON.parse(localStorage.getItem('docnamer_reviewed') || '{}'),
  lastProgressFetch: 0,
};

// ── API ───────────────────────────────────────────────────────────────────────

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text() || r.statusText);
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.text();
}
const GET    = p      => api('GET',    p);
const POST   = (p, b) => api('POST',   p, b);
const DELETE = p      => api('DELETE', p);

// ── Tabs ─────────────────────────────────────────────────────────────────────

function showTab(idx) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.step-item').forEach(s => s.classList.remove('active'));
  document.getElementById(`tab-${idx}`)?.classList.add('active');
  document.querySelector(`[data-tab="${idx}"]`)?.classList.add('active');
}

document.querySelectorAll('.step-item').forEach(el =>
  el.addEventListener('click', () => {
    const idx = Number(el.dataset.tab);
    showTab(idx);
    if (idx === 2) { updateConfigBar(); loadWatchConfig(); }
    if (idx === 3) loadJobs();
  })
);

// ── Tab 0: Configure ──────────────────────────────────────────────────────────

function loadConfig() {
  const c = state.config;
  if (c.clientName) document.getElementById('clientName').value = c.clientName;
  if (c.clientCode) document.getElementById('clientCode').value = c.clientCode;
  if (c.dateFrom)   document.getElementById('dateFrom').value   = c.dateFrom;
  if (c.dateTo)     document.getElementById('dateTo').value     = c.dateTo;
}

function saveConfig() {
  const name = document.getElementById('clientName').value.trim();
  const code = document.getElementById('clientCode').value.trim();
  const from = document.getElementById('dateFrom').value;
  const to   = document.getElementById('dateTo').value;
  if (!name || !code || !from || !to) { alert('Please fill in all fields.'); return; }
  if (from > to) { alert('"From" date must be before "To" date.'); return; }
  state.config = { clientName: name, clientCode: code, dateFrom: from, dateTo: to };
  localStorage.setItem('docnamer_config', JSON.stringify(state.config));
  document.querySelector('[data-tab="0"]').classList.add('done');
  showTab(1);
  loadMailConfig();
}

// ── Tab 1: Mail server ────────────────────────────────────────────────────────

async function loadMailConfig() {
  try {
    const cfg = await GET('/mail/config');
    if (cfg.host)     document.getElementById('imapHost').value    = cfg.host;
    if (cfg.port)     document.getElementById('imapPort').value    = cfg.port;
    if (cfg.username) document.getElementById('imapUser').value    = cfg.username;
    if (cfg.mailbox)  document.getElementById('imapMailbox').value = cfg.mailbox;
    document.getElementById('imapSSL').checked = cfg.use_ssl !== false;
    if (cfg.password_set) {
      document.getElementById('imapPass').placeholder = '(saved — leave blank to keep)';
    }
    const { configured } = await GET('/mail/status');
    state.mailOk = configured;
    if (configured) {
      setMailBadge('saved');
      document.getElementById('continueMailBtn').style.display = 'inline-flex';
      document.querySelector('[data-tab="1"]').classList.add('done');
    }
  } catch { /* first visit */ }
}

async function saveMailConfig() {
  const host    = document.getElementById('imapHost').value.trim();
  const port    = parseInt(document.getElementById('imapPort').value) || 993;
  const username= document.getElementById('imapUser').value.trim();
  const password= document.getElementById('imapPass').value;
  const mailbox = document.getElementById('imapMailbox').value.trim() || 'INBOX';
  const use_ssl = document.getElementById('imapSSL').checked;
  if (!host || !username) { alert('Host and username are required.'); return; }
  try {
    await POST('/mail/config', { host, port, username, password, use_ssl, mailbox });
    state.mailOk = true;
    setMailBadge('saved');
    document.querySelector('[data-tab="1"]').classList.add('done');
    document.getElementById('continueMailBtn').style.display = 'inline-flex';
  } catch (e) { alert('Failed to save: ' + e.message); }
}

async function testMailConfig() {
  const btn = document.getElementById('testBtn');
  const res = document.getElementById('testResult');
  btn.disabled = true; btn.textContent = 'Testing…';
  res.style.display = 'none';
  try {
    const { ok, message } = await POST('/mail/test');
    res.style.display = 'block';
    res.className = `callout callout-${ok ? 'info' : 'warn'}`;
    res.innerHTML = `<strong>${ok ? '✓ Success' : '✗ Failed'}:</strong> ${escHtml(message)}`;
    if (ok) {
      state.mailOk = true;
      setMailBadge('connected');
      document.getElementById('continueMailBtn').style.display = 'inline-flex';
      document.querySelector('[data-tab="1"]').classList.add('done');
    }
  } catch (e) {
    res.style.display = 'block';
    res.className = 'callout callout-error';
    res.textContent = 'Error: ' + e.message;
  } finally { btn.disabled = false; btn.textContent = 'Test connection'; }
}

function setMailBadge(s) {
  const badge = document.getElementById('mailBadge');
  const label = document.getElementById('mailLabel');
  const sub   = document.getElementById('mailSub');
  if (s === 'connected') {
    badge.className = 'badge badge-connected';
    badge.innerHTML = '<span class="dot"></span> Connected';
    label.textContent = 'Connection verified';
    sub.textContent   = 'Ready to scan.';
  } else if (s === 'saved') {
    badge.className = 'badge badge-running';
    badge.innerHTML = '<span class="dot"></span> Config saved';
    label.textContent = 'Settings saved — click Test to verify';
    sub.textContent   = 'Credentials stored locally on this server.';
  } else {
    badge.className = 'badge badge-disconnected';
    badge.innerHTML = '<span class="dot"></span> Not configured';
    label.textContent = 'Enter your IMAP details below';
    sub.textContent   = 'Credentials stored locally on this server only.';
  }
}

// ── Tab 2: Process ────────────────────────────────────────────────────────────

function updateConfigBar() {
  const c = state.config;
  const bar = document.getElementById('configBar');
  if (!c.clientName) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  bar.innerHTML = `
    <strong>${escHtml(c.clientName)}</strong>
    <span class="separator">|</span>
    <span>Code: <strong>${escHtml(c.clientCode)}</strong></span>
    <span class="separator">|</span>
    <span>${c.dateFrom} → ${c.dateTo}</span>`;
}

function logLine(message, level = 'info') {
  const log = document.getElementById('log');
  const d = document.createElement('div');
  d.className = `log-${level}`;
  d.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

function setProgress(pct, label, countLabel = '') {
  document.getElementById('progressFill').style.width  = `${pct}%`;
  document.getElementById('progressLabel').textContent = label;
  document.getElementById('progressCount').textContent = countLabel;
  setTitle(countLabel || label);
}

async function startScan() {
  const c = state.config;
  if (!c.clientName) { alert('Complete Step 1 first.'); showTab(0); return; }
  const { configured } = await GET('/mail/status').catch(() => ({ configured: false }));
  if (!configured) { alert('Configure the mail server first (Step 2).'); showTab(1); return; }

  requestNotify();

  document.getElementById('startBtn').style.display   = 'none';
  document.getElementById('cancelBtn').style.display  = 'inline-flex';
  document.getElementById('scanProgress').style.display = 'block';
  document.getElementById('scanDone').style.display    = 'none';
  document.getElementById('log').innerHTML = '';
  setProgress(3, `Scanning ${c.clientName} · ${c.dateFrom} → ${c.dateTo}`);

  try {
    const { job_id } = await POST('/jobs', {
      client_name: c.clientName, client_code: c.clientCode,
      date_from: c.dateFrom, date_to: c.dateTo,
    });
    state.currentJobId = job_id;
    listenToJob(job_id);
  } catch (e) {
    logLine('Failed to start: ' + e.message, 'error');
    document.getElementById('startBtn').style.display  = 'inline-flex';
    document.getElementById('cancelBtn').style.display = 'none';
  }
}

async function cancelScan() {
  if (!state.currentJobId) return;
  if (!confirm('Cancel the running scan?')) return;
  try {
    await DELETE(`/jobs/${state.currentJobId}`);
    document.getElementById('cancelBtn').style.display  = 'none';
    document.getElementById('startBtn').style.display   = 'inline-flex';
  } catch (e) { alert('Could not cancel: ' + e.message); }
}

function listenToJob(jobId) {
  if (state.evtSource) state.evtSource.close();
  const es = new EventSource(`/jobs/${jobId}/events`);
  state.evtSource = es;

  es.onmessage = async (e) => {
    const data = JSON.parse(e.data);

    if (data.type === 'done') {
      es.close();
      document.getElementById('cancelBtn').style.display  = 'none';
      document.getElementById('startBtn').style.display   = 'inline-flex';
      document.getElementById('startBtn').disabled        = false;

      const ok = data.status === 'completed';
      const n  = data.total_documents || 0;
      setProgress(100, ok ? 'Complete' : data.status, '');
      setTitle(ok ? `${n} docs ready` : 'Scan ' + data.status);

      if (ok && n > 0) {
        const dlEl = document.getElementById('scanDoneDownload');
        dlEl.href = `/jobs/${jobId}/download-all`;
        document.getElementById('scanDoneTitle').textContent =
          `✓ Scan complete — ${n} document${n === 1 ? '' : 's'} named`;
        document.getElementById('scanDone').style.display = 'flex';
        document.querySelector('[data-tab="2"]').classList.add('done');
        notify(`Scan complete — ${n} document${n === 1 ? '' : 's'} ready`);
        loadJobs();
      } else if (ok) {
        // Completed but 0 docs — log already has the diagnosis
        document.getElementById('scanDone').style.display = 'none';
      }
      return;
    }

    logLine(data.message, data.level || 'info');

    const now = Date.now();
    if (now - state.lastProgressFetch >= 1000) {
      state.lastProgressFetch = now;
      GET(`/jobs/${jobId}`).then(job => {
        const total = Math.max(job.total_emails, 1);
        const done  = job.processed_emails || 0;
        setProgress(
          Math.round(3 + (done / total) * 92),
          done ? `Processing email ${done} of ${total}…` : 'Fetching emails…',
          done ? `${done} / ${total}` : ''
        );
      }).catch(() => {});
    }
  };

  es.onerror = () => {
    es.close();
    GET(`/jobs/${jobId}`).then(job => {
      if (job.status === 'running') {
        logLine('Connection dropped — reconnecting…', 'warn');
        setTimeout(() => listenToJob(jobId), 2000);
      }
    }).catch(() => {});
  };
}

function goToResults() { showTab(3); loadJobs(); }

// ── Watch mode ────────────────────────────────────────────────────────────────

async function loadWatchConfig() {
  try {
    const cfg = await GET('/watch');
    const toggle = document.getElementById('watchToggle');
    toggle.checked = !!cfg.enabled;
    if (cfg.interval_minutes) {
      const sel = document.getElementById('watchInterval');
      if (sel) sel.value = String(cfg.interval_minutes);
    }
    if (cfg.lookback_days) {
      const sel = document.getElementById('watchLookback');
      if (sel) sel.value = String(cfg.lookback_days);
    }
    renderWatchStatus(cfg);
    document.getElementById('watchConfig').style.display = cfg.enabled ? 'block' : 'none';
  } catch { /* not critical */ }
}

function renderWatchStatus(cfg) {
  const badge  = document.getElementById('watchBadge');
  const status = document.getElementById('watchStatus');
  if (cfg.enabled) {
    badge.className = 'badge badge-running';
    badge.innerHTML = '<span class="dot"></span> Active';
    if (cfg.last_run_at && status) {
      const ago = timeSince(cfg.last_run_at);
      status.textContent = `Last checked: ${ago} · checks every ${cfg.interval_minutes} min, looking back ${cfg.lookback_days} day(s)`;
    }
  } else {
    badge.className = 'badge badge-disconnected';
    badge.innerHTML = '<span class="dot"></span> Off';
    if (status) status.textContent = '';
  }
}

function timeSince(isoStr) {
  const sec = Math.round((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (sec < 60)  return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec/60)}m ago`;
  return `${Math.round(sec/3600)}h ago`;
}

async function toggleWatch() {
  const enabled  = document.getElementById('watchToggle').checked;
  const cfgEl    = document.getElementById('watchConfig');
  cfgEl.style.display = enabled ? 'block' : 'none';

  const c = state.config;
  if (enabled && (!c.clientName || !c.clientCode)) {
    alert('Complete Step 1 (client name & code) before enabling watch mode.');
    document.getElementById('watchToggle').checked = false;
    cfgEl.style.display = 'none';
    return;
  }

  const interval = parseInt(document.getElementById('watchInterval')?.value || '10');
  const lookback = parseInt(document.getElementById('watchLookback')?.value || '7');

  try {
    const result = await POST('/watch', {
      enabled,
      client_name:      c.clientName || '',
      client_code:      c.clientCode || '',
      interval_minutes: interval,
      lookback_days:    lookback,
    });
    renderWatchStatus(result);
  } catch (e) {
    alert('Could not update watch mode: ' + e.message);
    document.getElementById('watchToggle').checked = !enabled;
    cfgEl.style.display = !enabled ? 'block' : 'none';
  }
}

async function saveWatchSettings() {
  const c = state.config;
  const enabled  = document.getElementById('watchToggle').checked;
  const interval = parseInt(document.getElementById('watchInterval')?.value || '10');
  const lookback = parseInt(document.getElementById('watchLookback')?.value || '7');
  try {
    const result = await POST('/watch', {
      enabled,
      client_name:      c.clientName || '',
      client_code:      c.clientCode || '',
      interval_minutes: interval,
      lookback_days:    lookback,
    });
    renderWatchStatus(result);
  } catch (e) { alert('Could not save watch settings: ' + e.message); }
}

// ── Tab 3: Results ────────────────────────────────────────────────────────────

async function loadJobs() {
  try {
    const jobs = await GET('/jobs');
    renderJobCards(jobs);
  } catch (e) { console.error('Failed to load jobs', e); }
}

function renderJobCards(jobs) {
  const container = document.getElementById('jobCards');
  if (!jobs.length) {
    container.innerHTML = `<div class="no-jobs"><p>No scans yet — complete Steps 1–3 to get started.</p></div>`;
    return;
  }

  container.innerHTML = jobs.map(j => {
    const docLabel = `${j.total_documents} doc${j.total_documents === 1 ? '' : 's'}`;
    const emailLabel = `${j.total_emails} email${j.total_emails === 1 ? '' : 's'}`;
    const canDownload = j.status === 'completed' && j.total_documents > 0;
    const isCurrentJob = j.id === state.currentJobId;

    return `
    <div class="job-card" id="card-${j.id}">
      <div class="job-card-header" onclick="toggleCard('${j.id}')">
        <div>
          <div class="job-card-title">
            <h3>${escHtml(j.client_name)}</h3>
            <span class="code">${escHtml(j.client_code)}</span>
            <span class="badge badge-${j.status}"><span class="dot"></span>${j.status}</span>
          </div>
          <div class="job-card-meta">${j.date_from} → ${j.date_to} &nbsp;·&nbsp; ${docLabel} from ${emailLabel}</div>
        </div>
        <div class="job-card-actions">
          ${canDownload ? `
            <a class="btn-download" style="padding:9px 18px;font-size:.88rem"
               href="/jobs/${j.id}/download-all"
               download="${escHtml(j.client_code)}-${j.date_from}-to-${j.date_to}.zip"
               onclick="event.stopPropagation()">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Download All
            </a>` : ''}
          <span class="job-chevron" style="color:var(--gray-400);font-size:.85rem">${isCurrentJob ? '▲' : '▼'}</span>
        </div>
      </div>
      <div class="job-card-body${isCurrentJob ? ' open' : ''}" id="body-${j.id}">
        <div id="docs-${j.id}">
          <div class="empty-state" style="padding:20px">Loading…</div>
        </div>
      </div>
    </div>`;
  }).join('');

  // Auto-load current job's docs
  if (state.currentJobId) {
    loadDocsForCard(state.currentJobId);
  }
}

function toggleCard(jobId) {
  const body = document.getElementById(`body-${jobId}`);
  const isOpen = body.classList.contains('open');
  // Close all cards and reset their chevrons
  document.querySelectorAll('.job-card-body.open').forEach(b => {
    b.classList.remove('open');
    const chevron = b.closest('.job-card')?.querySelector('.job-chevron');
    if (chevron) chevron.textContent = '▼';
  });
  if (!isOpen) {
    body.classList.add('open');
    const chevron = body.closest('.job-card')?.querySelector('.job-chevron');
    if (chevron) chevron.textContent = '▲';
    loadDocsForCard(jobId);
  }
}

async function loadDocsForCard(jobId) {
  const container = document.getElementById(`docs-${jobId}`);
  if (!container) return;
  try {
    const [job, docs] = await Promise.all([GET(`/jobs/${jobId}`), GET(`/jobs/${jobId}/documents`)]);

    if (!docs.length) {
      container.innerHTML = renderEmptyDocs(job);
      return;
    }

    const unverified = docs.filter(d => !d.client_name_found).length;
    container.innerHTML = `
      ${unverified ? `<div class="callout callout-warn" style="margin-bottom:14px">
        <strong>${unverified} document${unverified === 1 ? '' : 's'} where the client name wasn't confirmed.</strong>
        Click ✗ to mark as reviewed once you've checked it manually.
      </div>` : ''}
      <table>
        <thead>
          <tr>
            <th>Filename</th>
            <th>Date</th>
            <th>Summary</th>
            <th>Pages</th>
            <th>Client</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${docs.map(d => `
            <tr id="row-${d.id}">
              <td class="filename-cell">
                ${escHtml(d.output_filename)}
                <button class="copy-btn" title="Copy filename" onclick="copyText('${escHtml(d.output_filename)}', this)">⎘</button>
              </td>
              <td>${d.doc_date || '—'}</td>
              <td>${escHtml(d.summary)}</td>
              <td>${d.start_page}–${d.end_page}</td>
              <td>${renderClientCell(d)}</td>
              <td>
                <a href="/jobs/${escHtml(d.job_id)}/documents/${encodeURIComponent(d.output_filename)}/download"
                   class="btn btn-outline btn-sm" download title="Download">⬇</a>
              </td>
            </tr>`).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    if (container) container.innerHTML = `<div class="callout callout-error">Error: ${escHtml(e.message)}</div>`;
  }
}

function renderEmptyDocs(job) {
  if (job.status === 'running') {
    return `<div class="empty-state">Scan in progress…</div>`;
  }
  if (job.status === 'completed') {
    return `<div class="diagnosis-card">
      <strong>No documents were produced for this scan.</strong>
      Check the log on the Process tab for details — the most common causes are:
      <ul style="padding-left:18px;margin-top:6px">
        <li>Date range doesn't cover when the scanner emails were received</li>
        <li>Sender address didn't match the photocopier detection keywords</li>
        <li>Emails exist but have no PDF attachments (images-only scans)</li>
        <li>Wrong mailbox / folder name</li>
      </ul>
    </div>`;
  }
  return `<div class="empty-state">${job.status}</div>`;
}

function renderClientCell(doc) {
  const rid = `${doc.job_id}:${doc.id}`;
  if (doc.client_name_found) return '<span class="check" title="Client name confirmed">✓</span>';
  if (state.reviewed[rid])   return `<span class="reviewed" title="Marked as reviewed — click to undo" onclick="toggleReviewed('${rid}')">✓̣</span>`;
  return `<span class="cross" title="Client name not confirmed — click to mark as reviewed" onclick="toggleReviewed('${rid}')">✗</span>`;
}

function toggleReviewed(rid) {
  state.reviewed[rid] = !state.reviewed[rid];
  if (!state.reviewed[rid]) delete state.reviewed[rid];
  localStorage.setItem('docnamer_reviewed', JSON.stringify(state.reviewed));
  // Re-render the cell in place
  const [jobId] = rid.split(':');
  loadDocsForCard(jobId);
}

// ── Clipboard ─────────────────────────────────────────────────────────────────

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    const orig = btn.textContent;
    btn.textContent = '✓';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1500);
  } catch { /* clipboard blocked */ }
}

// ── Browser notifications ─────────────────────────────────────────────────────

function requestNotify() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

function notify(msg) {
  if ('Notification' in window && Notification.permission === 'granted') {
    new Notification('DocNamer', { body: msg });
  }
}

// ── Page title ────────────────────────────────────────────────────────────────

function setTitle(suffix) {
  document.title = suffix ? `DocNamer — ${suffix}` : 'DocNamer';
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Boot ─────────────────────────────────────────────────────────────────────

(async function init() {
  loadConfig();

  // Restore step completion state from server
  const [mailStatus, jobs] = await Promise.allSettled([
    GET('/mail/status'),
    GET('/jobs'),
  ]);

  const configured = mailStatus.status === 'fulfilled' && mailStatus.value.configured;
  const jobList    = jobs.status === 'fulfilled' ? jobs.value : [];

  if (state.config.clientName) {
    document.querySelector('[data-tab="0"]').classList.add('done');
  }
  if (configured) {
    state.mailOk = true;
    document.querySelector('[data-tab="1"]').classList.add('done');
    await loadMailConfig();
  }
  if (jobList.some(j => j.status === 'completed')) {
    document.querySelector('[data-tab="2"]').classList.add('done');
  }

  // Navigate to the furthest meaningful tab automatically
  if (jobList.length) {
    renderJobCards(jobList);
    // If there's a running job, go to the process tab and reconnect
    const running = jobList.find(j => j.status === 'running');
    if (running) {
      state.currentJobId = running.id;
      showTab(2);
      updateConfigBar();
      document.getElementById('scanProgress').style.display = 'block';
      document.getElementById('cancelBtn').style.display    = 'inline-flex';
      document.getElementById('startBtn').style.display     = 'none';
      listenToJob(running.id);
    } else {
      showTab(3);
    }
  } else if (configured && state.config.clientName) {
    showTab(2);
    updateConfigBar();
  } else if (configured) {
    showTab(1);
  }

  // Load watch mode state into the Process tab
  loadWatchConfig();
})();
