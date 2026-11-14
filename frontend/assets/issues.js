document.getElementById('topbar').innerHTML = renderTopbar('issues.html');
loadEngineHint();

const $ = id => document.getElementById(id);

const REASON_HINT = {
  MISSING: '必填字段没有抽取到：检查模板/别名，或该单据确实缺少该信息',
  LOW_CONF: '模型自身不确定：优先人工确认，不要直接当标签',
  CROSS_ENGINE_DIFF: '两套引擎给出不同结果：属于最高优先级复核项',
  UNPARSEABLE: '格式无法解析：多半是抽到了相邻文本，需要修规则或改列归属',
  FORMAT: '数值/日期格式不规范：常见于千分位、位数不足',
  CHECKSUM_FAIL: '统一社会信用代码校验位不通过：极可能是识别串字',
  DATE_OUT_OF_RANGE: '日期明显不合理：多为年份识别错误',
  SUM_MISMATCH: '金额勾稽不平：合计 ≠ 不含税 + 税额',
  LEXICON_FIXED: '词表自动纠错：确认无误即可',
};

async function load() {
  try {
    const data = await api('/api/issues');
    $('issueSummary').textContent = `共 ${data.total} 处可疑，归为 ${data.items.length} 类`;
    if (!data.items.length) {
      $('issues').innerHTML = '<div class="empty">没有可疑字段，先上传并识别一批单据</div>';
      return;
    }
    $('issues').innerHTML = `<table><thead><tr>
        <th style="width:190px">字段</th><th style="width:150px">原因</th><th style="width:70px">数量</th>
        <th>样例单据</th></tr></thead><tbody>
      ${data.items.map(g => `<tr>
        <td><strong>${esc(g.label)}</strong><div class="muted mono" style="font-size:11px">${esc(g.key)}</div></td>
        <td><span class="chip warn">${esc(g.reason_text)}</span>
            <div class="muted" style="font-size:11px;margin-top:4px">${esc(REASON_HINT[g.reason] || '')}</div></td>
        <td class="num">${g.count}</td>
        <td>${g.docs.map(d => `<div class="issue-doc">
            <a href="review.html?id=${d.doc_id}">${esc(d.filename)}</a>
            <span class="muted">${esc(d.doc_type || '')} 第${d.page_no}页</span>
            <span class="mono">${esc(d.value || '（空）')}</span>
            ${d.confidence ? `<span class="muted">${Number(d.confidence).toFixed(2)}</span>` : ''}
          </div>`).join('')}</td>
      </tr>`).join('')}
      </tbody></table>`;
  } catch (e) {
    toast(e.message, true);
  }
}

$('refreshBtn').onclick = load;
load();
