    // ---------------------------------------------------------------- formatting
    function fmtUptime(sec) {
      if (sec < 60) return sec + 's';
      if (sec < 3600) return Math.floor(sec/60) + 'm';
      const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60);
      return h < 48 ? h + 'h ' + m + 'm' : Math.floor(h/24) + 'd ' + (h%24) + 'h';
    }

    function fmtNum(n) {
      return n != null ? Number(n).toLocaleString('en-GB') : '—';
    }

    function fmtTs(ts) {
      return String(ts || '').replace('T', ' ').slice(0, 16);
    }

    // Text inside HTML, also as a quoted attribute value
    function _esc(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // An address for href: only http(s), else '#'
    function _href(u) {
      return /^https?:\/\//i.test(String(u || '').trim()) ? _esc(String(u).trim()) : '#';
    }

    // An answer as the user read it, in part: bold and clickable addresses (the text is escaped first)
    function _answerHtml(s) {
      return _esc(s)
        .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
        .replace(/https?:\/\/(?:(?!&(?:quot|#39|lt|gt);)[^\s<])+/g, u => {
          const tail = (u.match(/[.,;:!?)\]]+$/) || [''])[0];
          const url = u.slice(0, u.length - tail.length);
          return '<a href="' + url + '" target="_blank" rel="noopener">' + url + '</a>' + tail;
        });
    }

    // ---------------------------------------------------------------- tabs
    // One section at a time. The open tab is kept in the address (#messages), so a reload keeps it.
    const TABS = Array.from(document.querySelectorAll('.adm-tabs [role="tab"]'));
    let activeTab = '';
    const _loaded = {};

    function showTab(name, focus) {
      const tab = TABS.find(t => t.dataset.tab === name) || TABS[0];
      const changed = activeTab && activeTab !== tab.dataset.tab;
      activeTab = tab.dataset.tab;
      TABS.forEach(t => {
        const on = t === tab;
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
      });
      // the bar scrolls sideways on a phone: keep the chosen tab in view
      const bar = tab.parentElement;
      if (tab.offsetLeft < bar.scrollLeft || tab.offsetLeft + tab.offsetWidth > bar.scrollLeft + bar.clientWidth) {
        bar.scrollLeft = tab.offsetLeft - (bar.clientWidth - tab.offsetWidth) / 2;
      }
      // a new section starts at its top, not where the previous one was scrolled to
      const top = document.getElementById('admin-grid').getBoundingClientRect().top + window.scrollY;
      if (changed && window.scrollY > top) window.scrollTo(0, top);
      if (focus) tab.focus();
      try { history.replaceState(null, '', '#' + activeTab); } catch (_) { /* file:// or old browser */ }
      onTabShown(activeTab);
    }

    function onTabShown(name) {
      if (name === 'messages') { if (_loaded.messages) refreshMessages(); else { _loaded.messages = true; loadMessages(); } }
      if (name === 'people') loadUsage();
      if (name === 'system') updateCrawlHistory();
    }

    TABS.forEach((t, i) => {
      t.addEventListener('click', () => showTab(t.dataset.tab));
      t.addEventListener('keydown', ev => {
        let j = null;
        if (ev.key === 'ArrowRight') j = (i + 1) % TABS.length;
        else if (ev.key === 'ArrowLeft') j = (i - 1 + TABS.length) % TABS.length;
        else if (ev.key === 'Home') j = 0;
        else if (ev.key === 'End') j = TABS.length - 1;
        if (j !== null) { ev.preventDefault(); showTab(TABS[j].dataset.tab, true); }
      });
    });
    window.addEventListener('hashchange', () => {
      const h = location.hash.slice(1);
      if (h && h !== activeTab && TABS.some(t => t.dataset.tab === h)) showTab(h);
    });

    function setBadge(id, n) {
      const b = document.getElementById(id);
      b.hidden = !n;
      b.textContent = n ? String(n) : '';
    }

    // ---------------------------------------------------------------- overview and system (GET /health)
    let _health = null, _negOpen = null, _appsPending = null;

    // What needs a decision comes first: open reports, applications, maintenance
    function renderAttention() {
      const box = document.getElementById('attention');
      const items = [];
      const mi = (_health && _health.maintenance_info) || {};
      if (mi.active) items.push(['', '<strong>Maintenance</strong> active since ' + _esc(mi.since || '?') + ' — ' + _esc(mi.reason || ''), 'system']);
      if (_negOpen) items.push(['', '<strong>' + _negOpen + '</strong> open report' + (_negOpen > 1 ? 's' : '') + ' 👎', 'reports']);
      if (_appsPending) items.push(['warn', '<strong>' + _appsPending + '</strong> application' + (_appsPending > 1 ? 's' : '') + ' to decide', 'people']);
      if (!items.length) {
        box.innerHTML = (_negOpen === null || _appsPending === null) ? ''
          : '<div class="attn ok">✓ Nothing to handle: no open reports, no applications to decide</div>';
        return;
      }
      box.innerHTML = items.map(([cls, text, tab]) =>
        '<button type="button" class="attn ' + cls + '" data-go="' + tab + '"><span>' + text + '</span><span class="go">Open</span></button>'
      ).join('');
      box.querySelectorAll('[data-go]').forEach(b => b.addEventListener('click', () => showTab(b.dataset.go)));
    }

    function updateStats() {
      const t0 = performance.now();
      fetch(apiBase + '/health', { headers: adminHeaders() })
        .then(r => r.json())
        .then(d => {
          _health = d;
          const ms = Math.round(performance.now() - t0);
          document.getElementById('dot').className = 'dot ok';
          document.getElementById('backend-status').textContent = 'online';
          document.getElementById('latency').textContent = ms + ' ms';
          document.getElementById('last-update').textContent = new Date().toLocaleTimeString('en-GB');
          document.getElementById('uptime').textContent = fmtUptime(d.uptime_seconds || 0);
          // Maintenance mode (model provider unavailable): since when and why, admin only
          const mi = d.maintenance_info || {};
          const mEl = document.getElementById('maintenance');
          mEl.textContent = mi.active ? 'ACTIVE since ' + (mi.since || '?') + ' — ' + (mi.reason || '') : 'no';
          mEl.style.color = mi.active ? '#b3261e' : '';
          mEl.style.fontWeight = mi.active ? '600' : '';

          document.getElementById('model-name').textContent = d.llm_model || '—';
          document.getElementById('llm-provider').textContent = d.llm_provider ? '(' + d.llm_provider + ')' : '';
          document.getElementById('hybrid').textContent = d.hybrid_search ? '✓ active' : '✗ not available';
          document.getElementById('last-indexed').textContent = d.last_indexed || '—';
          document.getElementById('unique-sources').textContent = fmtNum(d.unique_sources);
          document.getElementById('chunk-count').textContent = fmtNum(d.indexed_chunks);
          if (d.doc_types) {
            document.getElementById('doc-types').textContent = Object.entries(d.doc_types)
              .sort((a, b) => b[1] - a[1]).map(([k, v]) => fmtNum(v) + ' ' + k).join(' · ');
          }
          if (d.top_doc) {
            const td = d.top_doc;
            document.getElementById('top-doc').innerHTML =
              '<a href="' + _href(td.source) + '" target="_blank" rel="noopener">' + _esc(td.title || String(td.source || '').split('/').pop()) + '</a>'
              + ' <span class="muted">(' + _esc(td.chunks) + ' chunks)</span>';
          }

          const q = d.queries_since_restart ?? 0;
          const g = d.gaps_total ?? 0;
          document.getElementById('query-count').textContent = fmtNum(q);
          document.getElementById('gaps-count').textContent = fmtNum(g);
          document.getElementById('gaps-pct').textContent = q > 0 ? Math.round(g / q * 100) + '%' : '—';

          const act = d.activity || {};
          document.getElementById('avg-resp').textContent =
            act.avg_response_ms != null ? (act.avg_response_ms / 1000).toFixed(1) + ' s' : '—';

          // Questions by hour
          const hc = act.hour_counts || new Array(24).fill(0);
          const maxH = Math.max(...hc, 1);
          const nowH = new Date().getHours();
          document.getElementById('hours-chart').innerHTML = hc.map((v, h) =>
            '<div class="hour-bar' + (h === nowH ? ' active' : '') + '" style="height:' + Math.max(2, Math.round(v / maxH * 46)) + 'px"'
            + ' data-label="' + h + ':00 — ' + v + ' questions" title="' + h + ':00 — ' + v + ' questions"></div>'
          ).join('');

          // Most frequent questions
          const tq = act.top_queries || [];
          if (tq.length) {
            const maxC = Math.max(...tq.map(([, c]) => c), 1);
            document.getElementById('top-queries').innerHTML = tq.map(([tqq, c]) =>
              '<div class="qbar-item"><div class="qbar-header"><span class="qbar-text">' + _esc(tqq) + '</span>'
              + '<span class="qbar-count">×' + _esc(c) + '</span></div>'
              + '<div class="qbar-track"><div class="qbar-fill" style="width:' + Math.round(c / maxC * 100) + '%"></div></div></div>'
            ).join('');
          }

          // Feedback
          const fb = d.feedback || {};
          const tot = fb.total || 0;
          document.getElementById('fb-pct').textContent = tot ? Math.round((fb.positive || 0) / tot * 100) + '%' : '—';
          document.getElementById('fb-pos').textContent = fmtNum(fb.positive ?? 0);
          document.getElementById('fb-total').textContent = fmtNum(tot);
          document.getElementById('fb-res').textContent = fmtNum(fb.resolved ?? 0);

          // Tokens & costs
          document.getElementById('tok-in').textContent = fmtNum(act.token_in_total);
          document.getElementById('tok-out').textContent = fmtNum(act.token_out_total);
          document.getElementById('tok-avg-in').textContent = fmtNum(act.token_avg_in);
          document.getElementById('tok-avg-out').textContent = fmtNum(act.token_avg_out);
          const cost = act.token_cost_total;
          document.getElementById('tok-cost').textContent = cost != null ? '$' + cost.toFixed(2) : '—';
          const hist = act.token_history || [];
          document.getElementById('cost-chart-wrap').hidden = !hist.length;
          if (hist.length) {
            const maxCost = Math.max(...hist.map(e => e.cost || 0), 0.001);
            document.getElementById('cost-chart').innerHTML = hist.map(e => {
              const tip = _esc(fmtTs(e.ts)) + ' — $' + (e.cost || 0).toFixed(4);
              return '<div class="cost-bar-col" title="' + tip + '"><div class="cost-bar" style="height:'
                + Math.max(2, Math.round((e.cost || 0) / maxCost * 46)) + 'px"></div></div>';
            }).join('');
            const rows = [...hist].reverse();
            const tokMore = document.getElementById('tok-more');
            tokMore.hidden = _tokAll || rows.length <= 10;
            document.getElementById('token-history-body').innerHTML = (_tokAll ? rows : rows.slice(0, 10)).map(e =>
              '<tr><td>' + _esc(fmtTs(e.ts).slice(5)) + '</td>'
              + '<td>' + (e.user ? _esc(e.user) : '<span class="muted">—</span>') + '</td>'
              + '<td class="col-model muted">' + _esc((e.model || '').replace('claude-', '')) + '</td>'
              + '<td>' + fmtNum(e.in) + '</td><td>' + fmtNum(e.out) + '</td>'
              + '<td>' + (e.cost || 0).toFixed(4) + '</td></tr>'
            ).join('');
          }
          renderAttention();
        })
        .catch(() => {
          document.getElementById('dot').className = 'dot err';
          document.getElementById('backend-status').textContent = 'unreachable';
        });
    }

    let _crawlAll = false, _tokAll = false;
    document.getElementById('tok-more').addEventListener('click', () => { _tokAll = true; updateStats(); });
    function updateCrawlHistory() {
      fetch(apiBase + '/crawl-history', { headers: adminHeaders() })
        .then(r => r.json())
        .then(d => {
          [['html', d.current_html, ' pages'], ['pdf', d.current_pdf, '']].forEach(([k, p, unit]) => {
            if (!p) return;
            const done = p.current >= p.total;
            document.getElementById('crawl-progress-' + k).hidden = false;
            document.getElementById('crawl-' + k + '-label').textContent = k.toUpperCase() + ': ' + fmtNum(p.current)
              + ' / ' + fmtNum(p.total) + unit + (done ? ' ✓' : ' — in progress ' + p.ts);
            document.getElementById('crawl-' + k + '-bar').style.width = (p.total > 0 ? Math.round(p.current / p.total * 100) : 0) + '%';
          });
          const evs = [...(d.events || [])].reverse();
          const tl = document.getElementById('crawl-timeline');
          const more = document.getElementById('crawl-more');
          if (!evs.length) {
            tl.innerHTML = '<li><span class="tl-label muted">no events found in the log</span></li>';
            more.hidden = true;
            return;
          }
          const LIMIT = 12;
          tl.innerHTML = (_crawlAll ? evs : evs.slice(0, LIMIT)).map(e => {
            const cls = { vs_loaded: 'vs', html_done: 'html', crawl_end: 'crawl' }[e.type] || '';
            return '<li><div class="tl-dot ' + cls + '"></div><span class="tl-ts">' + _esc(e.ts) + '</span>'
              + '<span class="tl-label">' + _esc(e.label || e.type) + '</span></li>';
          }).join('');
          more.hidden = _crawlAll || evs.length <= LIMIT;
        })
        .catch(() => {
          document.getElementById('crawl-timeline').innerHTML =
            '<li><span class="tl-label" style="color:#c62828">error loading history</span></li>';
        });
    }
    document.getElementById('crawl-more').addEventListener('click', () => { _crawlAll = true; updateCrawlHistory(); });

    // ---------------------------------------------------------------- reports (👎)
    function loadNegativeFeedback() {
      const statusEl = document.getElementById('neg-fb-status');
      const listEl = document.getElementById('neg-fb-list');
      fetch(apiBase + '/feedback/negative', { headers: adminHeaders() })
        .then(r => r.json())
        .then(data => {
          const items = (data.items || []).slice().reverse();
          _negOpen = items.length;
          setBadge('badge-reports', _negOpen);
          renderAttention();
          statusEl.textContent = '— ' + items.length + ' open out of ' + (data.total_negative || 0) + ' total';
          // a note being written is not thrown away by the refresh
          if (listEl.querySelector('.resolve-form')) return;
          if (!items.length) {
            listEl.innerHTML = '<li><span class="muted">No open reports.</span></li>';
            return;
          }
          listEl.innerHTML = '';
          items.forEach(e => listEl.appendChild(_reportItem(e)));
        })
        .catch(() => {
          statusEl.textContent = '';
          listEl.innerHTML = '<li><span class="muted">Error loading.</span></li>';
        });
    }

    function _reportItem(e) {
      const li = document.createElement('li');
      let html = '<div class="neg-q">' + _esc(e.question || '') + '</div>';
      const meta = [fmtTs(e.ts), e.user ? '👤 ' + _esc(e.user) : ''].filter(Boolean).join(' · ');
      if (meta) html += '<div class="neg-meta">' + meta + '</div>';
      if (e.comment) html += '<div class="neg-comment">' + _esc(e.comment) + '</div>';
      if (e.urls && e.urls.length) {
        html += '<ul class="neg-urls">' + e.urls.map(u =>
          '<li>🔗 <a href="' + _href(u) + '" target="_blank" rel="noopener noreferrer">' + _esc(u) + '</a></li>').join('') + '</ul>';
      }
      if (e.answer_preview) {
        html += '<details class="neg-answer"><summary>Answer given (start)</summary><div>' + _esc(e.answer_preview) + '…</div></details>';
      }
      html += '<div class="neg-actions"><button type="button" class="btn">✓ Resolve…</button></div>';
      li.innerHTML = html;
      li.querySelector('.neg-actions .btn').addEventListener('click', ev => _openResolveForm(e, li, ev.currentTarget));
      return li;
    }

    // Resolving: with an author, an optional note goes in the follow-up email (up to 2000 characters)
    let _formSeq = 0;
    function _openResolveForm(e, li, openBtn) {
      openBtn.hidden = true;
      const id = 'resolve-note-' + (++_formSeq);
      const form = document.createElement('form');
      form.className = 'resolve-form';
      form.innerHTML = e.user
        ? '<label for="' + id + '">Note for ' + _esc(e.user) + ' (optional, added to the follow-up email)</label>'
          + '<textarea id="' + id + '" maxlength="2000" rows="4"></textarea><div class="note-count" aria-live="polite">0 / 2000</div>'
          + '<div class="form-actions"><button type="submit" class="btn primary">Resolve and send email</button>'
          + '<button type="button" class="btn" data-act="silent">Resolve without email</button>'
          + '<button type="button" class="btn" data-act="cancel">Cancel</button></div>'
        : '<div class="form-actions"><button type="submit" class="btn primary">Resolve</button>'
          + '<button type="button" class="btn" data-act="cancel">Cancel</button></div>';
      li.appendChild(form);
      const ta = form.querySelector('textarea');
      if (ta) {
        ta.addEventListener('input', () => { form.querySelector('.note-count').textContent = ta.value.length + ' / 2000'; });
        ta.focus();
      } else {
        form.querySelector('[type="submit"]').focus();
      }
      const close = () => { form.remove(); openBtn.hidden = false; openBtn.focus(); };
      form.querySelector('[data-act="cancel"]').addEventListener('click', close);
      const silent = form.querySelector('[data-act="silent"]');
      if (silent) silent.addEventListener('click', () => _resolve(e, li, form, '', false));
      form.addEventListener('submit', ev => { ev.preventDefault(); _resolve(e, li, form, ta ? ta.value : '', !!e.user); });
    }

    function _resolve(e, li, form, note, notify) {
      const btns = form.querySelectorAll('button');
      btns.forEach(b => { b.disabled = true; });
      fetch(apiBase + '/feedback/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Key': getAdminKey() },
        body: JSON.stringify({ ts: e.ts, question: e.question, id: e.id || null, note: note.trim(), notify }),
      })
        .then(r => {
          if (r.status === 403) { _adminKey = null; throw new Error('Invalid admin key.'); }
          if (!r.ok) throw new Error('HTTP ' + r.status);
          li.classList.add('resolving');
          setTimeout(() => { li.remove(); loadNegativeFeedback(); updateStats(); }, 320);
        })
        .catch(err => {
          btns.forEach(b => { b.disabled = false; });
          alert('Not resolved: ' + (err && err.message ? err.message : 'error'));
        });
    }

    // ---------------------------------------------------------------- message log
    // A page of 100 messages at a time, without the retrieved passages (half of the payload);
    // 30 cards shown, then «Show more». Read only while the tab is open.
    const MSG_PAGE = 100, MSG_STEP = 30;
    const _msg = { items: [], total: 0, shown: MSG_STEP, sig: '' };

    function _msgKey(m) { return [m.rid || '', m.ts || '', m.uid || ''].join('|'); }

    function _fetchMessages(offset) {
      return fetch(apiBase + '/usage/messages?limit=' + MSG_PAGE + '&offset=' + offset + '&retrieved=false',
        { headers: adminHeaders() })
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
    }

    function loadMessages() {
      _fetchMessages(0)
        .then(d => { _msg.items = d.messages || []; _msg.total = d.total || 0; _fillUserFilter(); renderMessages(); })
        .catch(() => { document.getElementById('msg-body').innerHTML = '<p class="muted">Error loading the log.</p>'; });
    }

    // The newest page replaces the top of the list; older pages already loaded stay
    function refreshMessages() {
      _fetchMessages(0).then(d => {
        const page = d.messages || [];
        const keys = new Set(page.map(_msgKey));
        const oldest = page.length ? page[page.length - 1].ts : '';
        const rest = _msg.items.filter(m => !keys.has(_msgKey(m)) && m.ts < oldest);
        _msg.items = page.concat(rest);
        _msg.total = d.total || 0;
        _fillUserFilter();
        renderMessages(true);
      }).catch(() => { /* keep what is shown */ });
    }

    function _loadOlderMessages(btn) {
      btn.disabled = true;
      _fetchMessages(_msg.items.length).then(d => {
        const keys = new Set(_msg.items.map(_msgKey));
        _msg.items = _msg.items.concat((d.messages || []).filter(m => !keys.has(_msgKey(m))));
        _msg.total = d.total || _msg.total;
        _msg.shown += MSG_STEP;
        _fillUserFilter();
        renderMessages();
      }).catch(() => alert('Error loading older messages.')).finally(() => { btn.disabled = false; });
    }

    function _fillUserFilter() {
      const sel = document.getElementById('msg-user');
      const cur = sel.value;
      const names = new Map();
      _msg.items.forEach(m => { if (m.uid && !names.has(m.uid)) names.set(m.uid, m.name || m.uid); });
      if (cur && !names.has(cur)) names.set(cur, sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : cur);
      const opts = [...names.entries()].sort((a, b) => a[1].localeCompare(b[1], 'it'));
      const html = '<option value="">All users</option>' + opts.map(([uid, name]) =>
        '<option value="' + _esc(uid) + '">' + _esc(name) + '</option>').join('');
      if (sel.innerHTML !== html) { sel.innerHTML = html; sel.value = cur; }
    }

    function _filteredMessages() {
      const words = document.getElementById('msg-search').value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const uid = document.getElementById('msg-user').value;
      return _msg.items.filter(m => {
        if (uid && m.uid !== uid) return false;
        if (!words.length) return true;
        const hay = [m.q, m.a, m.name, m.rid].join(' ').toLowerCase();
        return words.every(w => hay.includes(w));
      });
    }

    function _isoDay(dt) {
      return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');
    }

    function _dayLabel(day) {
      const now = new Date();
      const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
      if (day === _isoDay(now)) return 'Today';
      if (day === _isoDay(yesterday)) return 'Yesterday';
      const dt = new Date(day + 'T12:00:00');
      return isNaN(dt) ? day : dt.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
    }

    function _msgCard(m, open) {
      const key = _msgKey(m);
      const meta = '<span class="msg-meta">' + _esc((m.ts || '').slice(11, 16)) + ' · ' + _esc(m.name || m.uid || '—')
        + (m.partial ? ' · <span class="msg-partial">answer interrupted</span>' : '') + '</span>';
      const q = '<span class="msg-q">' + _esc(m.q) + '</span>';
      // Since 07/10/2026 the answer is recorded too (answers.jsonl): the question opens it
      if (typeof m.a !== 'string') return '<div class="msg-card plain">' + meta + q + '</div>';
      const src = (m.sources || []).map(s => {
        const t = _esc(s.title || s.url || '');
        return '<li>' + (s.url ? '<a href="' + _href(s.url) + '" target="_blank" rel="noopener">' + t + '</a>' : t) + '</li>';
      }).join('');
      return '<details class="msg-card" data-key="' + _esc(key) + '"' + (open.has(key) ? ' open' : '') + '>'
        + '<summary>' + meta + q + '</summary>'
        + '<div class="msg-answer-text">' + _answerHtml(m.a) + '</div>'
        + (src ? '<ul class="msg-sources">' + src + '</ul>' : '')
        + (m.rid ? '<div class="msg-foot">rid ' + _esc(m.rid) + '</div>' : '')
        + '</details>';
    }

    function renderMessages(fromRefresh) {
      const body = document.getElementById('msg-body');
      const list = _filteredMessages();
      const shown = list.slice(0, _msg.shown);
      // a refresh that changes nothing leaves the page alone (scroll position, open answers)
      const sig = shown.map(m => _msgKey(m) + ':' + (typeof m.a === 'string' ? m.a.length : '-')).join(',') + '#' + _msg.total;
      if (fromRefresh && sig === _msg.sig) return;
      _msg.sig = sig;
      const open = new Set(Array.from(body.querySelectorAll('details[open]')).map(d => d.dataset.key));
      const filtering = list.length !== _msg.items.length;
      document.getElementById('msg-status').textContent = (filtering ? list.length + ' found in ' : '')
        + 'the latest ' + _msg.items.length + ' of ' + _msg.total + ' messages'
        + (filtering && _msg.items.length < _msg.total ? ' · load older messages to search further back' : '');
      // new messages arrive at the top: keep what is on screen where it is
      const before = document.documentElement.scrollHeight;
      const below = window.scrollY > body.getBoundingClientRect().top + window.scrollY;
      if (!shown.length) {
        body.innerHTML = '<p class="muted">' + (filtering ? 'No message matches.' : 'No messages recorded so far.') + '</p>';
      } else {
        let html = '', day = null;
        shown.forEach(m => {
          const d = (m.ts || '').slice(0, 10);
          if (d !== day) {
            if (day !== null) html += '</ol>';
            day = d;
            html += '<h3 class="msg-day">' + _esc(_dayLabel(d)) + '</h3><ol class="msg-list">';
          }
          html += '<li>' + _msgCard(m, open) + '</li>';
        });
        body.innerHTML = html + '</ol>';
      }
      if (fromRefresh && below) window.scrollBy(0, document.documentElement.scrollHeight - before);
      const more = document.getElementById('msg-more');
      if (list.length > _msg.shown) {
        more.hidden = false;
        more.dataset.act = 'more';
        more.textContent = 'Show ' + Math.min(MSG_STEP, list.length - _msg.shown) + ' more';
      } else if (_msg.items.length < _msg.total) {
        more.hidden = false;
        more.dataset.act = 'older';
        more.textContent = 'Load older messages';
      } else {
        more.hidden = true;
      }
    }

    document.getElementById('msg-more').addEventListener('click', ev => {
      const btn = ev.currentTarget;
      if (btn.dataset.act === 'older') { _loadOlderMessages(btn); return; }
      _msg.shown += MSG_STEP;
      renderMessages();
    });
    let _searchTimer = null;
    document.getElementById('msg-search').addEventListener('input', () => {
      clearTimeout(_searchTimer);
      _searchTimer = setTimeout(() => { _msg.shown = MSG_STEP; renderMessages(); }, 200);
    });
    document.getElementById('msg-user').addEventListener('change', () => { _msg.shown = MSG_STEP; renderMessages(); });

    // ---------------------------------------------------------------- people
    function loadUsage() {
      const body = document.getElementById('usage-body');
      const status = document.getElementById('usage-status');
      fetch(apiBase + '/usage/summary', { headers: adminHeaders() })
        .then(r => {
          if (r.status === 403) { _adminKey = null; throw new Error('key'); }
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(data => {
          const users = data.users || [];
          const reg = data.total_registered != null ? data.total_registered : (data.total_users || 0);
          const act = data.total_active != null ? data.total_active : users.length;
          status.textContent = '— ' + reg + ' registered · ' + act + ' active · ' + (data.total_messages || 0) + ' messages';
          if (!users.length) {
            body.innerHTML = '<span class="muted">No registered users (or login disabled: AUTH_ENABLED).</span>';
            return;
          }
          body.innerHTML = '<ul class="user-list">' + users.map(u =>
            '<li' + (u.messages ? '' : ' class="idle"') + '><span>' + _esc(u.name) + '</span>'
            + '<span class="user-meta">' + _esc(u.messages) + ' messages · ' + _esc(u.active_days) + ' days · last '
            + _esc(u.last_seen ? fmtTs(u.last_seen) : 'never') + '</span></li>'
          ).join('') + '</ul>';
        })
        .catch(e => {
          body.innerHTML = '<span class="muted">' + (String(e && e.message) === 'key' ? 'Invalid admin key.' : 'Error loading the users.') + '</span>';
        });
    }

    // Applications of the citizens who asked to take part (POST /apply): the same
    // decisions as `scripts.applications --accept/--reject`, from any device.
    function loadApplications() {
      const statusEl = document.getElementById('apps-status');
      const listEl = document.getElementById('apps-list');
      const showAll = document.getElementById('apps-show-all').checked;
      fetch(apiBase + '/applications', { headers: adminHeaders() })
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(data => {
          const all = data.applications || [];
          _appsPending = data.pending || 0;
          setBadge('badge-people', _appsPending);
          renderAttention();
          const items = showAll ? all : all.filter(a => (a.status || 'new') === 'new');
          statusEl.textContent = '— ' + _appsPending + ' to decide out of ' + all.length + ' received';
          if (!items.length) {
            listEl.innerHTML = '<li><span class="muted">' + (showAll ? 'No applications received.' : 'No applications to decide.') + '</span></li>';
            return;
          }
          listEl.innerHTML = '';
          items.forEach(a => {
            const st = String(a.status || 'new');
            const li = document.createElement('li');
            const meta = [fmtTs(a.ts), _esc(a.email), a.age ? _esc(a.age) + ' years' : '', a.district ? _esc(a.district) : '']
              .filter(Boolean).join(' · ');
            li.innerHTML = '<span class="app-name">' + _esc(a.name) + '</span> <span class="app-badge ' + _esc(st) + '">' + _esc(st) + '</span>'
              + '<div class="app-meta">' + meta + '</div>' + (a.usage ? '<div class="app-usage">' + _esc(a.usage) + '</div>' : '');
            if (st === 'new') {
              const acts = document.createElement('div');
              acts.className = 'app-actions';
              const ok = document.createElement('button');
              ok.type = 'button'; ok.className = 'btn primary'; ok.textContent = '✓ Accept';
              const no = document.createElement('button');
              no.type = 'button'; no.className = 'btn btn-reject'; no.textContent = '✗ Reject';
              ok.addEventListener('click', () => decideApplication(a, 'accept', li, [ok, no]));
              no.addEventListener('click', () => decideApplication(a, 'reject', li, [ok, no]));
              acts.append(ok, no);
              li.appendChild(acts);
            }
            listEl.appendChild(li);
          });
        })
        .catch(() => {
          statusEl.textContent = '';
          listEl.innerHTML = '<li><span class="muted">Error loading the applications.</span></li>';
        });
    }

    function decideApplication(a, action, liEl, btns) {
      const who = (a.name || '') + ' <' + (a.email || '') + '>';
      const what = action === 'accept'
        ? 'Accept ' + who + '?\n\nThe account is created and the invitation with the link to choose the password is sent now.'
        : 'Reject ' + who + '?\n\nNothing is sent to the person: the application is only marked as not selected.';
      if (!confirm(what)) return;
      btns.forEach(b => { b.disabled = true; });
      fetch(apiBase + '/applications/' + action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Key': getAdminKey() },
        body: JSON.stringify({ id: a.id }),
      })
        .then(async r => {
          if (r.status === 403) { _adminKey = null; throw new Error('Invalid admin key.'); }
          if (!r.ok) {
            let detail = '';
            try { detail = (await r.json()).detail || ''; } catch (_) { /* no body */ }
            throw new Error(detail || ('HTTP ' + r.status));
          }
          return r.json();
        })
        .then(d => {
          liEl.classList.add('resolving');
          setTimeout(loadApplications, 320);
          if (action === 'accept') alert('Accepted: the invitation was sent to ' + (d.email || a.email) + '.');
        })
        .catch(e => {
          btns.forEach(b => { b.disabled = false; });
          alert('Not done: ' + (e && e.message ? e.message : 'error'));
          loadApplications();
        });
    }
    document.getElementById('apps-show-all').addEventListener('change', loadApplications);

    // ---------------------------------------------------------------- changelog
    // The installation's changelog comes after the panel: it moves into its tab, newest 20 entries first.
    function mountChangelog() {
      const sec = document.getElementById('changelog-section');
      if (!sec) return;
      const slot = document.getElementById('changelog-slot');
      slot.innerHTML = '';
      slot.appendChild(sec);
      const entries = sec.querySelectorAll('.cl-entry');
      const LIMIT = 20;
      if (entries.length <= LIMIT) return;
      entries.forEach((el, i) => { if (i >= LIMIT) el.classList.add('cl-hidden'); });
      const btn = document.getElementById('changelog-more');
      btn.hidden = false;
      btn.textContent = 'Show ' + (entries.length - LIMIT) + ' older entries';
      btn.addEventListener('click', () => { entries.forEach(el => el.classList.remove('cl-hidden')); btn.hidden = true; });
    }

    // ---------------------------------------------------------------- start
    // Called by startAdminView() (login.html) after the panel has been inserted into the page.
    // Each section is read when shown and refreshed only while it is on screen; nothing runs
    // while the page is in the background.
    window.zautteAdminBoot = function () {
      mountChangelog();
      const h = location.hash.slice(1);
      showTab(TABS.some(t => t.dataset.tab === h) ? h : 'overview');
      updateStats();
      loadNegativeFeedback();
      loadApplications();
      let tick = 0;
      setInterval(() => {
        if (document.hidden) return;
        tick += 1;
        updateStats();
        if (tick % 2) return;
        loadNegativeFeedback();
        loadApplications();
        if (activeTab === 'messages') refreshMessages();
        if (activeTab === 'system') updateCrawlHistory();
        if (activeTab === 'people' && tick % 10 === 0) loadUsage();
      }, 30000);
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        updateStats();
        loadNegativeFeedback();
        loadApplications();
        if (activeTab === 'messages' && _loaded.messages) refreshMessages();
      });
    };
