# 文档导入：校验、归一化、MinerU 与切分

`app/ingestion` 是与数据库无关的实现。原文件、PDF、解析结构、资产、质量告警和 chunk 的持久化及状态推进由 Worker 负责。本模块不发布文档版本，也不调用 Embedding、Milvus 或 Neo4j。

## 调用契约

```python
from app.ingestion import (
    FileLimits, NormalizeConfig, ChunkConfig, HTTPMalwareScanner,
    validate_file, scan_file, normalize_file, MinerUClient,
    validate_parse_quality, create_chunks,
)

validated = validate_file(data, filename, mime_type, FileLimits())
await scan_file(data, HTTPMalwareScanner(scanner_url))
# 此处持久化原始 Blob；以下同步转换仅在 Worker 中运行。
normalized = normalize_file(data, validated, NormalizeConfig())
# 持久化 normalized.pdf、两个 SHA256、转换器标识和耗时。
parsed = await MinerUClient(mineru_url).parse(normalized.pdf, str(version_id))
# 原始 parsed.raw、规范化 blocks 和资产均需要持久化。
quality = validate_parse_quality(parsed, normalized.page_count)
if quality.status == "BLOCK":
    raise ValueError("PARSE_QUALITY_BLOCKED")
chunks = create_chunks(parsed.blocks, str(document_id), str(version_id), filename, ChunkConfig())
```

公开数据结构为 dataclass；可以使用 `dataclasses.asdict()` 序列化。所有页面编号从 1 开始。`IngestionError` 包含 `error_code`、`retryable`，错误文本不包含转换器堆栈或外部服务响应正文。

- `ValidatedFile`：`sha256, kind, mime_type, page_count, filename`。
- `NormalizedDocument`：`pdf, original_sha256, normalized_sha256, page_count, converter_version, duration_ms`。
- `ParsedBlock`：任务书中的内部块契约，包括 bbox、标题等级、表格 HTML、公式和资产 ID。
- `ParseResult`：`blocks, raw, page_count, parser_version`。
- `QualityReport`：`status, warnings, metrics`；WARN 应展示在文档页面，BLOCK 必须阻断 Embedding 和图谱。
- `Chunk`：正文、注入标题的 embedding_text、章节路径、页码、来源块 ID、稳定 ID、序号、配置版本；表格额外保存 `table_id, row_start, row_end, markdown, table_html`。

## 校验及扫描

默认最大 50 MiB、500 页；图片最大 20 MiB、最长边 16000、总像素 8000 万。检查扩展名/MIME/文件签名一致性、PDF 加密及结构、图像解码和每一帧尺寸。文本只接收 UTF-8；二进制控制字符和已知二进制签名被拒绝。SHA256 始终对原始字节计算。

Office 仅接收 DOCX/PPTX/XLSX；验证对应 ZIP 部件、XML、CRC、条目数量、展开大小和压缩比，拒绝路径穿越、重复条目、加密内容、VBA、ActiveX、嵌入对象、DOCTYPE/ENTITY 和外部关系。保守策略也会拒绝包含外部超链接关系的 Office 文件；请先另存为不含外部关系的文档。

扫描器默认失败关闭。`HTTPMalwareScanner` 将字节以 `application/octet-stream` POST 到受信任配置地址，要求 JSON 精确包含 `{"status":"clean"}`；超时或异常不能当作扫描成功。配置地址不能来自上传用户。`DevelopmentBypassScanner` 仅用于显式启用的开发环境，生产环境不能启用。

## 归一化

- PDF：通过 pypdf 复制页面，去除页面动作/注释及文档级附件/脚本入口。
- PNG/JPEG/TIFF/BMP：按每帧尺寸、EXIF 方向和 DPI 封装为 PDF；缺失 DPI 使用 150，异常 DPI 限制在 36–1200。不生成图片描述。
- Office：LibreOffice headless，独立用户配置目录、最高宏安全等级、无 shell、无 stdin、超时，输出再次检查 PDF 页数。XLSX 使用文档的打印区域/分页设置；PPTX 使用演示文稿页面。成功、失败和超时均由 TemporaryDirectory 清理本次临时目录。
- TXT/Markdown/HTML：固定 ReportLab 模板，支持中文。Markdown 以原始文本排版；HTML 先删除脚本、嵌入内容、样式和资源，再转换成纯文本。保留表格 rowspan/colspan 仅供解析表格的结构保真，不允许 URL、事件处理器或 CSS。

**进程调用参数不是操作系统沙箱。** 生产转换 Worker 必须在独立低权限容器运行，根文件系统只读、限制内存/CPU/PID/临时磁盘，且转换过程禁止网络访问；服务访问通过独立网络允许列表控制。异常杀进程/宿主机崩溃后，操作系统或部署层还需按 TTL 清理专属临时卷。实际 LibreOffice 转换需要安装该二进制，不能将缺少转换器视作成功。

## MinerU 服务契约

本实现支持 MinerU 2.x 兼容的同步 `POST /file_parse`，使用 `files` multipart 字段，文件名固定为 `document.pdf`。参数包括 `backend=pipeline, parse_method=auto, lang_list=ch, image_analysis=false`，请求 `return_content_list=true`、`return_middle_json=true`、`return_images=true`，禁止 ZIP 响应。使用 pipeline OCR 解析；图片解释、图表分析和视觉描述字段不会映射到内部契约。

接受响应结构：

```json
{
  "version": "2.5.4",
  "results": {
    "document": {
      "content_list": "[{\"type\":\"text\",\"text\":\"内容\",\"page_idx\":0}]",
      "middle_json": "{\"pdf_info\":[{\"page_idx\":0}]}",
      "images": {}
    }
  }
}
```

`content_list` 和 `middle_json` 可以是 JSON 字符串或已解码对象。只接受一个文档结果；必须有 `middle_json.pdf_info`，以避免末尾空白页消失。块类型异常、错误坐标或置信度、页码越界均失败关闭。HTTP 408、429、5xx、连接异常和超时可重试；其他结构错误不可重试。版本号随原始响应保存，升级 MinerU 前必须运行契约测试；新的 `/v1/files`/异步任务 API 和 `content_list_v2` 不会被默默接受为本契约。

官方契约参考：[MinerU fast_api.py](https://github.com/opendatalab/MinerU/blob/master/mineru/cli/fast_api.py)、[api_client.py](https://github.com/opendatalab/MinerU/blob/master/mineru/cli/api_client.py)。本地测试使用明确标记的 HTTP fixture，尚不能证明已部署 MinerU 的模型 OCR 质量。

原始 images 内容保留在 `raw.results.<document>.images`。图片块资产 ID 为 `uuid5(NAMESPACE_URL, f"{version_id}:asset:{img_path}")`；持久化端需按 `img_path` 的文件名匹配 images 键，解码并验证其 MIME/大小后存入 `document_assets`，不得把路径当作本地路径读取。

## 质量门与切分

按照任务书阈值执行空白页比例、乱码比例、OCR 平均置信度、页数差异、标题层级及表格结构检查。只有明确传入 `page_difference_explained=True` 的差一页情况才可 WARN。未提供 OCR 置信度时指标为 null，不伪造置信度；扫描文档的实测 OCR 准确率需要独立评测。

按标题树保留完整章节路径，在章节内部按段落、句子和字符边界切分；不会跨一级标题合并。所有 embedding_text 注入文档名和章节路径。表格依据相邻页、列数、表头相似度和页面位置合并（bbox 契约为 0–1000 页面坐标）；每个行组保留完整表头、HTML、数据行范围、页码。rowspan 关联行组不可分割；单行或关联行组超限时失败关闭，不截断单元格。

图片只使用 MinerU OCR、图注、同章节同页的紧邻正文、章节与定位信息；空白图片不生成 chunk，也不触发任何 LLM 描述。无语义内容的图片仍应保留解析块和资产。

默认 target=650、max=1000、overlap=100、minimum_merge=300，全部可配置。**当前 token_count 使用 UTF-8 字节上界，属于保守预算，并非模型真实 token 数**；这样离线环境无需首次下载 tokenizer，且 byte-level BPE 不会超过上界。中文 chunk 因此可能显著短于 500–800 真实 tokens 的目标；上线调优时应替换为随镜像打包的指定模型 tokenizer，并升级 `chunk_config_version`、重建索引和重新评测。配置快照应记录此限制。

稳定 UUID5 基于 document_id、version_id、section_path、NFC 正规化正文 SHA256、序号和 chunk_config_version。相同版本相同配置重跑 ID 不变；正文或配置版本变化则变化。正文 chunk 仅引用实际覆盖的来源块页码。

## 本地验证及未实测项

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ingestion.py -q
.venv\Scripts\python.exe -m ruff check app/ingestion tests/test_ingestion.py
```

测试覆盖 MIME/签名伪造、PDF 页数/加密、图像尺寸、Office 宏/压缩炸弹/外部关系、扫描默认拒绝、HTML 清理、中文和图片 PDF、MinerU 适配与限流、质量阈值、标题树、稳定 ID、精确页码、跨页表头、rowspan 和空白图片。实际 MinerU、恶意文件扫描服务、Office 三格式转换、模型解析准确率及生产沙箱/资源限额仍需要部署环境验收，不能根据单元测试声称达标。
