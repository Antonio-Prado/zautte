    function fmtUptime(sec) {
      if (sec < 60) return sec + 's';
      if (sec < 3600) return Math.floor(sec/60) + 'm ' + (sec%60) + 's';
      const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60);
      return h + 'h ' + m + 'm';
    }

    function fmtNum(n) {
      return n != null ? Number(n).toLocaleString('en-GB') : '—';
    }

    function updateStats() {
      const t0 = performance.now();
      fetch(apiBase + '/health', { headers: adminHeaders() })
        .then(r => r.json())
        .then(d => {
          const ms = Math.round(performance.now() - t0);
          document.getElementById('dot').className = 'dot ok';
          document.getElementById('backend-status').textContent = 'online';
          document.getElementById('latency').textContent = ms + ' ms';
          document.getElementById('last-update').textContent = new Date().toLocaleTimeString('en-GB');
          document.getElementById('uptime').textContent = fmtUptime(d.uptime_seconds || 0);

          document.getElementById('model-name').textContent = d.llm_model || '—';
          document.getElementById('llm-provider').textContent = d.llm_provider ? '(' + d.llm_provider + ')' : '';
          document.getElementById('hybrid').textContent = d.hybrid_search ? '✓ active' : '✗ not available';
          document.getElementById('last-indexed').textContent = d.last_indexed || '—';
          document.getElementById('unique-sources').textContent = fmtNum(d.unique_sources);
          document.getElementById('chunk-count').textContent = fmtNum(d.indexed_chunks);

          if (d.doc_types) {
            const parts = Object.entries(d.doc_types).sort((a,b)=>b[1]-a[1]).map(([k,v])=>fmtNum(v)+' '+k);
            document.getElementById('doc-types').textContent = parts.join(' · ');
          }
          if (d.top_doc) {
            const td = d.top_doc;
            document.getElementById('top-doc').innerHTML =
              '<a href="'+_href(td.source)+'" target="_blank" rel="noopener" style="color:#003366;font-size:.9em">'+_esc(td.title||td.source.split('/').pop())+'</a>'
              +' <span style="color:#666;font-size:.8em">('+_esc(td.chunks)+' chunk)</span>';
          }

          const q = d.queries_since_restart ?? 0;
          const g = d.gaps_total ?? 0;
          document.getElementById('query-count').textContent = fmtNum(q);
          document.getElementById('gaps-count').textContent = fmtNum(g);
          document.getElementById('gaps-pct').textContent = q > 0 ? Math.round(g/q*100)+'%' : '—';

          const act = d.activity || {};
          document.getElementById('avg-resp').textContent = act.avg_response_ms != null ? fmtNum(act.avg_response_ms) : '—';

          // Peak hours
          const hc = act.hour_counts || new Array(24).fill(0);
          const maxH = Math.max(...hc, 1);
          const chart = document.getElementById('hours-chart');
          chart.innerHTML = '';
          const nowH = new Date().getHours();
          hc.forEach((v, h) => {
            const bar = document.createElement('div');
            bar.className = 'hour-bar' + (h === nowH ? ' active' : '');
            bar.style.height = Math.max(2, Math.round(v/maxH*38)) + 'px';
            bar.setAttribute('data-label', h + ':00 — ' + v + ' questions');
            chart.appendChild(bar);
          });

          // Top queries — horizontal bars
          const tq = act.top_queries || [];
          const qDiv = document.getElementById('top-queries');
          if (tq.length) {
            const maxC = Math.max(...tq.map(([,c]) => c), 1);
            qDiv.innerHTML = tq.map(([q,c]) =>
              '<div class="qbar-item">' +
              '<div class="qbar-header"><span class="qbar-text">'+_esc(q)+'</span><span class="qbar-count">×'+_esc(c)+'</span></div>' +
              '<div class="qbar-track"><div class="qbar-fill" style="width:'+Math.round(c/maxC*100)+'%"></div></div>' +
              '</div>'
            ).join('');
          }

          // Feedback + SVG donut
          const fb = d.feedback || {};
          document.getElementById('fb-total').textContent = fmtNum(fb.total ?? 0);
          document.getElementById('fb-pos').textContent = fmtNum(fb.positive ?? 0);
          document.getElementById('fb-neg').textContent = fmtNum(fb.negative ?? 0);
          document.getElementById('fb-res').textContent = fb.resolved ? '✓ ' + fmtNum(fb.resolved) + ' resolved' : '';
          {
            const r = 28, cx = 35, cy = 35, sw = 10;
            const circ = 2 * Math.PI * r;
            const t = fb.total || 0;
            const pos = fb.positive || 0;
            const neg = fb.negative || 0;
            const posLen = t > 0 ? (pos/t)*circ : 0;
            const negLen = t > 0 ? (neg/t)*circ : 0;
            const posSeg = posLen > 0
              ? '<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="#43a047" stroke-width="'+sw+'" stroke-dasharray="'+posLen.toFixed(1)+' '+circ.toFixed(1)+'" stroke-dashoffset="0" transform="rotate(-90 '+cx+' '+cy+')"/>'
              : '';
            const negSeg = negLen > 0
              ? '<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="#e53935" stroke-width="'+sw+'" stroke-dasharray="'+negLen.toFixed(1)+' '+circ.toFixed(1)+'" stroke-dashoffset="'+(-posLen).toFixed(1)+'" transform="rotate(-90 '+cx+' '+cy+')"/>'
              : '';
            const pct = t > 0 ? Math.round(pos/t*100) : 0;
            document.getElementById('fb-donut').innerHTML =
              '<svg viewBox="0 0 70 70" width="70" height="70">' +
              '<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="#eee" stroke-width="'+sw+'"/>' +
              posSeg + negSeg +
              '<text x="'+cx+'" y="'+(cy+1)+'" text-anchor="middle" dominant-baseline="middle" font-size="11" font-weight="bold" fill="#555">'+pct+'%</text>' +
              '</svg>';
          }

          // Tokens & costs
          document.getElementById('tok-in').textContent  = fmtNum(act.token_in_total);
          document.getElementById('tok-out').textContent = fmtNum(act.token_out_total);
          document.getElementById('tok-avg-in').textContent  = fmtNum(act.token_avg_in);
          document.getElementById('tok-avg-out').textContent = fmtNum(act.token_avg_out);
          const cost = act.token_cost_total;
          document.getElementById('tok-cost').textContent = cost != null ? '$' + cost.toFixed(4) : '—';

          const hist = act.token_history || [];
          // Cost chart
          if (hist.length) {
            document.getElementById('cost-chart-wrap').style.display = '';
            const maxCost = Math.max(...hist.map(e => e.cost || 0), 0.001);
            document.getElementById('cost-chart').innerHTML = hist.map(e => {
              const h = Math.max(2, Math.round((e.cost || 0) / maxCost * 46));
              const lbl = _esc((e.ts || '').replace(/T.*/,'').slice(5));
              return '<div class="cost-bar-col">' +
                '<div class="cost-bar" style="height:'+h+'px" data-tip="'+lbl+' — $'+(e.cost||0).toFixed(4)+'"></div>' +
                '<div class="cost-bar-lbl">'+lbl+'</div>' +
                '</div>';
            }).join('');
          }
          // Token history table
          const tbody = document.getElementById('token-history-body');
          if (hist.length) {
            tbody.innerHTML = [...hist].reverse().map(e =>
              '<tr>' +
              '<td>' + _esc((e.ts || '').replace('T',' ').slice(0, 19)) + '</td>' +
              '<td>' + (e.user ? _esc(e.user) : '<span style="color:#666">—</span>') + '</td>' +
              '<td style="color:#666">' + _esc((e.model || '').replace('claude-','')) + '</td>' +
              '<td>' + fmtNum(e.in) + '</td>' +
              '<td>' + fmtNum(e.out) + '</td>' +
              '<td>$' + (e.cost || 0).toFixed(5) + '</td>' +
              '</tr>'
            ).join('');
          }
        })
        .catch(() => {
          document.getElementById('dot').className = 'dot err';
          document.getElementById('backend-status').textContent = 'unreachable';
        });
    }

    function updateCrawlHistory() {
      fetch(apiBase + '/crawl-history', { headers: adminHeaders() })
        .then(r => r.json())
        .then(d => {
          // Progress bars
          const html = d.current_html;
          if (html) {
            const pct = html.total > 0 ? Math.round(html.current / html.total * 100) : 0;
            const done = html.current >= html.total;
            document.getElementById('crawl-progress-html').style.display = '';
            document.getElementById('crawl-html-label').textContent =
              'HTML: ' + fmtNum(html.current) + ' / ' + fmtNum(html.total) + ' pages' + (done ? ' ✓' : ' — in progress ' + html.ts);
            document.getElementById('crawl-html-bar').style.width = pct + '%';
          }
          const pdf = d.current_pdf;
          if (pdf) {
            const pct = pdf.total > 0 ? Math.round(pdf.current / pdf.total * 100) : 0;
            const done = pdf.current >= pdf.total;
            document.getElementById('crawl-progress-pdf').style.display = '';
            document.getElementById('crawl-pdf-label').textContent =
              'PDF: ' + fmtNum(pdf.current) + ' / ' + fmtNum(pdf.total) + (done ? ' ✓' : ' — in progress ' + pdf.ts);
            document.getElementById('crawl-pdf-bar').style.width = pct + '%';
          }

          // Event timeline
          const evs = d.events || [];
          const tl = document.getElementById('crawl-timeline');
          if (evs.length) {
            tl.innerHTML = [...evs].reverse().map(e => {
              let dotClass = '';
              if (e.type === 'vs_loaded') dotClass = 'vs';
              else if (e.type === 'html_done') dotClass = 'html';
              else if (e.type === 'crawl_end') dotClass = 'crawl';
              return '<li>' +
                '<div class="tl-dot ' + dotClass + '"></div>' +
                '<span class="tl-ts">' + _esc(e.ts) + '</span>' +
                '<span class="tl-label">' + _esc(e.label || e.type) + '</span>' +
                '</li>';
            }).join('');
          } else {
            tl.innerHTML = '<li><span class="tl-label" style="color:#666">no events found in the log</span></li>';
          }
        })
        .catch(() => {
          document.getElementById('crawl-timeline').innerHTML =
            '<li><span class="tl-label" style="color:#e53935">error loading history</span></li>';
        });
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

    function loadMessages() {
      const body = document.getElementById('msg-body');
      const status = document.getElementById('msg-status');
      fetch(apiBase + '/usage/messages?limit=500', { headers: adminHeaders() })
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(data => {
          const msgs = data.messages || [];
          status.textContent = '— ' + (data.total || msgs.length) + ' messages';
          // The table is rebuilt every 30 s: open answers stay open.
          const open = new Set(Array.from(body.querySelectorAll('details[open]')).map(d => d.dataset.rid));
          if (!msgs.length) {
            body.innerHTML = '<span style="color:#666;font-size:.85em">No messages recorded so far.</span>';
            return;
          }
          let html = '<table style="width:100%;border-collapse:collapse;font-size:.9em">'
            + '<thead><tr style="text-align:left;color:#666;border-bottom:1px solid #eee">'
            + '<th style="padding:6px 8px;white-space:nowrap">When</th>'
            + '<th style="padding:6px 8px;white-space:nowrap">User</th>'
            + '<th style="padding:6px 8px">Question and answer</th></tr></thead><tbody>';
          msgs.forEach(m => {
            const ts = (m.ts || '').replace('T', ' ').slice(0, 19);
            // Since 07/10/2026 the answer given by Zautte is also there (answers.jsonl):
            // the question opens and closes the answer, with the sources at the bottom.
            let cell = _esc(m.q);
            if (typeof m.a === 'string') {
              let src = '';
              (m.sources || []).forEach(s => {
                const t = _esc(s.title || s.url || '');
                src += '<li>' + (s.url ? '<a href="' + _href(s.url) + '" target="_blank" rel="noopener">' + t + '</a>' : t) + '</li>';
              });
              cell = '<details class="msg-answer" data-rid="' + _esc(m.rid) + '"'
                + (open.has(m.rid) ? ' open' : '') + '><summary>' + _esc(m.q)
                + (m.partial ? ' <span class="msg-partial">(answer interrupted)</span>' : '')
                + '</summary><div class="msg-answer-text">' + _esc(m.a) + '</div>'
                + (src ? '<ul class="msg-sources">' + src + '</ul>' : '') + '</details>';
            }
            html += '<tr style="border-bottom:1px solid #f3f3f3">'
              + '<td style="padding:6px 8px;color:#666;white-space:nowrap">' + ts + '</td>'
              + '<td style="padding:6px 8px;white-space:nowrap">' + _esc(m.name) + '</td>'
              + '<td style="padding:6px 8px">' + cell + '</td></tr>';
          });
          html += '</tbody></table>';
          body.innerHTML = html;
        })
        .catch(() => {
          body.innerHTML = '<span style="color:#666;font-size:.85em">Error loading the log.</span>';
        });
    }

    function resolveNegative(ts, question, liEl, btnEl, user) {
      const key = getAdminKey();
      // If the feedback has an author, whoever resolves it can notify them by email with an
      // optional note: OK = resolve and notify, Cancel = resolve silently.
      let note = '';
      let notify = false;
      if (user) {
        const answer = prompt(
          'Resolving the report from ' + user + '.\n\n' +
          'OK = resolve and send the follow-up email to the colleague (you can add an optional note below).\n' +
          'Cancel = resolve without sending an email.', '');
        notify = answer !== null;
        note = answer || '';
      }
      btnEl.disabled = true;
      btnEl.textContent = '…';
      fetch(apiBase + '/feedback/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Admin-Key': key },
        body: JSON.stringify({ ts, question, note, notify }),
      })
        .then(r => {
          if (r.status === 403) { _adminKey = null; btnEl.disabled = false; btnEl.textContent = '✓ Resolved'; alert('Invalid admin key.'); return; }
          if (!r.ok) throw new Error('HTTP ' + r.status);
          liEl.classList.add('resolving');
          setTimeout(() => { liEl.remove(); updateNegStatus(); updateStats(); }, 320);
        })
        .catch(() => { btnEl.disabled = false; btnEl.textContent = '✓ Resolved'; alert('Error while resolving.'); });
    }

    function updateNegStatus() {
      const statusEl = document.getElementById('neg-fb-status');
      const listEl = document.getElementById('neg-fb-list');
      const count = listEl.querySelectorAll(':scope > li').length;
      if (count === 0) {
        listEl.innerHTML = '<li><span class="neg-q" style="color:#666">No open negative feedback.</span></li>';
        statusEl.textContent = '— 0 open';
      }
    }

    function loadNegativeFeedback() {
      const statusEl = document.getElementById('neg-fb-status');
      const listEl = document.getElementById('neg-fb-list');
      fetch(apiBase + '/feedback/negative', { headers: adminHeaders() })
        .then(r => r.json())
        .then(data => {
          const items = (data.items || []).slice().reverse();
          const total = data.total_negative || 0;
          statusEl.textContent = '— ' + items.length + ' open out of ' + total + ' total';
          if (!items.length) {
            listEl.innerHTML = '<li><span class="neg-q" style="color:#666">No open negative feedback.</span></li>';
            return;
          }
          listEl.innerHTML = '';
          items.forEach(e => {
            const ts = (e.ts || '').replace('T', ' ').slice(0, 16);
            const li = document.createElement('li');
            const main = document.createElement('div');
            main.className = 'neg-main';
            let html = '<span class="neg-q">— ' + _esc(e.question || '') + '</span>';
            const meta = [ts, e.user ? '👤 ' + _esc(e.user) : ''].filter(Boolean).join(' · ');
            if (meta) html += '<div class="neg-meta">' + meta + '</div>';
            if (e.comment) html += '<div class="neg-comment">' + _esc(e.comment) + '</div>';
            if (e.urls && e.urls.length) {
              html += '<ul class="neg-urls">' + e.urls.map(u => {
                const safe = _esc(u);
                return '<li>🔗 <a href="' + _href(u) + '" target="_blank" rel="noopener noreferrer">' + safe + '</a></li>';
              }).join('') + '</ul>';
            }
            main.innerHTML = html;
            const btn = document.createElement('button');
            btn.className = 'btn-resolve';
            btn.textContent = '✓ Resolved';
            btn.addEventListener('click', () => resolveNegative(e.ts, e.question, li, btn, e.user || ''));
            li.appendChild(main);
            li.appendChild(btn);
            listEl.appendChild(li);
          });
        })
        .catch(() => {
          statusEl.textContent = '';
          listEl.innerHTML = '<li><span class="neg-q" style="color:#666">Error loading.</span></li>';
        });
    }

    function loadUsage() {
      const key = getAdminKey();
      if (!key) return;
      const body = document.getElementById('usage-body');
      const status = document.getElementById('usage-status');
      body.innerHTML = '<span style="color:#666;font-size:.85em">loading…</span>';
      fetch(apiBase + '/usage/summary', { headers: { 'X-Admin-Key': key } })
        .then(r => {
          if (r.status === 403) { _adminKey = null; throw new Error('key'); }
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(data => {
          const users = data.users || [];
          const reg = (data.total_registered != null ? data.total_registered : (data.total_users || 0));
          const act = (data.total_active != null ? data.total_active : users.length);
          status.textContent = '— ' + reg + ' registered · ' + act + ' active · ' + (data.total_messages || 0) + ' messages';
          if (!users.length) {
            body.innerHTML = '<span style="color:#666;font-size:.85em">No registered users.</span>';
            return;
          }
          let html = '<table style="width:100%;border-collapse:collapse;font-size:.9em">'
            + '<thead><tr style="text-align:left;color:#666;border-bottom:1px solid #eee">'
            + '<th style="padding:6px 8px">User</th>'
            + '<th style="padding:6px 8px;text-align:right">Messages</th>'
            + '<th style="padding:6px 8px;text-align:right">Active days</th>'
            + '<th style="padding:6px 8px">Last access</th></tr></thead><tbody>';
          users.forEach(u => {
            const last = u.last_seen ? u.last_seen.replace('T', ' ').slice(0, 16) : 'never';
            const dim = u.messages ? '' : 'color:#666;';
            html += '<tr style="border-bottom:1px solid #f3f3f3;' + dim + '">'
              + '<td style="padding:6px 8px">' + _esc(u.name) + '</td>'
              + '<td style="padding:6px 8px;text-align:right">' + _esc(u.messages) + '</td>'
              + '<td style="padding:6px 8px;text-align:right">' + _esc(u.active_days) + '</td>'
              + '<td style="padding:6px 8px;color:#666">' + _esc(last) + '</td></tr>';
          });
          html += '</tbody></table>';
          body.innerHTML = html;
        })
        .catch(e => {
          body.innerHTML = '<button id="usage-load-btn" class="btn-resolve">Retry (admin key)</button>';
          document.getElementById('usage-load-btn').addEventListener('click', loadUsage);
          if (String(e && e.message) === 'key') alert('Invalid admin key.');
        });
    }
    document.getElementById('usage-load-btn').addEventListener('click', loadUsage);

    // Start of the admin view: called by startAdminView() (dashboard.html)
    // after the panel has been inserted into the page.
    window.zautteAdminBoot = function () {
      updateStats();
      updateCrawlHistory();
      loadNegativeFeedback();
      loadUsage();
      loadMessages();
      setInterval(updateStats, 30000);
      setInterval(updateCrawlHistory, 60000);
      setInterval(loadNegativeFeedback, 60000);
      setInterval(loadMessages, 30000);
    };
