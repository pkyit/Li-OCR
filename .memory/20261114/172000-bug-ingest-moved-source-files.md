# bug：本地批量导入把源文件搬走了

- 时间：2026-11-14 17:20 (+08:00)
- 类型：bug
- 影响范围：`backend/app/storage.py:save_upload`、`tools/ingest_local.py`
- 关联提交：R1.0 初始实现内修复

## 现象

`python -m tools.ingest_local samples --sync` 跑完后，`samples/` 目录几乎空了；
第二次运行时出现两个不同文件打印同一文件名，14 份只入库 13 份。

## 根因

`save_upload` 是为 HTTP 上传写的：上传落到临时目录，再 `shutil.move` 归档，语义正确。
本地批量导入直接传用户目录里的原文件，于是被"移动"进了 `data/raw/`。
后续 `collect()` 收集到的文件已不存在，`ingest()` 读到的是同一份残留文件或直接跳过。

## 修复

给 `save_upload` 加 `move: bool = True` 参数；本地导入用 `move=False`（`copy2`）。
见 `storage.py` 与 `tools/ingest_local.py`。

## 回归验证

- 重新生成样例后导入，`samples/` 文件数保持不变；
- 18 份文件全部入库，无重复打印。

## 教训

任何"归档/整理"类工具，默认语义必须是无损的；只有明确的上传临时文件场景才允许移动。
