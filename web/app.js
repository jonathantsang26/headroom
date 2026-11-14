// The first shortlist_k rows of the verified SPP run, exported by build_data.py.
let ROWS = [];
const GET_NAME = {
  DLR: 'Dynamic line rating', TO: 'Topology optimization', APFC: 'Advanced power flow control',
  COMB: 'Combination', '': 'None worth deploying'
};
const EVIDENCE = {
  line_n1: 'Line, binds under N-1', line_n1_episodic: 'Line, episodic N-1', line_base: 'Line, base case',
  line_radial: 'Line, radial', transformer_n1: 'Transformer, binds under N-1',
  transformer_base: 'Transformer, base case', interface: 'Interface', loopflow: 'Loop flow'
};
const CAUTION = {
  same_station_ends: 'Same station name at both ends: possibly a bus tie or intra-station segment, where no GET applies.'
};
const FIRST_DIR = {rank: 1, rent: -1, months: -1, ev: -1, none: 1};
const LO = -2, HI = 11;
const pos = v => `${((v - LO) / (HI - LO)) * 100}%`;
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c]);
const fmt = (v, d = 2) => (v < 0 ? '−' : '') + Math.abs(v).toFixed(d);
const state = {q: '', f: 'all', key: 'rank', dir: 1, sel: 1};

function evTrack(r) {
  const neg = r.p5 < 0 ? `<span class="neg" style="left:${pos(r.p5)};width:calc(${pos(0)} - ${pos(r.p5)})"></span>` : '';
  const from = Math.max(r.p5, 0);
  return `<span class="evtrack">${neg}<span class="rng" style="left:${pos(from)};width:calc(${pos(r.p95)} - ${pos(from)})"></span>` +
    `<span class="zero" style="left:${pos(0)}"></span><span class="pt" style="left:${pos(r.ev)}"></span></span>`;
}

function visible() {
  const q = state.q.trim().toLowerCase();
  return ROWS
    .filter(r => state.f === 'all' || (state.f === 'worth') === (r.get !== ''))
    .filter(r => !q || `${r.corridor} ${r.flowgate} ${r.contingency}`.toLowerCase().includes(q))
    .sort((a, b) => (a[state.key] - b[state.key]) * state.dir || a.rank - b.rank);
}

function render() {
  const rows = visible();
  document.querySelector('#shortlist tbody').innerHTML = rows.map(r => `
    <tr data-rank="${r.rank}" class="${r.rank === state.sel ? 'sel' : ''}" title="${esc(r.flowgate)}">
      <td class="rank">${r.rank}</td>
      <td class="name">${esc(r.corridor)}${r.caution ? ' <span class="chip mid">CHECK</span>' : ''}</td>
      <td class="r">$${r.rent.toFixed(1)}M</td>
      <td class="r">${r.months}</td>
      <td class="ev"><span class="evbar"><b>${r.ev.toFixed(2)}</b>${evTrack(r)}</span></td>
      <td>${r.get ? `<span class="get ${r.get === 'TO' ? 'to' : ''}">${r.get}</span>` : '<span class="muted">—</span>'}</td>
      <td class="r">${r.none.toFixed(2)}</td>
    </tr>`).join('');
  document.getElementById('sl-empty').hidden = rows.length > 0;
  document.getElementById('sl-count').textContent =
    `${rows.length} of ${ROWS.length} flowgates · ranked by expected net value of a GET decision`;
  document.querySelectorAll('#shortlist th[data-sort]').forEach(th => {
    const on = th.dataset.sort === state.key;
    th.classList.toggle('sorted', on);
    th.classList.toggle('asc', on && state.dir === 1);
  });
}

function select(rank) {
  const r = ROWS.find(x => x.rank === rank);
  if (!r) return;
  state.sel = rank;
  document.querySelectorAll('#shortlist tbody tr').forEach(tr => tr.classList.toggle('sel', Number(tr.dataset.rank) === rank));
  document.getElementById('sel-title').textContent = `#${r.rank} ${r.corridor}`;
  document.getElementById('sel-get').textContent = r.get || 'NONE';
  const rows = [
    ['Flowgate', `<span class="mono">${esc(r.flowgate)}</span>`],
    ['Element', EVIDENCE[r.evidence] ?? esc(r.evidence)],
    ['Contingency', r.contingency ? `<span class="mono">${esc(r.contingency)}</span>` : '—'],
    ['2025 DA rent', `$${r.rent.toFixed(1)}M`],
    ['Binding', `${r.months} of 12 months · ${r.hours.toLocaleString()} h`],
    ['GET', r.get ? `${GET_NAME[r.get]} · default for its class` : GET_NAME[r.get]],
    ['P(none deployed)', r.none.toFixed(2)]
  ];
  if (r.caution) rows.push(['Caution', CAUTION[r.caution] ?? esc(r.caution)]);
  document.getElementById('sel-rows').innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join('');
  document.getElementById('sel-ev').innerHTML =
    `<div class="evbar"><b>${r.ev.toFixed(2)}</b>${evTrack(r)}</div>` +
    `<div class="axis"><span>p5 ${fmt(r.p5)}</span><span>expected net value, $M/yr</span><span>p95 ${fmt(r.p95)}</span></div>`;
}

async function loadShortlist() {
  const data = await (await fetch('shortlist.json')).json();
  ROWS = data.rows.map(v => Object.fromEntries(data.columns.map((c, i) => [c, v[i]])));
  const counts = {all: ROWS.length, worth: ROWS.filter(r => r.get).length, watch: ROWS.filter(r => !r.get).length};
  document.querySelectorAll('#sl-filter button').forEach(b => {
    b.querySelector('span').textContent = counts[b.dataset.f];
    b.addEventListener('click', () => {
      state.f = b.dataset.f;
      document.querySelectorAll('#sl-filter button').forEach(x => x.classList.toggle('on', x === b));
      render();
    });
  });
  document.getElementById('sl-search').addEventListener('input', e => { state.q = e.target.value; render(); });
  document.querySelectorAll('#shortlist th[data-sort]').forEach(th => th.addEventListener('click', () => {
    const key = th.dataset.sort;
    state.dir = key === state.key ? -state.dir : FIRST_DIR[key];
    state.key = key;
    render();
  }));
  document.querySelector('#shortlist tbody').addEventListener('click', e => {
    const tr = e.target.closest('tr');
    if (!tr) return;
    select(Number(tr.dataset.rank));
    setView('screen');
    setTab('ranking');
  });
  render();
  select(1);
}

function setView(v) {
  document.querySelectorAll('.seg button').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  document.querySelectorAll('[data-rail]').forEach(el => (el.hidden = el.dataset.rail !== v));
}

function setTab(t) {
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
  document.querySelectorAll('[data-pane]').forEach(el => (el.hidden = el.dataset.pane !== t));
}

// Map: layer toggles, opacity, pan and zoom on the SVG viewBox.
async function loadMap() {
  const host = document.getElementById('map');
  const text = await (await fetch('topology.svg')).text();
  host.innerHTML = text;
  const svg = host.querySelector('svg');
  const base = svg.getAttribute('viewBox').split(' ').map(Number);
  let vb = [...base];
  const apply = () => svg.setAttribute('viewBox', vb.map(n => n.toFixed(2)).join(' '));
  const unitsPerPx = () => Math.max(vb[2] / host.clientWidth, vb[3] / host.clientHeight);

  const layer = id => svg.querySelector(`[data-layer="${id}"]`);
  document.querySelectorAll('#layers li').forEach(li => {
    const box = li.querySelector('input[type="checkbox"][data-layer]');
    const range = li.querySelector('input[type="range"]');
    if (!box) return;
    const el = layer(box.dataset.layer);
    const sync = () => {
      el.style.display = box.checked ? '' : 'none';
      el.style.opacity = range.value;
      range.disabled = !box.checked;
      li.classList.toggle('dim', !box.checked);
      li.querySelector('.pct').textContent = `${Math.round(range.value * 100)}%`;
    };
    box.addEventListener('change', sync);
    range.addEventListener('input', sync);
    sync();
  });

  const zoomAt = (factor, px, py) => {
    const rect = host.getBoundingClientRect();
    const u = unitsPerPx();
    const cx = vb[0] + vb[2] / 2 + (px - rect.width / 2) * u;
    const cy = vb[1] + vb[3] / 2 + (py - rect.height / 2) * u;
    const w = Math.min(base[2] * 1.5, Math.max(base[2] / 14, vb[2] * factor));
    const k = w / vb[2];
    vb = [cx - (cx - vb[0]) * k, cy - (cy - vb[1]) * k, vb[2] * k, vb[3] * k];
    apply();
  };
  host.addEventListener('wheel', e => {
    e.preventDefault();
    const r = host.getBoundingClientRect();
    zoomAt(Math.exp(e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
  }, {passive: false});

  let drag = null;
  host.addEventListener('pointerdown', e => {
    drag = {x: e.clientX, y: e.clientY};
    host.setPointerCapture(e.pointerId);
    host.classList.add('dragging');
  });
  host.addEventListener('pointermove', e => {
    if (!drag) return;
    const u = unitsPerPx();
    vb[0] -= (e.clientX - drag.x) * u;
    vb[1] -= (e.clientY - drag.y) * u;
    drag = {x: e.clientX, y: e.clientY};
    apply();
  });
  const end = () => { drag = null; host.classList.remove('dragging'); };
  host.addEventListener('pointerup', end);
  host.addEventListener('pointercancel', end);

  const centre = f => zoomAt(f, host.clientWidth / 2, host.clientHeight / 2);
  document.getElementById('zoom-in').addEventListener('click', () => centre(0.7));
  document.getElementById('zoom-out').addEventListener('click', () => centre(1 / 0.7));
  document.getElementById('fit').addEventListener('click', () => { vb = [...base]; apply(); });
}

document.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));
document.getElementById('key-toggle').addEventListener('click', e => {
  const key = document.getElementById('key');
  key.classList.toggle('collapsed');
  e.target.textContent = key.classList.contains('collapsed') ? 'Show details' : 'Hide details';
});

if (matchMedia('(max-width: 820px)').matches) document.getElementById('key-toggle').click();
loadShortlist().catch(err => {
  document.getElementById('sl-empty').hidden = false;
  document.getElementById('sl-empty').textContent = `Shortlist unavailable: ${err.message}`;
});
loadMap().catch(err => {
  document.getElementById('map').innerHTML = `<div class="loading">Map unavailable: ${err.message}</div>`;
});
