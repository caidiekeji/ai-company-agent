# AI Company Agent / 智企 Agent

一款安装包轻量、数据本地化的「AI 公司员工」桌面应用：创建多个 AI 员工角色，为每个角色配置不同 LLM、岗位职责、性格与语音，让 AI 员工像真实公司一样开会商讨，并将成果沉淀为本地 SQLite 知识库。

PRD：见 `AI-Company-Agent-PRD-v1.2.md`（仓库外文档，可自行纳入版本管理）。

## 当前进度（M1 进行中）

| 模块 | 状态 |
|---|---|
| SQLite 数据模型（PRD §6 全表 + FTS5 trigram 中文检索） | ✅ 完成 |
| 凭证加密（系统密钥链优先 / Fernet 加密文件回退，不明文落库） | ✅ 完成 |
| 模型配置中心（LLM/ASR/TTS 统一范式：厂商→凭证→获取模型→测试→保存） | ✅ 完成 |
| 组织架构 / 员工管理 | ✅ 完成 |
| 多 Agent 会议编排（轮流/主持人指定/AI 自选、SSE 流式、轮次+token 预算、纪要入库） | ✅ 完成 |
| 知识库（事件层 / 条目层 / FTS5 检索 + LIKE 回退） | ✅ 完成 |
| React 前端（组织树 / 配置中心 / 会议界面 / 知识库） | ✅ 完成 |
| Tauri 桌面壳 | ⏳ 待接（本机无 Rust，工程文件 + CI 构建） |
| 语音 ASR/TTS 实际引擎接入、浏览器 RPA | ⏳ M2 |

## 技术栈

- **后端**：Python 3.13 + FastAPI + SQLite（WAL + FTS5 trigram），`backend/`
- **前端**：React 19 + TypeScript + Vite，`frontend/`
- **LLM 网关**：OpenAI 兼容 + Anthropic/Gemini 轻量适配（含 mock 离线通道，无 Key 可跑通全流程）

## 本地运行

```bash
# 后端（默认 http://127.0.0.1:8756，托管前端构建产物）
cd backend
python -m venv .venv && .venv/Scripts/pip install fastapi "uvicorn[standard]" httpx keyring cryptography
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
cd backend && python tests/smoke.py   # 22 项端到端冒烟
```

## CI（GitHub Actions）

- `backend-test`：Windows 上装依赖 → 起服务 → 跑 22 项冒烟
- `frontend-build`：TypeScript 检查 + Vite 构建
- `build-bundle`：PyInstaller onedir 打包 sidecar 可执行产物（上传 artifact）
- `tauri-build`：桌面壳构建占位（接入 src-tauri 后启用）
