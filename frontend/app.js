/* Fault Lines frontend. No framework; D3 for the map. All data is inserted as text, never as HTML. */
(() => {
  'use strict';

  const TYPES = {
    conflict:  { label: 'Conflicts',  one: 'Conflict',  sub: 'sources contradict' },
    duplicate: { label: 'Duplicates', one: 'Duplicate', sub: 'future conflicts' },
    outdated:  { label: 'Outdated',   one: 'Outdated',  sub: 'stale or orphaned' },
    missing:   { label: 'Gaps',       one: 'Gap',       sub: 'unanswered questions' },
  };
  const W = 1200, H = 760, R = 168;
  const TOPIC_CENTERS = [[230, 220], [600, 220], [970, 220], [230, 548], [600, 548], [970, 548]];

  const state = {
    csrf: null, user: null, data: null, layout: null,
    t: null, days: [], filter: 'all', selected: null, playing: false, timer: null,
  };

  // ------------------------------------------------------------------ helpers
  const $ = (sel) => document.querySelector(sel);

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  const fmtDate = (iso) => iso ? new Date(iso.slice(0, 10) + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
  const fmtMonth = (iso) => new Date(iso.slice(0, 10) + 'T00:00:00').toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
  const todayIso = () => new Date().toISOString().slice(0, 10);
  const shortId = (id) => id.replace(/^KB-/, '');

  function hashSeed(str) {
    let x = 2166136261;
    for (let i = 0; i < str.length; i++) { x ^= str.charCodeAt(i); x = Math.imul(x, 16777619); }
    return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return ((x >>> 0) % 10000) / 10000; };
  }

  // The session lives in an HttpOnly cookie that scripts cannot read. State-changing
  // requests also send the CSRF token, which is only ever kept in memory.
  async function api(path, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    const headers = { 'Content-Type': 'application/json' };
    if (method !== 'GET' && state.csrf) headers['X-CSRF-Token'] = state.csrf;
    const res = await fetch(path, { ...opts, method, headers, credentials: 'same-origin' });
    if (res.status === 401 && !['/api/login', '/api/me', '/api/logout'].includes(path)) {
      resetSession(); showLogin(); throw new Error('Your session ended. Please sign in again.');
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Request failed');
    return body;
  }

  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { t.hidden = true; }, 3200);
  }

  // ------------------------------------------------------------------ auth
  async function showLogin() {
    $('#app').hidden = true; $('#login').hidden = false;
    const box = $('#login-users');
    box.replaceChildren();
    let users = [];
    try { users = await api('/api/demo-users'); } catch { /* demo mode off: type a username instead */ }
    let chosen = users[0]?.id;
    $('#login-users-field').hidden = users.length === 0;
    $('#login-username-field').hidden = users.length > 0;
    for (const u of users) {
      const opt = h('button', {
        type: 'button', class: 'user-option', role: 'radio', 'aria-checked': u.id === chosen ? 'true' : 'false',
        onclick: () => {
          chosen = u.id;
          box.querySelectorAll('.user-option').forEach((b) => b.setAttribute('aria-checked', 'false'));
          opt.setAttribute('aria-checked', 'true');
        },
      }, h('strong', { text: u.name }), h('span', { text: `${u.role === 'owner' ? 'Knowledge owner' : 'Consultant'} · ${u.scopes.join(' + ')}` }));
      box.append(opt);
    }
    $('#login-form').onsubmit = async (e) => {
      e.preventDefault();
      const err = $('#login-error'); err.hidden = true;
      try {
        const username = users.length ? chosen : $('#login-username').value.trim();
        const res = await api('/api/login', { method: 'POST', body: JSON.stringify({ username, password: $('#login-password').value }) });
        state.csrf = res.csrf_token; state.user = res.user;
        $('#login-password').value = '';
        await enterApp();
      } catch (ex) { err.textContent = ex.message; err.hidden = false; }
    };
  }

  function resetSession() {
    state.csrf = null; state.user = null; state.data = null;
    stopPlay(); closeDrawer(); $('#doc').hidden = true;
  }

  async function signOut() {
    try { await api('/api/logout', { method: 'POST', body: '{}' }); } catch { /* already signed out */ }
    resetSession();
    showLogin();
  }

  async function enterApp() {
    $('#login').hidden = true; $('#app').hidden = false;
    const u = state.user;
    $('#user-chip').replaceChildren(h('strong', { text: u.name }), ` · ${u.role === 'owner' ? 'Knowledge owner' : 'Consultant'}`);
    $('#scope-chips').replaceChildren(...u.scopes.map((s) => h('span', { class: `chip chip-${s.toLowerCase()}`, text: s })));
    await load();
  }

  // ------------------------------------------------------------------ data + layout
  async function load(keepTime = false) {
    const data = await api('/api/map');
    state.data = data;
    const start = new Date(data.start.slice(0, 7) + '-01T00:00:00');
    const endIso = [data.as_of, todayIso()].sort().pop();
    const end = new Date(endIso + 'T00:00:00');
    state.days = [];
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) state.days.push(d.toISOString().slice(0, 10));
    if (!state.days.length || state.days[state.days.length - 1] !== endIso) state.days.push(endIso);
    const slider = $('#time');
    slider.max = String(state.days.length - 1);
    if (!keepTime || !state.t) { slider.value = slider.max; state.t = endIso; }
    else { const i = state.days.indexOf(state.t); slider.value = String(i < 0 ? state.days.length - 1 : i); }
    renderTicks();
    state.layout = computeLayout(data);
    drawStatic();
    render();
  }

  function computeLayout(data) {
    const topics = data.topics.map((t, i) => ({ ...t, cx: TOPIC_CENTERS[i % 6][0], cy: TOPIC_CENTERS[i % 6][1] }));
    const byId = Object.fromEntries(topics.map((t) => [t.id, t]));
    const nodes = data.items.map((it) => ({ kind: 'item', id: it.id, item: it, topic: byId[it.topic], r: 6 + Math.sqrt(it.uses) * 1.3 }));
    for (const is of data.issues.filter((i) => i.type === 'missing')) {
      nodes.push({ kind: 'gap', id: `gap:${is.id}`, issue: is, topic: byId[is.topic], r: 20 + is.pressure / 8 });
    }
    const side = (n) => (n.kind === 'item' ? n.item.country : n.issue.country) === 'BE' ? -1 : 1;
    const twoCountries = new Set(data.items.map((i) => i.country)).size > 1;
    // Pull the two sides of every conflict/duplicate apart so the crack between them is visible.
    const ids = new Set(nodes.map((n) => n.id));
    const links = data.issues
      .filter((i) => (i.type === 'conflict' || i.type === 'duplicate') && i.item_ids.length === 2 && i.item_ids.every((x) => ids.has(x)))
      .map((i) => ({ source: i.item_ids[0], target: i.item_ids[1], dist: i.type === 'conflict' ? 150 : 110 }));
    const linked = new Set(links.flatMap((l) => [l.source, l.target]));
    const yOffset = (n) => n.kind === 'gap' ? 78 : linked.has(n.id) ? -40 : 10;
    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((n) => n.id).distance((l) => l.dist).strength(0.5))
      .force('x', d3.forceX((n) => n.topic.cx + (twoCountries ? side(n) * 52 : 0)).strength(0.16))
      .force('y', d3.forceY((n) => n.topic.cy + yOffset(n)).strength(n => n.kind === 'gap' ? 0.4 : 0.16))
      .force('collide', d3.forceCollide((n) => n.r + (n.kind === 'gap' ? 30 : 16)).iterations(3))
      .force('charge', d3.forceManyBody().strength(-24))
      .stop();
    for (let i = 0; i < 400; i++) sim.tick();
    for (const n of nodes) {
      const dx = n.x - n.topic.cx, dy = n.y - n.topic.cy, dist = Math.hypot(dx, dy), max = R - n.r - 22;
      if (dist > max) { n.x = n.topic.cx + dx / dist * max; n.y = n.topic.cy + dy / dist * max; }
    }
    return { topics, nodes, pos: Object.fromEntries(nodes.map((n) => [n.id, n])) };
  }

  // ------------------------------------------------------------------ time
  const openAt = (is, T) => is.arose_at <= T && !(is.resolved_at && is.resolved_at.slice(0, 10) <= T);
  const itemVisible = (it, T) => it.created_at <= T;
  const itemInactiveAt = (it, T) => it.state !== 'active' && it.state_changed_at && it.state_changed_at <= T;

  function renderTicks() {
    const years = [...new Set(state.days.map((d) => d.slice(0, 4)))];
    $('#time-ticks').replaceChildren(...years.map((y) => h('span', { text: y })));
  }

  function setTime(i) {
    const idx = Math.max(0, Math.min(state.days.length - 1, i));
    state.t = state.days[idx];
    $('#time').value = String(idx);
    render();
  }

  function stopPlay() {
    state.playing = false; clearInterval(state.timer); $('#play').textContent = '▶';
    $('#play').setAttribute('aria-label', 'Play time-lapse');
  }

  function togglePlay() {
    if (state.playing) return stopPlay();
    let i = Number($('#time').value);
    if (i >= state.days.length - 1) i = 0;
    const step = Math.max(1, Math.round(state.days.length / 200));
    state.playing = true; $('#play').textContent = '❚❚'; $('#play').setAttribute('aria-label', 'Pause time-lapse');
    state.timer = setInterval(() => {
      i += step;
      if (i >= state.days.length - 1) { setTime(state.days.length - 1); stopPlay(); return; }
      setTime(i);
    }, 40);
  }

  // ------------------------------------------------------------------ map
  function drawStatic() {
    const svg = d3.select('#map').attr('viewBox', `0 0 ${W} ${H}`).attr('preserveAspectRatio', 'xMidYMid meet');
    svg.selectAll('*').remove();
    const defs = svg.append('defs');
    const terrain = defs.append('radialGradient').attr('id', 'terrain');
    // Light, flat regions (Tailwind gray-100 / gray-50) for the enterprise theme.
    terrain.append('stop').attr('offset', '0%').attr('stop-color', '#f3f4f6').attr('stop-opacity', 1);
    terrain.append('stop').attr('offset', '75%').attr('stop-color', '#f9fafb').attr('stop-opacity', 0.9);
    terrain.append('stop').attr('offset', '100%').attr('stop-color', '#ffffff').attr('stop-opacity', 0);
    const crater = defs.append('radialGradient').attr('id', 'crater');
    crater.append('stop').attr('offset', '0%').attr('stop-color', '#0f172a');
    crater.append('stop').attr('offset', '100%').attr('stop-color', '#1e293b');
    const glow = defs.append('filter').attr('id', 'glow').attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%');
    glow.append('feGaussianBlur').attr('stdDeviation', 3.5).attr('result', 'b');
    const merge = glow.append('feMerge');
    merge.append('feMergeNode').attr('in', 'b'); merge.append('feMergeNode').attr('in', 'SourceGraphic');

    const regions = svg.append('g').attr('class', 'regions');
    const radial = d3.lineRadial().curve(d3.curveCardinalClosed.tension(0.2));
    for (const t of state.layout.topics) {
      const g = regions.append('g').attr('transform', `translate(${t.cx},${t.cy})`).attr('data-topic', t.id);
      g.append('circle').attr('class', 'region-fill').attr('r', R + 20);
      const rnd = hashSeed(t.id);
      const s1 = rnd() * 6, s2 = rnd() * 6;
      [0.42, 0.62, 0.82, 1.0].forEach((f, k) => {
        const pts = d3.range(0, 48).map((j) => {
          const a = (j / 48) * Math.PI * 2;
          return [a, R * f * (1 + 0.07 * Math.sin(3 * a + s1 + k) + 0.045 * Math.sin(5 * a + s2 - k))];
        });
        g.append('path').attr('class', 'contour').attr('data-ring', k).attr('d', radial(pts));
      });
      g.append('text').attr('class', 'region-label').attr('y', -R - 4).attr('text-anchor', 'middle').text(t.label);
      g.append('text').attr('class', 'region-count').attr('y', -R + 12).attr('text-anchor', 'middle');
    }
    svg.append('g').attr('class', 'layer-edges');
    svg.append('g').attr('class', 'layer-craters');
    svg.append('g').attr('class', 'layer-nodes');
  }

  function faultPath(a, b, seed, jag = 8) {
    const rnd = hashSeed(seed);
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len, steps = Math.max(5, Math.round(len / 16));
    const pts = [[a.x, a.y]];
    for (let i = 1; i < steps; i++) {
      const f = i / steps, off = (i % 2 ? 1 : -1) * (jag * 0.4 + rnd() * jag);
      pts.push([a.x + dx * f + nx * off, a.y + dy * f + ny * off]);
    }
    pts.push([b.x, b.y]);
    return d3.line()(pts);
  }

  function offsetLine(a, b, off) {
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, nx = -dy / len * off, ny = dx / len * off;
    return `M${a.x + nx},${a.y + ny}L${b.x + nx},${b.y + ny}`;
  }

  function showTip(evt, lines) {
    const tip = $('#tooltip'), wrap = $('.map-wrap').getBoundingClientRect();
    tip.replaceChildren(h('strong', { text: lines[0] }), ...lines.slice(1).map((l) => h('div', { class: 'muted', text: l })));
    tip.hidden = false;
    const x = Math.min(evt.clientX - wrap.left + 14, wrap.width - 270);
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${evt.clientY - wrap.top + 14}px`;
  }
  const hideTip = () => { $('#tooltip').hidden = true; };

  function render() {
    const { data, layout, t: T } = state;
    if (!data) return;
    const open = data.issues.filter((i) => openAt(i, T));
    const svg = d3.select('#map');
    const pos = layout.pos;
    const visible = new Set(data.items.filter((it) => itemVisible(it, T)).map((it) => it.id));

    // region heat + counts
    for (const tp of layout.topics) {
      const inTopic = open.filter((i) => i.topic === tp.id);
      const heat = d3.max(inTopic, (i) => i.pressure) || 0;
      const g = svg.select(`g[data-topic="${tp.id}"]`);
      g.selectAll('.contour').classed('contour-hot', function () { return heat >= 45 && Number(this.dataset.ring) >= 4 - Math.ceil(heat / 30); });
      const items = data.items.filter((it) => it.topic === tp.id && visible.has(it.id)).length;
      g.select('.region-count').text(`${items} item${items === 1 ? '' : 's'}${inTopic.length ? ` · ${inTopic.length} open` : ''}`);
    }

    // edges: conflicts + duplicates
    const edges = svg.select('.layer-edges');
    edges.selectAll('*').remove();
    for (const is of open.filter((i) => (i.type === 'conflict' || i.type === 'duplicate') && i.item_ids.length === 2)) {
      const [a, b] = is.item_ids.map((id) => pos[id]);
      if (!a || !b) continue;
      const g = edges.append('g').attr('class', `issue-g${state.selected === is.id ? ' selected' : ''}`)
        .on('click', () => openIssue(is.id))
        .on('mousemove', (e) => showTip(e, [is.title, `${TYPES[is.type].one} · pressure ${is.pressure}`]))
        .on('mouseleave', hideTip);
      if (is.type === 'conflict') {
        const w = 2 + is.pressure / 22;
        g.append('path').attr('class', `fault fault-conflict${is.pressure >= 60 ? ' hot' : ''}`).attr('stroke-width', w).attr('d', faultPath(a, b, is.id));
        if (is.pressure >= 60) g.append('circle').attr('class', 'shock').attr('cx', (a.x + b.x) / 2).attr('cy', (a.y + b.y) / 2).attr('r', 4);
      } else {
        g.append('path').attr('class', 'echo').attr('d', offsetLine(a, b, 4));
        g.append('path').attr('class', 'echo').attr('d', offsetLine(a, b, -4));
      }
      g.append('path').attr('class', 'fault-hit').attr('d', `M${a.x},${a.y}L${b.x},${b.y}`);
    }

    // craters: gaps
    const craters = svg.select('.layer-craters');
    craters.selectAll('*').remove();
    for (const is of open.filter((i) => i.type === 'missing')) {
      const n = pos[`gap:${is.id}`];
      if (!n) continue;
      const g = craters.append('g').attr('class', `issue-g${state.selected === is.id ? ' selected' : ''}`)
        .attr('transform', `translate(${n.x},${n.y})`)
        .on('click', () => openIssue(is.id))
        .on('mousemove', (e) => showTip(e, [is.title, `${is.uses} unanswered questions · pressure ${is.pressure}`]))
        .on('mouseleave', hideTip);
      g.append('circle').attr('class', 'crater-rim').attr('r', n.r);
      g.append('text').attr('class', 'crater-q').text('?');
      g.append('text').attr('class', 'crater-label').attr('y', n.r + 13).text(is.details.label || 'gap');
    }

    // nodes
    const outdated = new Map(open.filter((i) => i.type === 'outdated').map((i) => [i.item_ids[0], i]));
    const nodes = svg.select('.layer-nodes');
    nodes.selectAll('*').remove();
    for (const n of layout.nodes.filter((x) => x.kind === 'item' && visible.has(x.id))) {
      const it = n.item, od = outdated.get(it.id);
      const g = nodes.append('g')
        .attr('class', `node ${it.country.toLowerCase()}${itemInactiveAt(it, T) ? ' inactive' : ''}`)
        .attr('transform', `translate(${n.x},${n.y})`)
        .on('click', () => openDoc(it.id))
        .on('mousemove', (e) => showTip(e, [it.title, `${shortId(it.id)} · v${it.version} · ${itemInactiveAt(it, T) ? it.state : 'active'}`, `Owner: ${it.owner_name}${it.owner_active ? '' : ' (left)'} · ${it.uses} uses`]))
        .on('mouseleave', hideTip);
      if (od) {
        g.append('circle').attr('class', 'erosion-halo').attr('r', n.r + 10);
        g.append('circle').attr('class', 'erosion').attr('r', n.r + 6)
          .on('click', (e) => { e.stopPropagation(); openIssue(od.id); });
      }
      g.append('circle').attr('class', 'core').attr('r', n.r);
      g.append('text').attr('y', n.r + 12).attr('text-anchor', 'middle').text(shortId(it.id));
    }

    // time label
    $('#time-label').textContent = fmtDate(T);
    $('#time-sub').textContent = T >= state.days[state.days.length - 1] ? 'today' : 'time-lapse';

    renderKpis(open);
    renderList(open);
  }

  function renderKpis(open) {
    const box = $('#kpis');
    box.replaceChildren(...Object.entries(TYPES).map(([type, meta]) => {
      const n = open.filter((i) => i.type === type).length;
      return h('button', {
        type: 'button', class: `kpi kpi-${type}`, 'aria-pressed': state.filter === type ? 'true' : 'false',
        onclick: () => { state.filter = state.filter === type ? 'all' : type; render(); },
      }, h('span', { class: 'kpi-num', text: n }),
      h('span', { class: 'kpi-text' }, h('span', { class: 'kpi-label', text: meta.label }), h('br'), h('span', { class: 'kpi-sub', text: meta.sub })));
    }));
  }

  function issueCard(is) {
    return h('li', {
      class: `issue t-${is.type}${state.selected === is.id ? ' active' : ''}`, tabindex: '0',
      onclick: () => openIssue(is.id),
      onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openIssue(is.id); } },
    },
    h('span', { class: 'bar' }),
    h('div', {},
      h('div', { class: 'issue-top' },
        h('span', { class: `pill pill-${is.type}`, text: TYPES[is.type].one }),
        h('span', { class: `chip chip-${is.country.toLowerCase()}`, text: is.country }),
        is.state === 'assigned' ? h('span', { class: 'pill pill-state', text: 'assigned' }) : null),
      h('div', { class: 'issue-title', text: is.title }),
      h('div', { class: 'issue-meta' },
        h('span', { class: 'meter', title: `Pressure ${is.pressure}` }, h('i', { class: 'meter-fill', 'data-w': is.pressure })),
        h('span', { text: `${is.pressure}` }),
        h('span', { text: is.type === 'missing' ? `${is.uses} questions · ${is.clients} clients` : `${is.uses} uses · ${is.clients} clients` }))));
  }

  function applyMeters(root) {
    root.querySelectorAll('.meter-fill').forEach((el) => { el.style.width = `${el.dataset.w}%`; });
  }

  function renderList(open) {
    const list = $('#issue-list');
    const filtered = open.filter((i) => state.filter === 'all' || i.type === state.filter).sort((a, b) => b.pressure - a.pressure);
    list.replaceChildren(...(filtered.length ? filtered.map(issueCard) : [h('li', { class: 'empty', text: 'No open issues at this moment. Solid ground.' })]));
    applyMeters(list);

    const filters = $('#filters');
    filters.replaceChildren(...[['all', 'All'], ...Object.entries(TYPES).map(([k, v]) => [k, v.label])].map(([k, label]) =>
      h('button', { type: 'button', class: 'filter', 'aria-pressed': state.filter === k ? 'true' : 'false', onclick: () => { state.filter = k; render(); }, text: label })));

    const closed = state.data.issues.filter((i) => i.state === 'resolved' || i.state === 'accepted');
    $('#closed-count').textContent = `(${closed.length})`;
    const cl = $('#closed-list');
    cl.replaceChildren(...closed.map(issueCard));
    applyMeters(cl);
  }

  // ------------------------------------------------------------------ issue drawer
  function closeDrawer() {
    $('#drawer').hidden = true; $('#drawer-backdrop').hidden = true;
    state.selected = null;
    if (state.data) render();
  }

  async function openIssue(id) {
    hideTip();
    state.selected = id;
    render();
    const drawer = $('#drawer');
    drawer.replaceChildren(h('p', { class: 'muted', text: 'Loading…' }));
    drawer.hidden = false; $('#drawer-backdrop').hidden = false;
    try { renderDrawer(await api(`/api/issues/${encodeURIComponent(id)}`)); }
    catch (ex) { drawer.replaceChildren(h('p', { class: 'error', text: ex.message })); }
  }

  function sourceCard(it, issue) {
    const conflictClaim = (issue.details.claims || []).find((c) => c.item_id === it.id);
    const claims = conflictClaim ? [conflictClaim] : it.claims.slice(0, 1);
    const cutoff = it.state !== 'active' && it.state_changed_at ? it.state_changed_at.slice(0, 7) : null;
    const months = it.usage_by_month.slice(-18);
    const maxU = d3.max(months, (m) => m.uses) || 1;
    const spark = h('div', { class: 'spark', title: 'Usage per month' },
      ...months.map((m) => {
        const bar = h('i', { class: cutoff && m.month >= cutoff ? 'after' : null, title: `${m.month}: ${m.uses}`, 'data-h': Math.round((m.uses / maxU) * 100) });
        return bar;
      }));
    return h('div', { class: `source${it.state === 'active' ? '' : ''}` },
      h('div', { class: 'issue-top' },
        h('span', { class: `pill pill-${it.state}`, text: it.state }),
        h('span', { class: 'id', text: `${it.id} · v${it.version}` })),
      h('h4', { text: it.title }),
      ...claims.map((c) => h('div', { class: `claim${issue.type === 'conflict' ? ' claim-conflict' : ''}`, text: c.text })),
      h('dl', { class: 'kv' },
        h('dt', { text: 'Owner' }), h('dd', { class: it.owner_active ? null : 'warn', text: it.owner_active ? it.owner_name : `${it.owner_name} (left the company)` }),
        h('dt', { text: 'Source' }), h('dd', { text: it.source }),
        h('dt', { text: 'Created' }), h('dd', { text: fmtDate(it.created_at) }),
        h('dt', { text: 'Review by' }), h('dd', { class: it.review_by && it.review_by < state.data.as_of && it.state === 'active' ? 'warn' : null, text: fmtDate(it.review_by) || '—' })),
      months.length ? h('div', {}, h('div', { class: 'muted', text: 'Usage per month' }), spark) : null,
      it.recent_clients.length ? h('div', { class: 'chips chips-wrap' }, ...it.recent_clients.slice(0, 5).map((c) => h('span', { class: 'chip', text: c }))) : null,
      h('button', { type: 'button', class: 'btn btn-sm', onclick: () => openDoc(it.id), text: 'Open document' }));
  }

  function historyList(issue) {
    const ev = [];
    for (const it of issue.items) ev.push({ at: it.created_at, text: `${shortId(it.id)} v${it.version} published by ${it.owner_name}` });
    ev.push({ at: issue.arose_at, text: 'Tension formed', cls: 'h-arose' });
    for (const hEv of issue.history) {
      if (hEv.action === 'issue.detected') ev.push({ at: hEv.at, text: 'Detected by Fault Lines' });
      else if (hEv.action.startsWith('issue.')) ev.push({ at: hEv.at, text: `${hEv.actor_name}: ${hEv.detail || hEv.action.replace('issue.', '')}`, cls: /resolved|accepted|cleared/.test(hEv.action) ? 'h-closed' : null });
      else if (hEv.action.startsWith('item.')) ev.push({ at: hEv.at, text: `${hEv.actor_name} ${hEv.action.replace('item.', 'marked ')} ${shortId(hEv.target)}` });
    }
    ev.sort((a, b) => a.at.localeCompare(b.at));
    return h('ol', { class: 'history' }, ...ev.map((e) => h('li', { class: e.cls }, h('time', { text: fmtDate(e.at) }), e.text)));
  }

  function actionPanel(issue) {
    if (!issue.allowed_actions.length) {
      if (issue.state === 'detected' && state.user.role !== 'owner') {
        return h('p', { class: 'muted', text: 'Only a knowledge owner for this scope can resolve this.' });
      }
      return null;
    }
    const note = h('textarea', { maxlength: '500', placeholder: 'Note for the audit trail (required when accepting)' });
    const err = h('p', { class: 'error', hidden: true });
    const run = async (payload, btn) => {
      err.hidden = true;
      btn.disabled = true;
      try {
        const res = await api(`/api/issues/${encodeURIComponent(issue.id)}/resolve`, { method: 'POST', body: JSON.stringify({ ...payload, note: note.value || null }) });
        const openBefore = new Set(state.data.issues.filter(isOpen).map((i) => i.id));
        await animateClose(issue.id);
        await load(true);
        const cleared = state.data.issues.filter((i) => i.id !== issue.id && openBefore.has(i.id) && !isOpen(i)).length;
        toast(res.state === 'assigned' ? 'Assigned to you.' : `Resolved.${cleared > 0 ? ` ${cleared} related issue${cleared > 1 ? 's' : ''} cleared too.` : ''}`);
        await openIssue(issue.id);
      } catch (ex) { err.textContent = ex.message; err.hidden = false; btn.disabled = false; }
    };
    const row = h('div', { class: 'actions-row' });
    const acts = issue.allowed_actions;
    if (acts.includes('keep')) {
      for (const it of issue.items) {
        const verb = issue.type === 'conflict' ? 'deprecate' : 'retire';
        const b = h('button', { type: 'button', class: 'btn btn-primary', text: `Keep ${shortId(it.id)} v${it.version}, ${verb} the other` });
        b.addEventListener('click', () => run({ action: 'keep', keep_item_id: it.id }, b));
        row.append(b);
      }
    }
    const mk = (action, label, cls = 'btn') => {
      const b = h('button', { type: 'button', class: cls, text: label });
      b.addEventListener('click', () => run({ action }, b));
      row.append(b);
    };
    if (acts.includes('reconfirm')) mk('reconfirm', issue.details.reasons?.some((r) => r.kind === 'owner_left') ? 'Reconfirm and take ownership' : 'Reconfirm as correct', 'btn btn-primary');
    if (acts.includes('retire')) mk('retire', 'Retire this item', 'btn btn-danger');
    if (acts.includes('assign') && issue.state !== 'assigned') mk('assign', "I'll write it (assign to me)", 'btn btn-primary');
    if (acts.includes('accept')) mk('accept', issue.type === 'missing' ? 'Not needed (with note)' : 'Both valid (with note)');
    return h('div', { class: 'actions' }, h('strong', { text: 'Resolve at the source' }), row, note, err);
  }

  function isOpen(i) { return i.state === 'detected' || i.state === 'assigned'; }

  function animateClose(id) {
    return new Promise((resolve) => {
      const sel = d3.select('#map').selectAll('.issue-g.selected');
      if (sel.empty()) return resolve();
      sel.transition().duration(700).style('opacity', 0).on('end', resolve);
      setTimeout(resolve, 800);
    });
  }

  function renderDrawer(is) {
    const drawer = $('#drawer');
    const closed = is.state === 'resolved' || is.state === 'accepted';
    const reasons = is.details.reasons || [];
    const parts = [
      h('div', { class: 'drawer-head' },
        h('div', { class: 'chips' },
          h('span', { class: `pill pill-${is.type}`, text: TYPES[is.type].one }),
          h('span', { class: `chip chip-${is.country.toLowerCase()}`, text: is.country }),
          h('span', { class: `pill ${closed ? 'pill-active' : 'pill-state'}`, text: is.state })),
        h('button', { type: 'button', class: 'btn btn-ghost btn-sm close', onclick: closeDrawer, text: 'Close ✕' })),
      h('h2', { text: is.title }),
      h('p', { class: 'lede', text: is.explanation }),
    ];
    if (closed) {
      parts.push(h('div', { class: 'resolution' },
        h('strong', { text: is.state === 'accepted' ? 'Accepted' : 'Resolved' }),
        ` ${is.resolved_by_name ? `by ${is.resolved_by_name} ` : 'automatically '}on ${fmtDate(is.resolved_at)}. `, is.resolution || ''));
    }
    if (is.state === 'assigned' && is.assigned_to_name) parts.push(h('p', { class: 'muted', text: `Assigned to ${is.assigned_to_name}.` }));

    parts.push(h('div', { class: 'section' }, h('h3', { text: `Pressure, last 90 days` }),
      h('div', { class: 'pressure-box' },
        h('div', { class: 'pressure-num', text: is.pressure }, h('small', { text: 'of 100' })),
        h('div', { class: 'stats' },
          h('div', {}, h('strong', { text: is.uses }), h('span', { text: is.type === 'missing' ? 'questions asked' : 'times used' })),
          h('div', {}, h('strong', { text: is.clients }), h('span', { text: 'clients affected' })),
          h('div', {}, h('strong', { text: is.people }), h('span', { text: 'colleagues' }))))));

    if (reasons.length) {
      parts.push(h('div', { class: 'section' }, h('h3', { text: 'Why it is outdated' }),
        ...reasons.map((r) => h('div', { class: 'claim', text: r.text }))));
    }
    if (is.items.length) {
      parts.push(h('div', { class: 'section' }, h('h3', { text: is.type === 'conflict' ? 'The two sides of the fault line' : 'Sources' }),
        h('div', { class: 'sources' }, ...is.items.map((it) => sourceCard(it, is)))));
    }
    if (is.details.questions) {
      parts.push(h('div', { class: 'section' }, h('h3', { text: 'Questions nobody could answer from the knowledge base' }),
        h('ul', { class: 'questions' }, ...is.details.questions.map((q) =>
          h('li', {}, h('div', { class: 'q', text: `“${q.text}”` }),
            h('div', { class: 'who', text: `${q.author_name || 'Unknown'}${q.client_name ? ` · for ${q.client_name}` : ''} · ${fmtDate(q.created_at)}` }))))));
      if (is.details.reference) {
        parts.push(h('p', { class: 'muted' }, 'Closest match is in another country: ',
          h('button', { type: 'button', class: 'btn btn-sm', onclick: () => openDoc(is.details.reference.id), text: `${is.details.reference.title}` })));
      }
    }
    parts.push(h('div', { class: 'section' }, h('h3', { text: 'How this fault line formed' }), historyList(is)));
    const act = actionPanel(is);
    if (act) parts.push(h('div', { class: 'section' }, act));
    drawer.replaceChildren(...parts);
    drawer.querySelectorAll('.spark i').forEach((el) => { el.style.height = `${Math.max(6, Number(el.dataset.h))}%`; });
    drawer.scrollTop = 0;
  }

  // ------------------------------------------------------------------ document viewer
  async function openDoc(id) {
    hideTip();
    const modal = $('#doc');
    let it;
    try { it = await api(`/api/items/${encodeURIComponent(id)}`); } catch (ex) { toast(ex.message); return; }
    const problems = [];
    if (it.replaced_by) problems.push(h('button', { type: 'button', onclick: () => openDoc(it.replaced_by.id), text: `A newer version exists: ${it.replaced_by.title} v${it.replaced_by.version} →` }));
    if (!it.owner_active) problems.push(h('div', { text: `⚠ The owner of this document (${it.owner_name}) has left the company.` }));
    for (const is of it.open_issues) {
      problems.push(h('button', { type: 'button', onclick: () => { modal.hidden = true; openIssue(is.id); }, text: `⚠ ${TYPES[is.type].one}: ${is.title} →` }));
    }
    const banner = problems.length
      ? h('div', { class: 'doc-banner bad' }, h('strong', { text: 'Fault Lines: be careful before relying on this document' }), ...problems)
      : h('div', { class: 'doc-banner good' }, h('strong', { text: `✓ ${it.state === 'active' ? 'Active' : it.state}, owned by ${it.owner_name}, next review ${fmtDate(it.review_by)}. No known issues.` }));
    const card = h('div', { class: 'doc', role: 'dialog', 'aria-modal': 'true', 'aria-label': it.title },
      banner,
      h('div', { class: 'doc-body' },
        h('h2', { text: it.title }),
        h('div', { class: 'doc-meta', text: `${it.id} · version ${it.version} · ${it.state} · ${it.source} · ${it.country} · published ${fmtDate(it.created_at)}` }),
        h('p', { text: it.body })),
      h('div', { class: 'doc-foot' }, h('button', { type: 'button', class: 'btn', onclick: () => { modal.hidden = true; }, text: 'Close' })));
    modal.replaceChildren(card);
    modal.hidden = false;
  }

  // ------------------------------------------------------------------ boot
  function boot() {
    $('#time').addEventListener('input', (e) => { stopPlay(); setTime(Number(e.target.value)); });
    $('#play').addEventListener('click', togglePlay);
    $('#logout').addEventListener('click', signOut);
    $('#drawer-backdrop').addEventListener('click', closeDrawer);
    $('#doc').addEventListener('click', (e) => { if (e.target.id === 'doc') e.currentTarget.hidden = true; });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (!$('#doc').hidden) $('#doc').hidden = true;
      else if (!$('#drawer').hidden) closeDrawer();
    });

    // Resume an existing session (the cookie is sent automatically), else show sign-in.
    api('/api/me')
      .then((res) => { state.user = res.user; state.csrf = res.csrf_token; return enterApp(); })
      .catch(() => showLogin());
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
