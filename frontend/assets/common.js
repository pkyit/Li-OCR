/* Li-OCR R1.0 前端公共库：无构建、无第三方依赖 */

const TOKEN_KEY = 'liocr_token';

function getToken() { return localStorage.getItem(TOKEN_KEY) || ''; }
function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY);
}

async function api(path, options = {}, retried = false) {
  const opts = Object.assign({ headers: {} }, options);
  const token = getToken();
  if (token) opts.headers['X-Api-Token'] = token;
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401 && !retried) {
    const t = window.prompt('该服务设置了访问口令，请输入 X-Api-Token：', '');
    if (t) { setToken(t.trim()); return api(path, options, true); }
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || res.statusText;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function toast(msg, isError = false) {
  let el = document.querySelector('.toast');
  if (!el) { el = document.createElement('div'); el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.classList.toggle('error', isError);
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), isError ? 5000 : 2600);
}

const STATUS_LABEL = {
  RECEIVED: '已接收', QUEUED: '排队中', PROCESSING: '处理中', NEED_REVIEW: '待复核',
  AUTO_PASSED: '自动通过', REVIEWED: '已复核', REJECTED: '已驳回', FAILED: '失败',
};

const STATUS_CLASS = {
  RECEIVED: '', QUEUED: '', PROCESSING: 'brand', NEED_REVIEW: 'warn',
  AUTO_PASSED: 'ok', REVIEWED: 'ok', REJECTED: 'danger', FAILED: 'danger',
};

const FLAG_LABEL = {
  LOW_CONF: '低置信', UNPARSEABLE: '无法解析', FORMAT: '格式异常', CHECKSUM_FAIL: '校验位错',
  DATE_OUT_OF_RANGE: '日期异常', MISSING: '缺失', CROSS_ENGINE_DIFF: '双引擎不一致',
  SUM_MISMATCH: '勾稽不平', LEXICON_FIXED: '词表纠错', SINGLE_ENGINE: '单引擎',
  INFERRED: '推断', VL_FALLBACK: 'VL兜底',
};

const KIND_LABEL = {
  TEXT_PDF: '文本层', SCAN: '扫描', PHOTO: '拍摄', SCAN_MIXED: '混合',
  HANDWRITE_LOW_QUALITY: '手写/低质量（已 VL 兜底）', LOW_QUALITY: '低质量',
};

const REASONS = [
  ['CHAR_ERROR', '字符识别错误'],
  ['COLUMN_SHIFT', '表格列错位'],
  ['UNIT_OR_CURRENCY', '单位/币种错误'],
  ['DATE_FORMAT', '日期格式错误'],
  ['MISSING_VALUE', '字段缺失补录'],
  ['WRONG_EXTRACT', '抽错字段'],
  ['OTHER', '其他'],
];

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function statusChip(status) {
  return `<span class="chip ${STATUS_CLASS[status] || ''}">${STATUS_LABEL[status] || status}</span>`;
}

function flagChips(flags) {
  if (!flags || !flags.length) return '';
  return flags.map(f => `<span class="chip tiny ${f === 'MISSING' || f === 'CROSS_ENGINE_DIFF' || f === 'SUM_MISMATCH' ? 'danger' : f === 'LEXICON_FIXED' ? 'brand' : 'warn'}">${esc(FLAG_LABEL[f] || f)}</span>`).join('');
}

function fmtTime(s) {
  if (!s) return '';
  return String(s).replace('T', ' ').slice(0, 19);
}

function fmtSize(bytes) {
  if (!bytes) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0, v = bytes;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function renderTopbar(active) {
  const items = [['index.html', '单据'], ['issues.html', '可疑问题'], ['stats.html', '统计与飞轮']];
  const nav = items.map(([href, text]) =>
    `<a href="${href}" class="${active === href ? 'active' : ''}">${text}</a>`).join('');
  return `<header class="topbar">
    <div class="brand">Li-OCR<small>R1.0 可行性验证</small></div>
    <nav>${nav}</nav>
    <div class="spacer"></div>
    <div class="hint" id="engineHint">引擎信息加载中…</div>
  </header>`;
}

async function loadEngineHint() {
  const el = document.getElementById('engineHint');
  if (!el) return;
  try {
    const c = await api('/api/config');
    const e = c.engines || {};
    const cross = e.rapid && e.rapid.enabled ? '双引擎' : '单引擎';
    el.textContent = `${c.config.device} · PP-OCRv5-${c.config.model} · ${cross}`;
  } catch (e) {
    el.textContent = '';
  }
}

function pollWhile(cond, fn, ms = 2000) {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try { await fn(); } catch (e) { /* 轮询失败静默重试 */ }
    if (!stopped && cond()) setTimeout(tick, ms);
  };
  tick();
  return () => { stopped = true; };
}
