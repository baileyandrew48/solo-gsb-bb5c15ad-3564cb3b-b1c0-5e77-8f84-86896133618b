# 本地字幕切分 API

基于 **Python + FastAPI** 的本地字幕切分服务。输入按时间排序、互不重叠的词级转写
（文字 + 起止毫秒）以及切分约束，服务通过**动态规划**寻找覆盖全部词、保持顺序的
最优合法切分，并支持导出 SRT 字幕。

## 算法说明

在所有“切分点只能位于相邻词之间”的切分方案上做动态规划：

- 一条字幕合法，当且仅当：覆盖的词连续、字数（含词间空格）不超过 `max_chars`，
  且时长在 `[min_duration, max_duration]`（秒）之内；
- **总代价** = Σ (字幕时长秒数 − `target_duration`)²
  + 4 × 被跨过的“句末标点 / 长停顿边界”数量；
  - 词以 `。！？!?….．.` 结尾，或相邻词间隔 ≥ `long_pause` 秒时，该间隙为边界；
  - “跨过”指该间隙两侧的词被放进同一条字幕；
- 总代价相同时，选择**字幕条数更少**的方案；
- 单词自身超过字数上限 / 时长上限，或不存在任何合法切分时，返回 `422` 与原因。

词间空格规则：仅当相邻两词的连接处两侧均为 ASCII 字母/数字时才插入空格，
因此英文/数字之间保留空格，中文等文字之间不会插入多余空格。

## 一键启动（推荐）

需要本机已安装 Docker 与 Docker Compose Plugin：

```bash
docker compose up --build
```

启动后访问：

- 服务地址：<http://localhost:8000>
- 健康检查：<http://localhost:8000/health>
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI 文档：<http://localhost:8000/openapi.json>

监听端口固定为容器内 **8000**。如需更换宿主机端口，修改
`docker-compose.yml` 中的端口映射（如 `"18000:8000"`）后重新启动即可，
无需任何其他配置；服务无外部依赖、不需要密钥或数据库。

## 本地直接运行（可选）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 接口

### `POST /segment` — 分段 JSON

请求体（词时间戳单位为毫秒，其余时长参数单位均为秒）：

| 字段             | 类型     | 说明                                                 |
| ---------------- | -------- | ---------------------------------------------------- |
| `words`          | 数组     | 词级转写，每项含 `text`、`start_ms`、`end_ms`        |
| `max_chars`      | 整数 > 0 | 每条字幕字数上限                                     |
| `min_duration`   | 秒 > 0   | 每条字幕时长下限                                     |
| `max_duration`   | 秒 > 0   | 每条字幕时长上限                                     |
| `target_duration`| 秒 > 0   | 目标时长，须位于时长上下限之间                       |
| `long_pause`     | 秒 > 0   | 长停顿阈值，相邻词间隔达到该值即视为停顿边界         |

请求示例：

```bash
curl -s http://localhost:8000/segment \
  -H 'Content-Type: application/json' \
  -d '{
    "words": [
      {"text": "你好", "start_ms": 0,    "end_ms": 600},
      {"text": "世界", "start_ms": 700,  "end_ms": 1500},
      {"text": "今天", "start_ms": 3000, "end_ms": 3600},
      {"text": "天气", "start_ms": 3700, "end_ms": 4400},
      {"text": "不错。","start_ms": 4500, "end_ms": 5200}
    ],
    "max_chars": 12,
    "min_duration": 1.0,
    "max_duration": 3.0,
    "target_duration": 2.0,
    "long_pause": 0.8
  }'
```

响应示例：

```json
{
  "segments": [
    {
      "index": 1,
      "text": "你好世界",
      "start_ms": 0,
      "end_ms": 1500,
      "duration": 1.5,
      "char_count": 4,
      "cost": 0.25
    },
    {
      "index": 2,
      "text": "今天天气不错。",
      "start_ms": 3000,
      "end_ms": 5200,
      "duration": 2.2,
      "char_count": 7,
      "cost": 0.04000000000000007
    }
  ],
  "total_cost": 0.29000000000000004
}
```

上例中两组词之间有 1.5 秒间隔（≥ `long_pause`），该停顿边界处发生了切分，
因此没有罚分。

### `POST /segment/srt` — 导出 SRT

请求体与 `/segment` 完全相同，直接返回 `text/srt` 内容（`subtitles.srt` 附件）：

```bash
curl -s http://localhost:8000/segment/srt \
  -H 'Content-Type: application/json' \
  -d @request.json -o subtitles.srt
```

### `GET /health`

返回 `{"status": "ok"}`，供容器健康检查与负载探活使用。

## 错误处理（HTTP 422）

以下情况返回 `422 Unprocessable Entity`，响应体为 `{"detail": "原因"}`：

- 请求参数缺失、类型错误，或 `target_duration` 不在时长上下限之间；
- 词文本为空、`end_ms < start_ms`、词之间乱序或时间重叠；
- 某个词自身字数超过 `max_chars`，或自身时长超过 `max_duration`（单词无法容纳）；
- 在给定约束下不存在覆盖全部词的合法切分。

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py        # FastAPI 应用、路由与 422 异常处理
│   ├── schemas.py     # 请求/响应模型与参数校验
│   ├── segmenter.py   # 动态规划切分核心
│   └── srt.py         # SRT 导出
├── tests/
│   └── test_segmenter.py
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 运行测试

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q
```
