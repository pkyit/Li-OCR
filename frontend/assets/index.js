document.getElementById('topbar').innerHTML = renderTopbar('index.html');
loadEngineHint();

const state = { items: [], counts: {}, stats: null, uploading: false };
const $ = id => document.getElementById(id);

async function refresh() {
  try {
    const [list, stats] = await Promise.all([
      api('/api/documents?' + new URLSearchParams({ status: $('statusFilter').value, limit: '200' })),
      api('/api/stats'),
    ]);
    state.items = list.items;
    state.counts = list.counts || {};
    state.stats = stats;
    renderMetrics();
    renderTable();
  } catch (e) {
    toast(e.message, true);
  }
}

function renderMetrics() {
  const s = state.stats;
  if (!s) return;
  const d = s.docs, f = s.fields;
  $('metrics').innerHTML = `
    <div class="card metric"><div class="label">单据总数</div>
      <div class="value">${d.total}</div>
      <div class="foot">共 ${s.pages.total} 页 · 平均识别 ${s.pages.avg_ocr_ms} ms/页</div></div>
    <div class="card metric"><div class="label">待复核</div>
      <div class="value">${d.need_review}</div>
      <div class="foot">已复核 ${d.reviewed} · 失败 ${d.by_status.FAILED || 0}</div></div>
    <div class="card metric"><div class="label">自动通过率</div>
      <div class="value">${d.auto_pass_rate}%</div>
      <div class="foot">无任何可疑字段的单据占比</div></div>
    <div class="card metric"><div class="label">可疑字段</div>
      <div class="value">${f.suspect + f.missing}</div>
      <div class="foot">字段总数 ${f.total} · 已人工改写 ${f.corrected_fields}</div></div>`;
}

function renderTable() {
  const items = state.items;
  $('docSummary').textContent = items.length ? `共 ${items.length} 条` : '';
  if (!items.length) {
    $('docTable').innerHTML = '<div class="empty">还没有单据，先上传几份试试</div>';
    return;
  }
  const rows = items.map(d => {
    const pct = Math.round((d.progress || 0) * 100);
    const running = d.status === 'QUEUED' || d.status === 'PROCESSING' || d.status === 'RECEIVED';
    const bar = running
      ? `<div class="progress"><i style="width:${pct}%"></i></div><div class="muted" style="font-size:11px;margin-top:4px">${esc(d.stage || '')}</div>`
      : '';
    const suspect = d.suspect_fields
      ? `<span class="chip warn">${d.suspect_fields} 个可疑</span>` : '<span class="muted">—</span>';
    return `<tr>
      <td><a href="review.html?id=${d.id}">${esc(d.filename)}</a>
          <div class="muted" style="font-size:11px">${d.id}</div></td>
      <td>${esc(d.doc_type || '—')}</td>
      <td>${statusChip(d.status)}${bar}</td>
      <td class="num">${d.page_count || 0}</td>
      <td>${suspect}</td>
      <td class="num muted">${fmtTime(d.updated_at)}</td>
      <td class="right nowrap">
        <a class="btn sm" href="review.html?id=${d.id}">${d.status === 'REVIEWED' ? '查看' : '复核'}</a>
        <button class="btn sm" data-reprocess="${d.id}">重跑</button>
        <button class="btn sm danger" data-del="${d.id}">删除</button>
      </td>
    </tr>`;
  }).join('');
  $('docTable').innerHTML = `<table>
    <thead><tr><th>文件</th><th>类型</th><th>状态</th><th>页数</th><th>可疑</th><th>更新时间</th><th></th></tr></thead>
    <tbody>${rows}</tbody></table>`;

  $('docTable').querySelectorAll('[data-reprocess]').forEach(btn => {
    btn.onclick = async () => {
      btn.disabled = true;
      try { await api(`/api/documents/${btn.dataset.reprocess}/reprocess`, { method: 'POST' }); toast('已重新排队'); refresh(); }
      catch (e) { toast(e.message, true); btn.disabled = false; }
    };
  });
  $('docTable').querySelectorAll('[data-del]').forEach(btn => {
    btn.onclick = async () => {
      if (!confirm('删除该单据的索引与页面图？（原始文件保留在不可变目录）')) return;
      try { await api(`/api/documents/${btn.dataset.del}`, { method: 'DELETE' }); toast('已删除'); refresh(); }
      catch (e) { toast(e.message, true); }
    };
  });
}

async function uploadFiles(files) {
  if (!files.length || state.uploading) return;
  state.uploading = true;
  const fd = new FormData();
  [...files].forEach(f => fd.append('files', f));
  $('uploadResult').innerHTML = `<div class="muted">正在上传 ${files.length} 份…</div>`;
  try {
    const res = await api('/api/documents', { method: 'POST', body: fd });
    const lines = res.items.map(it => it.error
      ? `<div class="chip danger">${esc(it.filename)}：${esc(it.error)}</div>`
      : `<div class="chip ok">${esc(it.filename)}${it.duplicate ? '（已存在，自动跳过）' : ' 已入队'}</div>`);
    $('uploadResult').innerHTML = `<div class="flex" style="flex-wrap:wrap">${lines.join('')}</div>`;
    toast(`已提交 ${res.count} 份`);
    refresh();
  } catch (e) {
    toast(e.message, true);
    $('uploadResult').innerHTML = '';
  } finally {
    state.uploading = false;
  }
}

const dz = $('dropzone'), fi = $('fileInput');
dz.onclick = () => fi.click();
fi.onchange = () => { uploadFiles(fi.files); fi.value = ''; };
['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('dragging'); }));
['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('dragging'); }));
dz.addEventListener('drop', e => uploadFiles(e.dataTransfer.files));
$('statusFilter').onchange = refresh;
$('refreshBtn').onclick = refresh;

refresh();
pollWhile(() => state.items.some(d => ['QUEUED', 'PROCESSING', 'RECEIVED'].includes(d.status)), refresh, 2500);
