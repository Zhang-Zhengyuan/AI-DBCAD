// ============== 组件实体级投影图（P6/ADR-3） ==============

async function openEntityGraph(hashSat, label) {
  const short = String(hashSat || '').slice(0, 12);
  const body = openModal(`实体投影 · ${short}…`);
  const limits = [120, 300, 1000, 2000, 5000];
  const saved = Number(sessionStorage.getItem('dbcad_graph_limit'));
  const initial = limits.includes(saved) ? saved : 300;
  body.innerHTML = `
    <div class="graph-controls">
      <label>节点显示上限 <select aria-label="节点显示上限">${limits.map(n =>
        `<option value="${n}"${n === initial ? ' selected' : ''}>${n.toLocaleString()}</option>`).join('')}</select></label>
      <button class="btn ghost" data-graph-reload>重新加载</button>
      <span class="muted" role="status" aria-live="polite" data-graph-status></span>
    </div>
    <div data-graph-content></div>`;
  const select = body.querySelector('select');
  const content = body.querySelector('[data-graph-content]');
  const status = body.querySelector('[data-graph-status]');
  let sequence = 0;
  async function load() {
    const request = ++sequence;
    const limit = Number(select.value);
    sessionStorage.setItem('dbcad_graph_limit', String(limit));
    status.textContent = `正在加载，节点上限 ${limit.toLocaleString()}…`;
    content.setAttribute('aria-busy', 'true');
    try {
      const url = `${API}/components/${encodeURIComponent(hashSat)}/entity-graph?limit=${limit}` +
        (label ? `&label=${encodeURIComponent(label)}` : '');
      const data = await getJSON(url);
      // Changing the limit or opening another modal must retire the old response.
      if (request !== sequence || !content.isConnected) return;
      renderEntityGraphModal(content, data);
      status.textContent = data.truncated || data.relationships_truncated ? '已显示部分投影，详见下方统计。' : '当前查询范围已完整显示。';
    } catch (e) {
      if (request !== sequence || !content.isConnected) return;
      status.textContent = `加载失败：${e.message}。可调整上限或重新加载。`;
    } finally {
      if (request === sequence && content.isConnected) content.removeAttribute('aria-busy');
    }
  }
  select.addEventListener('change', load);
  body.querySelector('[data-graph-reload]').addEventListener('click', load);
  await load();
}

function renderEntityGraphModal(body, data) {
  const graph = {
    nodes: (data.nodes || []).map(n => ({
      id: n.id,
      labels: n.labels && n.labels.length ? n.labels : ['entity'],
      props: n.props || { entity_type: n.entity_type, geometry_type: n.geometry_type, top_level: n.top_level },
      reference_count: n.reference_count,
    })),
    rels: data.rels || [],
  };
  body.innerHTML = `
    <table class="kv-table">
      <tr><th>hash_sat</th><td class="mono">${escapeHTML(data.hash_sat || '')}</td></tr>
      <tr><th>组件名</th><td>${escapeHTML(data.name || 'component')}</td></tr>
      <tr><th>实体内容节点</th><td>${(data.total_entities || 0).toLocaleString()} 个；组件内局部实体引用 ${(data.total_entity_references || 0).toLocaleString()} 次</td></tr>
      <tr><th>已显示节点</th><td>${(data.returned || 0).toLocaleString()} / ${(data.matched_entities || 0).toLocaleString()}${data.label_filter ? `（${escapeHTML(data.label_filter)} 筛选）` : ''} · ${data.truncated ? '已截断，可提高节点显示上限' : '节点未截断'}</td></tr>
      <tr><th>已显示拓扑关系</th><td>${(data.rels || []).length.toLocaleString()} 条${data.relationships_truncated ? ` · 已截断，关系安全上限 ${data.relationship_limit.toLocaleString()} 条` : ' · 关系未截断（当前可见节点之间）'}</td></tr>
    </table>
    ${renderNeo4jGraph(graph)}
    <p class="muted mt-16" style="font-size: var(--fz-sm);">
      【解读】投影挂在不可变组件之下（HAS_ENTITY）；Body→Lump→Shell→Face→Loop→Coedge/Edge→Vertex
      为 ACIS 拓扑层级。这里只显示当前组件的共享内容投影，不等同于原始 GraphJson 拓扑实例图。
      滚轮缩放、拖动平移；点击节点查看属性与相邻关系。
    </p>
  `;
  const graphRoot = body.querySelector('[data-neo4j-graph]');
  if (graphRoot) bindNeo4jGraph(graphRoot, graph);
}

function renderNeo4jGraph(graph) {
  if (!graph || !graph.nodes || !graph.nodes.length) return '<div class="neo4j-graph-detail">当前版本没有可绘制的实体图。</div>';
  const nodes = graph.nodes;
  const ids = new Set(nodes.map(n => n.id));
  const rels = (graph.rels || []).filter(r => ids.has(r.start) && ids.has(r.end));
  const positions = new Map();
  const radius = Math.max(220, Math.sqrt(nodes.length) * 29);
  const width = Math.max(900, radius * 3.4 + 80), height = Math.max(460, radius * 1.8 + 80);
  nodes.forEach((n, i) => {
    const root = (n.labels || []).map(String).map(x => x.toLowerCase()).includes('body');
    const r = root ? 0 : 29 * Math.sqrt(i + 1), angle = i * 2.399963;
    positions.set(n.id, { x: width / 2 + r * 1.7 * Math.cos(angle), y: height / 2 + r * .9 * Math.sin(angle) });
  });
  const presentTypes = [...new Set(nodes.flatMap(n => (n.labels || []).map(x => String(x).toLowerCase())))];
  const legend = presentTypes.map(key => `${escapeHTML(topologyLabelZh(key))}`).join('　·　');
  const colors = { body: '#5fb6ff', lump: '#b48ad6', shell: '#7fcf9a', face: '#f4a261', loop: '#f0c674', coedge: '#8493b3', edge: '#e76f51', vertex: '#6fd9d2' };
  return `<div data-neo4j-graph class="neo4j-graph-wrap">
    <div class="graph-controls"><span class="muted">已绘制 ${nodes.length.toLocaleString()} 个节点 / ${rels.length.toLocaleString()} 条关系</span>
      <button class="btn tiny" data-graph-zoom="in" aria-label="放大拓扑图">＋</button>
      <button class="btn tiny" data-graph-zoom="out" aria-label="缩小拓扑图">－</button>
      <button class="btn tiny" data-graph-zoom="fit">适应视图</button></div>
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Neo4j CAD 拓扑图" data-graph-canvas>
    ${rels.map(r => { const a=positions.get(r.start), b=positions.get(r.end); return `<line data-topology-edge x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="var(--border)" stroke-width="1.2" opacity=".7"><title>${escapeHTML(r.type || '关系')}</title></line>`; }).join('')}
    ${nodes.map(n => { const p=positions.get(n.id), key=String((n.labels||[])[0]||'entity').toLowerCase(), body=key==='body'; return `<g class="neo4j-graph-node" data-node-id="${escapeAttr(n.id)}" transform="translate(${p.x},${p.y})"><circle r="${body?18:8}" fill="${colors[key] || 'var(--surface)'}" stroke="var(--cyan)" stroke-width="${body?3:1}"/><text y="${body?34:21}" text-anchor="middle" fill="var(--text)" font-size="${body?13:10}">${escapeHTML(topologyLabelZh(key))}</text></g>`; }).join('')}
  </svg><div class="neo4j-graph-legend">${legend || '实体节点'}　·　线条表示 Neo4j 中实际保存的拓扑关系</div><div class="neo4j-graph-detail" data-graph-detail>点击节点查看 UUID、属性和相邻关系</div></div>`;
}

function _topologyLabel(key) {
  return ({body:'Body', lump:'Lump', shell:'Shell', face:'Face', loop:'Loop', coedge:'Coedge', edge:'Edge', vertex:'Vertex', transform:'Transform'})[key] || key;
}

function topologyLabelZh(key) {
  const k = String(key || '').toLowerCase();
  return ({body:'Body 实体', lump:'Lump 体块', shell:'Shell 壳体', face:'Face 面', loop:'Loop 环', coedge:'Coedge 半边', edge:'Edge 边', vertex:'Vertex 顶点', transform:'Transform 变换'})[k] || _topologyLabel(k) || '实体节点';
}

function bindNeo4jGraph(root, graph) {
  const detail = root.querySelector('[data-graph-detail]');
  const byId = new Map((graph.nodes || []).map(n => [n.id, n]));
  let dragged = false;
  bindGraphViewport(root, () => { dragged = true; }, () => { dragged = false; });
  root.querySelectorAll('[data-node-id]').forEach(node => node.addEventListener('click', () => {
    if (dragged) return;
    const item = byId.get(node.dataset.nodeId);
    if (!item || !detail) return;
    const key = String((item.labels || ['entity'])[0]).toLowerCase();
    const adjacent = (graph.rels || []).filter(r => r.start === item.id || r.end === item.id).slice(0, 12).map(r => {
      const otherId = r.start === item.id ? r.end : r.start;
      const other = byId.get(otherId);
      const otherKey = String((other?.labels || ['entity'])[0]).toLowerCase();
      return `${r.start === item.id ? '→' : '←'}${r.type || '关系'} ${topologyLabelZh(otherKey)}`;
    });
    detail.textContent = `${topologyLabelZh(key)} · 内容节点 ${item.id} · 局部引用 ${item.reference_count || 0} 次 · 属性 ${JSON.stringify(item.props || {})}${adjacent.length ? ` · 相邻（最多12条）：${adjacent.join('；')}` : ''}`;
  }));
}

function bindGraphViewport(root, onDrag, onStart) {
  const svg = root.querySelector('[data-graph-canvas]');
  const initial = svg.getAttribute('viewBox').split(' ').map(Number);
  let view = [...initial], pointer = null;
  const apply = () => svg.setAttribute('viewBox', view.join(' '));
  function zoom(factor) {
    const width = Math.min(initial[2] * 3, Math.max(initial[2] / 20, view[2] * factor));
    const height = width * initial[3] / initial[2];
    view = [view[0] + (view[2] - width) / 2, view[1] + (view[3] - height) / 2, width, height];
    apply();
  }
  root.querySelectorAll('[data-graph-zoom]').forEach(button => button.addEventListener('click', () => {
    const action = button.dataset.graphZoom;
    if (action === 'fit') { view = [...initial]; apply(); }
    else zoom(action === 'in' ? .75 : 1.333333);
  }));
  svg.addEventListener('wheel', event => { event.preventDefault(); zoom(event.deltaY < 0 ? .85 : 1.176471); }, { passive: false });
  svg.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    onStart();
    pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, view: [...view] };
    // Keep node clicks targeted at the node; only capture once a real drag begins.
  });
  svg.addEventListener('pointermove', event => {
    if (!pointer || event.pointerId !== pointer.id) return;
    const dx = event.clientX - pointer.x, dy = event.clientY - pointer.y;
    if (Math.abs(dx) + Math.abs(dy) < 4) return;
    onDrag();
    svg.setPointerCapture(event.pointerId);
    const rect = svg.getBoundingClientRect();
    const units = Math.max(pointer.view[2] / rect.width, pointer.view[3] / rect.height);
    view = [pointer.view[0] - dx * units, pointer.view[1] - dy * units, pointer.view[2], pointer.view[3]];
    apply();
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) svg.addEventListener(name, () => { pointer = null; });
}
