document.getElementById('topbar').innerHTML = renderTopbar('stats.html');
loadEngineHint();

const $ = id => document.getElementById(id);

async function load() {
  try {
    const [s, ds] = await Promise.all([api('/api/stats'), api('/api/datasets')]);
    renderMetrics(s);
    renderPerKey(s.per_key);
    renderFlags(s.flags);
    renderSpark(s.daily);
    renderDatasets(ds.items);
  } catch (e) {
    toast(e.message, true);
  }
}

function renderMetrics(s) {
  const d = s.docs, f = s.fields;
  $('metrics').innerHTML = `
    <div class="card metric"><div class="label">单据 / 页面</div>
      <div class="value">${d.total} / ${s.pages.total}</div>
      <div class="foot">设备 ${esc(s.config.device)} · PP-OCRv5-${esc(s.config.model)}</div></div>
    <div class="card metric"><div class="label">文档自动通过率</div>
      <div class="value">${d.auto_pass_rate}%</div>
      <div class="foot">待复核 ${d.need_review} · 已复核 ${d.reviewed}</div></div>
    <div class="card metric"><div class="label">字段可疑率</div>
      <div class="value">${f.suspect_rate}%</div>
      <div class="foot">可疑 ${f.suspect} · 缺失 ${f.missing} · 共 ${f.total}</div></div>
    <div class="card metric"><div class="label">人工纠正率</div>
      <div class="value">${f.correction_rate}%</div>
      <div class="foot">改写字段 ${f.corrected_fields} · 事件 ${f.correction_events}</div></div>`;
}

function renderPerKey(items) {
  if (!items || !items.length) {
    $('perKey').innerHTML = '<div class="empty">暂无字段数据</div>';
    return;
  }
  $('perKey').innerHTML = `<table><thead><tr>
      <th>字段</th><th>总数</th><th>可疑/缺失</th><th>可疑率</th>
      <th>人工改写</th><th>纠正率</th><th>平均置信</th></tr></thead><tbody>
    ${items.map(r => {
      const suspectRate = r.total ? (r.suspect / r.total * 100) : 0;
      return `<tr>
        <td><strong>${esc(r.label)}</strong><div class="muted mono" style="font-size:11px">${esc(r.key)}</div></td>
        <td class="num">${r.total}</td>
        <td class="num">${r.suspect}</td>
        <td class="num">${suspectRate.toFixed(1)}%</td>
        <td class="num">${r.corrected}</td>
        <td class="num">${r.correction_rate}%</td>
        <td class="num">${r.avg_confidence.toFixed(3)}</td>
      </tr>`;
    }).join('')}</tbody></table>`;
}

function renderFlags(flags) {
  if (!flags || !flags.length) {
    $('flagBars').innerHTML = '<div class="empty">暂无可疑信号</div>';
    return;
  }
  const max = Math.max(...flags.map(f => f.count), 1);
  $('flagBars').innerHTML = flags.map(f => `
    <div class="bar-row">
      <span>${esc(f.text)}</span>
      <div class="bar-track ${['CROSS_ENGINE_DIFF', 'SUM_MISMATCH', 'MISSING'].includes(f.code) ? 'danger' : 'warn'}">
        <i style="width:${f.count / max * 100}%"></i></div>
      <span class="num right">${f.count}</span>
    </div>`).join('');
}

function renderSpark(daily) {
  if (!daily || !daily.length) {
    $('spark').innerHTML = '<div class="empty">暂无数据</div>';
    return;
  }
  const max = Math.max(...daily.map(d => Math.max(d.docs, d.corrections)), 1);
  $('spark').innerHTML = daily.map(d => `
    <div class="col" title="${d.date}：单据 ${d.docs}，纠正 ${d.corrections}">
      <i style="height:${d.docs / max * 60}px"></i>
      <b style="height:${d.corrections / max * 60}px"></b>
      <span>${d.date.slice(5)}</span>
    </div>`).join('');
}

function renderDatasets(items) {
  if (!items || !items.length) {
    $('datasets').innerHTML = '<div class="empty">还没有导出过数据集</div>';
    return;
  }
  $('datasets').innerHTML = `<table><thead><tr>
      <th>版本</th><th>单据</th><th>字段</th><th>裁剪图</th><th>路径</th><th>时间</th><th></th>
    </tr></thead><tbody>${items.map(d => `<tr>
      <td class="mono">${esc(d.version)}</td>
      <td class="num">${d.docs}</td><td class="num">${d.fields}</td><td class="num">${d.crops}</td>
      <td class="mono muted" style="font-size:12px">${esc(d.path)}</td>
      <td class="num muted">${fmtTime(d.created_at)}</td>
      <td class="right"><button class="btn sm danger" data-del="${esc(d.version)}">删除</button></td>
    </tr>`).join('')}</tbody></table>`;
  $('datasets').querySelectorAll('[data-del]').forEach(b => {
    b.onclick = async () => {
      if (!confirm('删除该数据集目录？')) return;
      await api(`/api/datasets/${b.dataset.del}`, { method: 'DELETE' });
      toast('已删除');
      load();
    };
  });
}

$('exportBtn').onclick = async () => {
  $('exportBtn').disabled = true;
  try {
    const res = await api('/api/datasets/export', { method: 'POST', body: { note: '手动导出' } });
    if (res.message) toast(res.message, true);
    else toast(`已导出 ${res.version}：${res.docs} 单 / ${res.fields} 字段 / ${res.crops} 张裁剪图`);
    load();
  } catch (e) {
    toast(e.message, true);
  } finally {
    $('exportBtn').disabled = false;
  }
};

load();
