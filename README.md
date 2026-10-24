# LangGraph 混合式智能旅行规划系统

一个面向中文旅行规划场景的全栈项目。它用确定性路线算法约束行程顺序，用 LLM 生成可读的日程内容，用规则 Evaluator 定位问题天，并允许用户在结果页局部修改景点或酒店。

## 项目亮点

- **LangGraph 混合编排**：把天气、候选召回、路线算法、Planner 和质量门组成五节点状态图；评估问题最多触发一次完整 Planner 重试。
- **算法与 LLM 协作**：先按坐标完成景点分天、空间聚类和最近邻排序，再把路线骨架交给 LLM 生成日程，避免纯 Prompt 自由编排。
- **候选补足与路线调组**：景点不够覆盖行程天数时先复用候选池，再做有上限的高德补搜；过长的单日路线优先跨天调整。
- **可选路线校验**：高德 MCP 路线校验默认关闭，开启后作为路线节点内的校验步骤运行；核心路线使用本地坐标估算，MCP 不可用时自动回退，仍可正常生成行程。
- **人机协同修改**：支持删除或替换单个景点、替换酒店，以及自然语言多轮修改；只重排受影响的一天并重算预算。
- **端到端闭环**：SSE 工作流进度、地图标记、预算、PDF 导出、结果持久化与刷新恢复都已具备。

## 架构

```text
TripRequest
  -> Weather                     # 获取事实性天气数据
  -> Candidate Collection         # POI / Hotel 并发召回和候选补足
  -> Route Optimizer             # 坐标聚类与最近邻排序
  -> Planner Agent               # 生成结构化行程
  -> Quality Gate                # 确定性检查、报告写回和重试决策
       |-- 可修复问题且未重试 -> Planner Agent
       `-- 通过或达到上限 -> TripPlan / SSE / JSON persistence
```

前端使用 Vue 3、Pinia、Element Plus 和高德 JS API；后端使用 FastAPI、LangGraph、Pydantic、httpx 和高德 Web 服务 / MCP Server。

## 快速启动

### 后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
cp .env.example .env
python run.py
```

在 `backend/.env` 至少配置一个 LLM Key、`AMAP_API_KEY` 和需要时的 MCP Key：

```env
DEEPSEEK_API_KEY=your_llm_key
AMAP_API_KEY=your_amap_web_service_key

# 可选：填写高德 Web 服务 Key，可与 AMAP_API_KEY 复用
AMAP_MCP_API_KEY=your_amap_web_service_key
AMAP_MCP_ENABLED=false
AMAP_MCP_URL=https://mcp.amap.com/mcp
AMAP_MCP_MAX_ROUTE_LEGS=12
AMAP_MCP_TIMEOUT_SECONDS=15
AMAP_MCP_CALL_TIMEOUT_SECONDS=8
```

后端默认地址为 <http://localhost:8100>，Swagger 为 <http://localhost:8100/docs>。

### 前端

```bash
cd frontend
npm ci
npm run dev
```

配置 `frontend/.env`：

```env
VITE_API_BASE_URL=http://localhost:8100
VITE_AMAP_KEY=your_amap_js_key
```

前端默认地址为 <http://localhost:5180>。开发环境通过 Vite 代理请求 `/api`。

## 使用流程

1. 填写城市、日期、预算、交通方式、住宿和偏好。
2. 在首页查看五个阶段的混合规划工作流进度。
3. 在结果页查看地图、每日安排、路线距离和预算；MCP 路线详情作为可选增强。
4. 通过景点菜单、酒店替换或对话抽屉做局部调整。
5. 刷新结果页时，系统会按 `session_id` 恢复已保存的行程。

## 核心接口

| 接口 | 用途 |
| --- | --- |
| `POST /api/trip/plan` | 同步生成行程 |
| `POST /api/trip/plan/stream` | SSE 流式生成行程 |
| `POST /api/trip/plan/resume/{session_id}` | 从失败节点继续规划 |
| `GET /api/trip/result/{session_id}` | 恢复已保存行程 |
| `POST /api/trip/items/modify` | 局部删除 / 替换景点或酒店 |
| `POST /api/trip/chat` | 自然语言多轮修改 |
| `GET /api/map/*` | POI、天气、酒店与地理编码代理 |

## 目录

```text
backend/app/
  agents/        POI、Hotel、Planner、Chat Router 等 LLM Agent
  workflows/     LangGraph 编排、状态、SSE 映射和 checkpoint
  planning/      路线优化、规则评估与 Planner 后处理
  services/      高德 REST / MCP、结果持久化、局部修改、对话服务
  api/           FastAPI 路由

frontend/src/
  views/         首页与结果页
  components/    结果页卡片（地图、每日、预算、天气、规划过程、指标）、替换对话框、对话抽屉
  composables/   地图、PDF、结果恢复、附近餐馆、候选项逻辑
  services/      Axios JSON 接口与 SSE 流读取
  stores/        Pinia 行程状态
  utils/         错误信息、坐标、持久化、餐馆整理等纯函数
```

## 验证

```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check app tests    # 需先 pip install -r requirements-dev.txt

cd ../frontend
npm test
npm run build
```

前端测试使用 Node 内置测试框架，无新增依赖；CI 在 Node.js 20 下运行。

`backend/requirements.txt` 声明直接依赖范围，`backend/requirements.lock.txt` 固定 Python 3.12
下的完整传递依赖，并保留 Windows、macOS 和 Linux 平台标记。升级依赖后，在仓库根目录重新生成锁文件：

```bash
uv pip compile backend/requirements.txt \
  -o backend/requirements.lock.txt \
  --python-version 3.12 \
  --universal
```

GitHub Actions 会在每次 push 和 pull request 中使用锁文件运行后端测试，并通过
`npm ci` 执行前端生产构建。

后端使用标准库 `unittest`，覆盖 Planner 恢复、规则评估回边、路线算法、SSE 顺序、失败续跑、结果文件防碰撞、原子写入和同会话修改串行化。集成测试使用假 LLM / 假外部服务运行真实编译图，不访问真实 LLM、高德 REST 或高德 MCP。

## 说明

- 路线排序使用本地坐标算法；设置 `AMAP_MCP_ENABLED=true` 后，`route_optimizer` 节点会在本地排序之后调用高德 MCP 校验，再把结果传给 Planner。默认关闭。
- MCP 校验默认设置 15 秒整体超时和 8 秒单段调用超时（异步取消后仍需完成资源清理）。单日各段全部成功才更新该日；整体超时或连接关闭异常时丢弃本次验证结果，恢复原始坐标估算。调用方取消会继续向上传播。
- 景点局部修改后会重新运行当天的本地排序，避免保留已失效的路径校验结果。
- 高德 REST 的 POI、天气和地理编码调用只对传输错误、HTTP 429 和 5xx 最多尝试两次；酒店搜索复用 POI 的重试预算。其他 HTTP 错误及解析错误直接交给调用方处理。此上限针对单次服务方法调用，Agent 的不同关键词搜索和候选补搜仍各有独立调用。
- 结果持久化采用 JSON 文件；文件名使用完整 `session_id` 的哈希，写入采用临时文件原子替换，同会话的读取-修改-校验-写入会被串行化。存储访问封装在异步仓库适配器中。
- SSE 完成时若 JSON 保存失败，前端仍接收并展示已生成行程，同时提示刷新后无法恢复，不会因持久化失败中断流。
- LangGraph 运行状态写入 SQLite checkpoint，用于失败续跑：规划中途失败时，`POST /api/trip/plan/resume/{session_id}` 从失败节点继续，不重复调用天气、POI、酒店和 LLM。成功完成的会话会立即删除 checkpoint，未完成的按 `CHECKPOINT_RETENTION_DAYS`（默认 7 天）在启动时清理。结果页的跨重启恢复由最终行程 JSON 提供，`RESULT_RETENTION_DAYS` 默认 0（不自动清理）。聊天历史只保存在当前后端进程和前端 Pinia 中，不做持久化。
- 自动测试使用假 LLM 和假外部服务；与真实 LLM、高德 REST、高德 MCP 的联调需要配置 Key 后进行。

## 配置与安全

服务端 LLM Key 和高德 Web 服务 Key 只保存在后端环境变量中；前端仅使用独立的高德 JS API Key。允许的跨域来源通过 `CORS_ORIGINS` 配置。
