# AI Company Agent / 智企 Agent

一款安装包轻量、数据本地化的「AI 公司员工」桌面应用：创建多个 AI 员工角色，为每个角色配置不同 LLM、岗位职责、性格与语音，让 AI 员工像真实公司一样开会商讨、执行浏览器 RPA 任务，并将成果沉淀为本地 SQLite 知识库。

PRD：见仓库根目录 `AI-Company-Agent-PRD-v1.2.md`。

## 功能实现状态（对照 PRD v1.2）

### 4.1 组织架构与员工管理（P0）— ✅ 全量

| 需求 | 实现 |
|---|---|
| F-ORG-01 树展示 / 拖拽调整部门归属 | 部门树 + 员工行可拖拽落到目标部门 |
| F-ORG-02 新增员工（姓名/岗位/性别/头像/部门） | 头像上传并复制进应用数据目录 |
| F-ORG-03/04 岗位职责与性格 | 多行文本，支持 `{{meeting_topic}}` 占位符 |
| F-ORG-05 性别枚举 | 男/女/其他，作为 TTS 音色初始映射 |
| F-ORG-06 员工级 LLM 绑定 | 可选，留空继承全局默认 |
| F-ORG-07 ASR/TTS/音色覆盖 | 留空继承全局，界面展示「继承/覆盖」状态 |
| F-ORG-08 启用/停用 | 停用员工不参与会议调度 |
| 交互要求 | 右侧表单 + system prompt 实时预览 |

### 4.2 模型配置中心（P0）— ✅ 全量

- **厂商清单外置为 JSON**（`backend/app/data/vendors.json` + `catalog.json`），新增厂商不改核心代码（F-MOD-05）
- LLM 14 家 / ASR 8 家 / TTS 8 家；云厂商 + 本地引擎（Ollama / LM Studio / sherpa-onnx / whisper.cpp / MOSS-TTS / CosyVoice）
- 统一配置范式：厂商 → 凭证 → 获取模型 →（TTS 再取音色）→ 连接测试/试听 → 保存
- 凭证按厂商去重共用，支持**多账号别名**（`UNIQUE(provider, alias)`），删除被引用凭证时返回受影响配置清单
- API Key 存系统密钥链（keyring），失败回退本机加密文件（Fernet），SQLite 内无明文
- 厂商无模型列表接口时走内置清单 + 手动填写 `model_id` 兜底

### 4.3 多 Agent 会议商讨（P0）— ✅ 全量

- 三种调度策略：轮流固定 / 主持人指定 / AI 自主选择下一位
- 上下文可见性可配（主持人全量 / 成员最近 N 轮 + 被 @ 内容）
- 轮次与 token 双预算，80% 触发 UI 警示，超限自动收尾
- SSE 逐字流式，标注发言员工与所用模型
- 用户插话可选择**是否打断当前流式生成**（打断后标记「已打断」并保留已生成部分）
- 纪要自动生成（摘要 / 决议 / 待办含负责人）并写入知识库
- 会议记录回放（时间轴）、上下文压缩开关、4 套会议模板
- 单场成本预估（人数 × 轮次 → token → 元）与实时费用显示
- 单次 LLM 调用失败自动重试 ≤2 次，仍失败则跳过该员工并标注，不中断整场会议

### 4.4 语音交互（P1）— ✅ 全量

- ASR：OpenAI 兼容云引擎（OpenAI / Groq / 硅基流动）真实调用 + 本地引擎（sherpa-onnx / whisper.cpp）探测
- TTS：OpenAI 兼容云引擎真实调用 + Azure TTS（SSML）+ 本地引擎
- 性别 → 音色映射表（按厂商内置），手动指定优先
- 前端 MediaRecorder 录音 → 上传转写 → 填入输入框；TTS 音频播放
- 本地模型不可用时提示切换已配置的云厂商

### 4.5 浏览器 RPA（P1）— ✅ 全量

- Playwright 驱动，无障碍树（ARIA snapshot）识别元素，DOM 回退兜底
- 动作：click / type / select / scroll / waitFor / extract
- 内容提取为 Markdown / JSON
- 持久化 profile（Cookie/localStorage 隔离），登录态复用
- 登录墙 / 验证码检测 → 暂停并提示人工接管
- 域名白名单强制 + 高风险动作（提交/支付/删除）二次确认
- 外部内容以 `<external_content>` 边界标记注入，防提示词注入
- 浏览器内核按需下载（进度 / 体积 / 删除入口）

### 4.6 本地知识库（P0/P1）— ✅ 全量

- 原始事件层（events / meeting_messages / rpa_runs）与知识条目层（knowledge_items）分离
- 语义检索：sqlite-vec 虚拟表**按模型 + 维度分表**（`vec_bge_small_zh_512` / `vec_bge_m3_1024`），未装 sqlite-vec 时回退本地余弦计算
- 嵌入模型切换 → 后台全量重嵌入任务，期间旧条目降级 FTS5 召回并标注「重建中」
- FTS5 trigram 中文分词；会议纪要、RPA 提取自动入库；条目可编辑/删除/打标签

### 4.7 系统设置与安全（P0）— ✅ 全量

- 域名白名单、高风险动作确认开关、无头模式开关
- 数据导出/备份（SQLite + 脱敏设置，不含明文 Key）
- 模型包管理（下载状态 / 体积 / 删除 / 手动放置登记）、缓存与日志清理
- 隐私说明（本地存储 / 发往云厂商的数据 / 安全边界，逐项列明）
- 自动更新检查（拉取签名 manifest）；Windows 签名与 macOS 公证接入 CI（Gate G1）

## 技术栈

- **后端**：Python 3.13 + FastAPI + SQLite（WAL + FTS5 trigram + sqlite-vec），`backend/`
- **前端**：React 19 + TypeScript + Vite，`frontend/`
- **桌面壳**：Tauri 2.x，Rust 主进程管理 Python sidecar 生命周期，`src-tauri/`
- **LLM 网关**：OpenAI 兼容 + Anthropic/Gemini 轻量适配（含 mock 离线通道，无 Key 可跑通全流程）

## 目录结构

```
backend/
  app/
    db.py            # 数据模型 + 迁移 + FTS5
    settings.py      # 系统设置 / 白名单 / 导出 / 更新
    providers.py     # 厂商清单（JSON 驱动）
    llm_gateway.py   # LLM 网关（多厂商 + mock）
    orchestrator.py  # 会议编排引擎
    voice.py         # ASR / TTS 服务
    embedding.py     # 嵌入模型 + 向量检索 + 重嵌入迁移
    rpa.py           # 浏览器 RPA 服务
    models_mgr.py    # 模型包下载管理
    security.py      # 凭证加密
    data/            # vendors.json / catalog.json（厂商清单）
    routers/         # API 路由
  tests/smoke.py     # 63 项端到端冒烟
frontend/src/pages/  # 组织架构 / 模型配置 / AI 会议 / 语音 / RPA / 知识库 / 系统设置
src-tauri/           # Tauri 桌面壳
```

## 本地运行

```bash
# 后端（默认 http://127.0.0.1:8756，托管前端构建产物）
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m app.main

# 前端开发（热更新，代理 /api 到 8756）
cd frontend
npm install
npm run dev        # http://localhost:5180
npm run build      # 产物由后端托管时直接刷新页面即可
```

无 API Key 时可在模型配置中心选择 **Mock（离线演示/测试）** 厂商，即可跑通会议 → 纪要 → 知识库全流程。

## 测试

```bash
# 服务启动后：
cd backend && python tests/smoke.py   # 63 项端到端冒烟
```

## CI（GitHub Actions）

| Job | 说明 |
|---|---|
| `backend-test` | Windows 装依赖 → 起服务 → 跑 63 项冒烟 |
| `frontend-build` | TypeScript 检查 + Vite 构建 |
| `build-bundle` | 矩阵（Windows / macOS）PyInstaller onedir 打包 sidecar，并校验体积 ≤ 60 MB（Gate G0） |
| `tauri-windows` | Tauri 桌面壳构建 + NSIS 打包 + 签名 |
| `tauri-macos` | macOS 构建 + 签名 + 公证（需配置证书 secret 后启用，Gate G1） |
| `release` | 打 tag 时创建 Release 并生成自动更新清单 `latest.json` |

## 发布前必须完成

- **附录 A 厂商/模型核实**：内置的本地 TTS 模型（MOSS-TTS / CosyVoice）发布状态、体积、License 需逐项核实后回填 `backend/app/data/catalog.json`
- **Gate G1 签名与公证**：配置 `APPLE_CERTIFICATE` / `APPLE_ID` 等 secret，Windows 签名证书
- **RPA 浏览器内核**：首次启用时由 Playwright 下载（约 150–400 MB），不随安装包分发
