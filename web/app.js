// Top 10 of the verified SPP run (snapshot 2026-10-13.r2, commit 45a79a0).
const SHORTLIST = [
  {name: 'LN OSAGE_OG - WEBBTAP4', rent: 97.0, months: 12, ev: 2.22, p5: -1.55, p95: 10.65, get: 'DLR', none: 0.25},
  {name: 'LN CLEVLND7 - CLEV_AEC', rent: 83.3, months: 12, ev: 1.76, p5: -1.55, p95: 9.04, get: 'DLR', none: 0.26},
  {name: 'LN NEBRCTY - SUB3456', rent: 77.3, months: 12, ev: 1.62, p5: -1.67, p95: 8.57, get: 'DLR', none: 0.25},
  {name: 'LN RUSSETT - SBROWN', rent: 64.9, months: 12, ev: 1.22, p5: -1.76, p95: 6.98, get: 'DLR', none: 0.26},
  {name: 'LN FT_CAL - FT_CAL', rent: 42.6, months: 9, ev: 0.49, p5: -0.19, p95: 2.58, get: 'DLR', none: 0.42,
    caution: 'Same station name at both ends: possibly a bus tie or intra-station segment, where no GET applies.'},
  {name: 'LN OASIS - SAN_JUAN', rent: 22.0, months: 12, ev: 0.30, p5: -0.21, p95: 1.71, get: 'DLR', none: 0.42},
  {name: 'XFMR EDWV - EDWV', rent: 60.9, months: 8, ev: 0.28, p5: -0.25, p95: 1.85, get: 'TO', none: 0.70},
  {name: 'LN GORD - MAIZ', rent: 40.2, months: 6, ev: 0.27, p5: -0.22, p95: 1.54, get: 'DLR', none: 0.41},
  {name: 'LN ANADARKO - GRACMONT', rent: 30.6, months: 7, ev: 0.22, p5: -0.24, p95: 1.32, get: 'DLR', none: 0.42},
  {name: 'LN STONEWSW - TUPELO2', rent: 28.6, months: 7, ev: 0.20, p5: -0.23, p95: 1.19, get: 'DLR', none: 0.42}
];
const GET_NAME = {DLR: 'Dynamic line rating', TO: 'Topology optimization'};
const LO = -2, HI = 11;
const pos = v => `${((v - LO) / (HI - LO)) * 100}%`;

function evTrack(r) {
  const neg = r.p5 < 0 ? `<span class="neg" style="left:${pos(r.p5)};width:calc(${pos(0)} - ${pos(r.p5)})"></span>` : '';
  const from = Math.max(r.p5, 0);
  return `<span class="evtrack">${neg}<span class="rng" style="left:${pos(from)};width:calc(${pos(r.p95)} - ${pos(from)})"></span>` +
    `<span class="zero" style="left:${pos(0)}"></span><span class="pt" style="left:${pos(r.ev)}"></span></span>`;
}

const fmt = (v, d = 2) => (v < 0 ? '−' : '') + Math.abs(v).toFixed(d);

function renderShortlist() {
  const body = document.querySelector('#shortlist tbody');
  body.innerHTML = SHORTLIST.map((r, i) => `
    <tr data-i="${i}" title="${r.caution ?? ''}">
      <td class="rank">${i + 1}</td>
      <td class="name">${r.name}${r.caution ? ' <span class="chip mid">CHECK</span>' : ''}</td>
      <td class="r">$${r.rent.toFixed(1)}M</td>
      <td class="r">${r.months}</td>
      <td class="ev"><span class="evbar"><b>${r.ev.toFixed(2)}</b>${evTrack(r)}</span></td>
      <td><span class="get ${r.get === 'TO' ? 'to' : ''}">${r.get}</span></td>
      <td class="r">${r.none.toFixed(2)}</td>
    </tr>`).join('');
  body.addEventListener('click', e => {
    const tr = e.target.closest('tr');
    if (!tr) return;
    select(Number(tr.dataset.i));
    setView('screen');
    setTab('ranking');
  });
}

function select(i) {
  const r = SHORTLIST[i];
  document.querySelectorAll('#shortlist tbody tr').forEach(tr => tr.classList.toggle('sel', Number(tr.dataset.i) === i));
  document.getElementById('sel-title').textContent = `#${i + 1} ${r.name}`;
  document.getElementById('sel-get').textContent = r.get;
  const rows = [
    ['Element', r.name.startsWith('XFMR') ? 'Transformer' : 'Line'],
    ['Contingency', 'Binds under N-1'],
    ['2025 DA rent', `$${r.rent.toFixed(1)}M (champion flowgate)`],
    ['Months binding', `${r.months} of 12`],
    ['GET', GET_NAME[r.get]],
    ['P(none deployed)', r.none.toFixed(2)]
  ];
  document.getElementById('sel-rows').innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join('') +
    (r.caution ? `<div><span>Caution</span><span>${r.caution}</span></div>` : '');
  document.getElementById('sel-ev').innerHTML =
    `<div class="evbar"><b>${r.ev.toFixed(2)}</b>${evTrack(r)}</div>` +
    `<div class="axis"><span>p5 ${fmt(r.p5)}</span><span>expected net value, $M/yr</span><span>p95 ${fmt(r.p95)}</span></div>`;
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
renderShortlist();
select(0);
loadMap().catch(err => {
  document.getElementById('map').innerHTML = `<div class="loading">Map unavailable: ${err.message}</div>`;
});
