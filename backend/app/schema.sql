-- Li-OCR R1.0 SQLite schema
-- 设计要点：原始层不可变；字段与校正 append-only；一切可重放

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 单据主表：一个上传文件一条
CREATE TABLE IF NOT EXISTS document (
    id          TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    filename    TEXT NOT NULL,
    ext         TEXT,
    size        INTEGER DEFAULT 0,
    raw_path    TEXT NOT NULL,
    page_count  INTEGER DEFAULT 0,
    doc_type    TEXT,
    status      TEXT NOT NULL DEFAULT 'RECEIVED',
    first_decision TEXT,          -- 首次处理结论（AUTO_PASSED/NEED_REVIEW），用于统计真实自动通过率
    stage       TEXT,
    progress    REAL DEFAULT 0,
    error       TEXT,
    run_id      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_document_sha ON document(sha256);
CREATE INDEX IF NOT EXISTS ix_document_status ON document(status, updated_at DESC);

-- 页面：渲染图 + 识别引擎 + 耗时
CREATE TABLE IF NOT EXISTS page (
    doc_id          TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    page_no         INTEGER NOT NULL,
    image_path      TEXT,
    width           INTEGER DEFAULT 0,
    height          INTEGER DEFAULT 0,
    page_kind       TEXT,
    text_layer_chars INTEGER DEFAULT 0,
    engine          TEXT,
    elapsed_ms      INTEGER DEFAULT 0,
    ocr_json        TEXT,
    PRIMARY KEY (doc_id, page_no)
);

-- 字段：可复核的最小单元
CREATE TABLE IF NOT EXISTS field (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    page_no     INTEGER DEFAULT 1,
    group_name  TEXT DEFAULT '关键字段',
    key         TEXT NOT NULL,
    label       TEXT NOT NULL,
    value_raw   TEXT,
    value_norm  TEXT,
    confidence  REAL,
    bbox        TEXT,
    source      TEXT,
    status      TEXT DEFAULT 'OK',      -- OK | SUSPECT | MISSING
    flags       TEXT,                    -- JSON 数组，可疑原因
    required    INTEGER DEFAULT 0,
    candidate   TEXT,                    -- 交叉引擎给出的另一版本，JSON
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_field_doc ON field(doc_id, id);
CREATE INDEX IF NOT EXISTS ix_field_status ON field(status, key);
CREATE INDEX IF NOT EXISTS ix_field_key ON field(key);

-- 校正事件：append-only，永不覆盖
CREATE TABLE IF NOT EXISTS correction (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      TEXT NOT NULL,
    field_id    INTEGER,
    page_no     INTEGER,
    key         TEXT,
    label       TEXT,
    value_before TEXT,
    value_after  TEXT,
    reason      TEXT,
    reviewer    TEXT,
    model_version TEXT,
    bbox        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_correction_doc ON correction(doc_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_correction_key ON correction(key, created_at DESC);

-- 复核结论：每单一条最新（历史在 correction / review_log）
CREATE TABLE IF NOT EXISTS review (
    doc_id      TEXT PRIMARY KEY,
    reviewer    TEXT,
    action      TEXT,          -- CONFIRMED | REJECTED
    note        TEXT,
    created_at  TEXT NOT NULL
);

-- 处理运行：版本可追溯，支持重放
CREATE TABLE IF NOT EXISTS run (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    versions    TEXT,
    status      TEXT,
    started_at  TEXT,
    finished_at TEXT,
    timings     TEXT,
    warnings    TEXT
);
CREATE INDEX IF NOT EXISTS ix_run_doc ON run(doc_id, started_at DESC);

-- 数据集导出记录
CREATE TABLE IF NOT EXISTS dataset_export (
    id          TEXT PRIMARY KEY,
    version     TEXT NOT NULL,
    path        TEXT NOT NULL,
    docs        INTEGER DEFAULT 0,
    fields      INTEGER DEFAULT 0,
    crops       INTEGER DEFAULT 0,
    note        TEXT,
    created_at  TEXT NOT NULL
);

-- 领域词表：校正沉淀后自动扩充，用于提升后续抽取/校验
CREATE TABLE IF NOT EXISTS lexicon (
    term        TEXT NOT NULL,
    kind        TEXT NOT NULL,      -- seller | buyer | currency | generic
    weight      REAL DEFAULT 1.0,
    source      TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (term, kind)
);
