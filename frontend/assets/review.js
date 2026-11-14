document.getElementById('topbar').innerHTML = renderTopbar('index.html');
loadEngineHint();

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const docId = params.get('id');

const state = {
  doc: null, pages: [], fields: [], corrections: [], currentPage: 1,
  activeFieldId: null, dirty: new Set(), reasonText: {},
};

$('reason').innerHTML = REASONS.map(([v, t]) => `<option value="${v}">${t}</option>`).join('');
$('reviewer').value = localStorage.getItem('liocr_reviewer') || '';
$('reviewer').onchange = () => localStorage.setItem('liocr_reviewer', $('reviewer').value);

if (!docId) {
  $('fields').innerHTML = '<div class="empty">缺少单据 id</div>';
}

async function load() {
  const data = await api(`/api/documents/${docId}`);
  state.doc = data.doc;
  state.pages = data.pages;
  state.fields = data.fields;
  state.corrections = data.corrections || [];
  state.reasonText = data.reason_text || {};
  state.nextReviewId = data.next_review_id;
  state.dirty.clear();
  renderHeader();
  renderFields();
  renderPage(state.pages[0] ? state.pages[0].page_no : 1);
}

function renderHeader() {
  const d = state.doc;
  $('docName').textContent = d.filename;
  $('docStatus').innerHTML = statusChip(d.status);
  const lowQ = state.pages.some(p => String(p.page_kind || '').includes('LOW_QUALITY'));
  $('docMeta').innerHTML = `${esc(d.doc_type || '未分类')} · ${d.page_count} 页 · 更新 ${fmtTime(d.updated_at)}`
    + (lowQ ? ' <span class="chip warn">含手写/低质量页，已启用 VL 兜底，请逐字段确认</span>' : '');
  $('pageTabs').innerHTML = state.pages.map(p =>
    `<button data-page="${p.page_no}">第${p.page_no}页 · ${KIND_LABEL[p.page_kind] || p.page_kind || '—'}</button>`).join('');
  $('pageTabs').querySelectorAll('button').forEach(b => b.onclick = () => renderPage(Number(b.dataset.page)));
  $('reprocessBtn').onclick = async () => {
    if (!confirm('重新识别该单据？现有字段会被覆盖（校正历史保留）')) return;
    await api(`/api/documents/${docId}/reprocess`, { method: 'POST' });
    toast('已重新排队，稍后刷新');
  };
  if (d.error) toast('上次处理失败：' + d.error, true);
}

function renderPage(pageNo) {
  state.currentPage = pageNo;
  const page = state.pages.find(p => p.page_no === pageNo);
  if (!page) return;
  $('pageImg').src = `/api/documents/${docId}/pages/${pageNo}/image`;
  $('pageImg').onload = () => drawOverlay();
  $('pageTabs').querySelectorAll('button').forEach(b =>
    b.classList.toggle('active', Number(b.dataset.page) === pageNo));
  drawOverlay();
}

function drawOverlay() {
  const page = state.pages.find(p => p.page_no === state.currentPage);
  if (!page) return;
  const { width: W, height: H } = page;
  const boxes = state.fields.filter(f => f.bbox && f.page_no === page.page_no).map(f => {
    const [x0, y0, x1, y1] = f.bbox;
    const cls = ['bbox'];
    if (f.flags.includes('CROSS_ENGINE_DIFF')) cls.push('cross');
    else if (f.status !== 'OK') cls.push('suspect');
    if (f.id === state.activeFieldId) cls.push('active');
    return `<div class="${cls.join(' ')}" data-fid="${f.id}" title="${esc(f.label)}: ${esc(f.value_raw || '')}"
      style="left:${x0 / W * 100}%;top:${y0 / H * 100}%;width:${(x1 - x0) / W * 100}%;height:${(y1 - y0) / H * 100}%"></div>`;
  }).join('');
  $('overlay').innerHTML = boxes;
  $('overlay').querySelectorAll('.bbox').forEach(el => {
    el.onclick = () => focusField(Number(el.dataset.fid), false);
  });
}

function renderFields() {
  const groups = {};
  state.fields.forEach(f => { (groups[f.group] = groups[f.group] || []).push(f); });
  const order = ['关键字段', '附加字段'];
  const keys = Object.keys(groups).sort((a, b) => {
    const ia = order.indexOf(a), ib = order.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });

  const suspectCount = state.fields.filter(f => f.status !== 'OK').length;
  $('fieldSummary').textContent = `共 ${state.fields.length} 个字段 · ${suspectCount} 个待确认`;

  let html = '';
  if (state.corrections.length) {
    html += `<details style="padding:10px 14px;border-bottom:1px solid var(--line)">
      <summary class="muted" style="cursor:pointer;font-size:12px">历史修改 ${state.corrections.length} 条</summary>
      <div style="margin-top:8px">${state.corrections.map(c =>
        `<div class="issue-doc"><span class="mono">${esc(c.key)}</span>
         <span class="muted">${esc(c.value_before || '空')} → </span><strong>${esc(c.value_after)}</strong>
         <span class="muted" style="font-size:11px">${esc(c.reviewer || '')} ${fmtTime(c.created_at)}</span></div>`).join('')}</div>
    </details>`;
  }
  if (!state.fields.length) {
    html += '<div class="empty">暂无字段（可能仍在处理中）</div>';
  }
  keys.forEach(g => {
    html += `<div class="field-group-title">${esc(g)} · ${groups[g].length}</div>`;
    groups[g].forEach(f => {
      const cross = f.candidate && f.candidate.cross;
      const lex = f.candidate && f.candidate.lexicon;
      html += `<div class="field-row ${f.status === 'MISSING' ? 'missing' : ''}" data-fid="${f.id}" data-page="${f.page_no}">
        <div class="head">
          <span class="name">${esc(f.label)}</span>
          <span class="key mono">${esc(f.key)}</span>
          ${f.required ? '<span class="chip tiny">必填</span>' : ''}
          <span class="conf">${f.confidence ? '置信 ' + f.confidence.toFixed(2) : ''} · ${esc(f.source || '')}</span>
        </div>
        <input type="text" value="${esc(f.value_raw || '')}" data-input="${f.id}" placeholder="${f.status === 'MISSING' ? '未识别到，请补录' : ''}">
        <div class="flags">${flagChips(f.flags)}</div>
        ${cross ? `<div class="cross">CPU 旁路给出：<strong>${esc(cross.value)}</strong>
            <button class="btn sm" data-adopt="${f.id}">采用</button></div>` : ''}
        ${lex ? `<div class="cross">词表纠错：${esc(lex.before)} → <strong>${esc(lex.lexicon)}</strong></div>` : ''}
      </div>`;
    });
  });
  $('fields').innerHTML = html;

  $('fields').querySelectorAll('[data-input]').forEach(inp => {
    inp.oninput = () => {
      const fid = Number(inp.dataset.input);
      const f = state.fields.find(x => x.id === fid);
      if (inp.value !== (f.value_raw || '')) state.dirty.add(fid); else state.dirty.delete(fid);
      inp.closest('.field-row').classList.toggle('dirty', state.dirty.has(fid));
    };
    inp.onfocus = () => focusField(Number(inp.dataset.input), true);
    inp.onkeydown = e => {
      if (e.key === 'Enter') { e.preventDefault(); moveFocus(inp, 1); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); moveFocus(inp, 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); moveFocus(inp, -1); }
    };
  });
  $('fields').querySelectorAll('[data-adopt]').forEach(btn => {
    btn.onclick = () => {
      const fid = Number(btn.dataset.adopt);
      const f = state.fields.find(x => x.id === fid);
      const val = f.candidate.cross.value;
      const inp = $('fields').querySelector(`[data-input="${fid}"]`);
      inp.value = val;
      state.dirty.add(fid);
      toast('已填入旁路结果，确认后才会落库');
    };
  });
}

function moveFocus(inp, delta) {
  const all = [...$('fields').querySelectorAll('[data-input]')];
  const i = all.indexOf(inp);
  const next = all[i + delta];
  if (next) { next.focus(); next.select(); }
}

function focusField(fid, scrollToRow) {
  state.activeFieldId = fid;
  const f = state.fields.find(x => x.id === fid);
  if (!f) return;
  if (f.page_no !== state.currentPage) renderPage(f.page_no);
  else drawOverlay();
  $('fields').querySelectorAll('.field-row').forEach(r =>
    r.classList.toggle('active', Number(r.dataset.fid) === fid));
  if (scrollToRow) {
    const row = $('fields').querySelector(`.field-row[data-fid="${fid}"]`);
    if (row) row.scrollIntoView({ block: 'nearest' });
  } else {
    const inp = $('fields').querySelector(`[data-input="${fid}"]`);
    if (inp) { const row = inp.closest('.field-row'); if (row) row.scrollIntoView({ block: 'center' }); }
  }
}

function collectChanges() {
  const reason = $('reason').value;
  return state.fields
    .filter(f => state.dirty.has(f.id))
    .map(f => {
      const inp = $('fields').querySelector(`[data-input="${f.id}"]`);
      return { field_id: f.id, value: inp ? inp.value.trim() : '', reason };
    });
}

async function submit(action) {
  const reviewer = $('reviewer').value.trim();
  if (!reviewer) { toast('请先填写复核人', true); $('reviewer').focus(); return; }
  localStorage.setItem('liocr_reviewer', reviewer);
  const changes = collectChanges();
  if (action === 'CONFIRMED' && changes.length === 0 &&
      state.fields.some(f => f.status === 'MISSING')) {
    if (!confirm('仍有缺失字段未补录，仍要确认本单？')) return;
  }
  const btns = ['saveBtn', 'confirmBtn', 'rejectBtn'].map(i => $(i));
  btns.forEach(b => b.disabled = true);
  try {
    const res = await api(`/api/documents/${docId}/review`, {
      method: 'POST',
      body: { action, reviewer, note: '', changes },
    });
    toast(`已提交：修改 ${res.applied} 个字段`);
    if (action === 'CONFIRMED' && state.nextReviewId) {
      location.href = `review.html?id=${state.nextReviewId}`;
    } else if (action !== 'SAVE') {
      location.href = 'index.html';
    } else {
      await load();
    }
  } catch (e) {
    toast(e.message, true);
    btns.forEach(b => b.disabled = false);
  }
}

$('confirmBtn').onclick = () => submit('CONFIRMED');
$('rejectBtn').onclick = () => {
  if (confirm('驳回该单据？')) submit('REJECTED');
};
$('saveBtn').onclick = () => submit('SAVE');

document.addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); submit('CONFIRMED'); }
});

if (docId) load().catch(e => toast(e.message, true));
