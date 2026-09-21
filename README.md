# Jev Memory Gate for QwenPaw

基于 **TypeSafe AI Jev** 极速决策模型的 QwenPaw 自动语义记忆检索智能门控插件。

---

## 1. 它解决什么

QwenPaw 提供了功能强大的长期语义记忆（ReMe Light）检索能力，但在日常使用中存在两个极端：

1. **默认状态（仅作为普通 Tool）**：
   - 依赖主 Agent 在 ReAct 循环中自主判断是否调用 `memory_search`。
   - 主模型经常遗忘调用，导致该回忆上下文时没有搜索。
2. **开启 `auto_memory_search` 实验性功能后**：
   - 系统会在每轮用户消息进入 LLM 推理前，盲目自动执行记忆搜索并伪装工具调用结果。
   - 导致大量无需记忆的闲聊、通用知识问答（如“你好”、“继续”、“Python tuple 和 list 有什么区别”）也触发耗时的向量检索与 Reranker，带来延迟与上下文污染。

**Jev Memory Gate** 在自动检索真正执行之前增加了一道超轻量级（~30-50ms）的“智能门禁”：

> 快速判断当前用户请求是否可能从长期语义记忆中获取对回答有实质帮助的信息？

- **判定无需记忆（SKIP）**：直接短路，不执行检索，0 额外开销，0 上下文污染；
- **判定需要记忆（RETRIEVE）**：无缝委托给原生 ReMe Light 执行检索与消息合成。

---

## 2. 它不做什么（边界说明）

- **不重新实现 ReMe**：底层存储、BM25 全文检索、Embedding 向量搜索、Reranker 重排序全部 100% 复用原生 ReMe。
- **不修改普通 `memory_search` Tool**：主 Agent 主动调用的 `memory_search(...)` 工具完全不受影响，随时可用。
- **不处理 `recall_history`**：本插件只专注于 long-term semantic memory，不介入 Scroll context manager 的 raw turns 回溯。
- **不进行 query rewrite**：保持 query 原样传递给原生检索。
- **不修改 QwenPaw Core**：完全基于官方公开 API `qwenpaw.memory` 与 `PluginApi` 实现，独立存放于 `~/.qwenpaw/plugins/`，随 QwenPaw 核心版本升级不失效。

---

## 3. 架构流程图

```text
User Message
     ↓
QwenPaw Runtime & MemoryMiddleware (on_model_call)
     ↓
BaseMemoryManager.auto_memory_search()
     ↓
JevGatedReMeManager._search_for_auto_memory(query, options)
     ↓
┌─────────────────────────────────────────────────────────────┐
│ JevGate.evaluate(query)                                     │
│ TypeSafe AI Jev SystemOne Call                              │
│ Instructions: "Does answering require long-term memory?"    │
└─────────────────────────────────────────────────────────────┘
     │
     ├── P < threshold (default 0.50)
     │   └── SKIP: 返回 None
     │        └── 无记忆注入，主 Agent 直接进入推理
     │
     └── P >= threshold (或 API 故障 Fail-Open)
         └── RETRIEVE: super()._search_for_auto_memory()
              └── 原生 ReMe BM25 / Vector Search & Reranker
                   └── 生成合成 AssistantMsg 注入 Context
                        └── 主 Agent 带记忆完成单轮推理
```

### Jev SystemOne 真实通信协议

- **Request**:
  ```json
  {
    "model": "jev-latest",
    "state": "<user_query>",
    "questions": {
      "need_long_term_memory": {
        "type": "noul",
        "instructions": "Determine whether answering this user query requires recalling personal preferences, past user facts, project history, or prior decisions stored in long-term semantic memory. Return false for general knowledge questions, chit-chat, self-contained logic, or requests that do not require past personal context."
      }
    }
  }
  ```
- **Response**:
  ```json
  {
    "answers": {
      "need_long_term_memory": {
        "type": "noul",
        "noul": 0.09
      }
    }
  }
  ```
  *插件同时兼容 `answers.<key>.noul`、`decisions.<key>.probability` 以及 `results` 等多种变体。*

---

## 4. 安装方式

本插件属于标准的 QwenPaw Memory Plugin，支持以下任意一种方式安装：

### 方式 A：QwenPaw 官方 CLI 安装（推荐）
```bash
# 从本地目录安装
qwenpaw plugin install /path/to/jev-memory-gate

# 或从 Release ZIP 压缩包安装（支持本地或远程 URL）
qwenpaw plugin install https://github.com/<your-username>/qwenpaw-jev-memory-gate/releases/download/v0.1.0/jev-memory-gate-v0.1.0.zip
```

### 方式 B：Git Clone 源码安装
```bash
git clone https://github.com/<your-username>/qwenpaw-jev-memory-gate.git ~/.qwenpaw/plugins/jev-memory-gate
```

### 验证插件加载
运行以下命令验证插件格式和注册状态：
```bash
# 验证插件结构
qwenpaw plugin validate ~/.qwenpaw/plugins/jev-memory-gate

# 查看已注册的记忆后端（包含 'jev-remelight'）
./venv/bin/python -c "from qwenpaw.memory import memory_registry; print(memory_registry.list_registered())"
# 输出: ['remelight', 'none', 'jev-remelight']
```

---

## 5. 配置指南

### 5.1 配置 API Key（零泄密推荐）

Jev API Key 是访问 TypeSafe AI 决策服务的凭据。**为了避免将密钥误提交到公开仓库，最安全的方式是通过系统环境变量注入**：

```bash
# 在 ~/.zshrc 或 ~/.bashrc 中添加：
export JEV_API_KEY="apikey_your_secret_key_here"
```
保存后执行 `source ~/.zshrc` 生效。插件启动时会自动读取 `JEV_API_KEY`（或兼容读取 `TYPESAFE_API_KEY`）。这样代码和配置文件中都**无需明文存放密钥**！

> **说明**：如果确实需要在配置文件中指定，也可以在后文配置的 `"api_key"` 字段填入。插件代码已对该字段声明隐私保护，日志中会自动脱敏。

---

### 5.2 智能体配置（推荐最佳实践）

在 QwenPaw 2.x 中，每个智能体拥有独立的工作区配置，位于：
`~/.qwenpaw/workspaces/<agent_id>/agent.json`（默认智能体路径为 `~/.qwenpaw/workspaces/default/agent.json`）。也可以在全局 `~/.qwenpaw/config.json` 中配置。

编辑该文件，在 `running` 中切换记忆后端为 `jev-remelight` 并开启自动检索：

```json
{
  "running": {
    "memory_manager_backend": "jev-remelight",
    "reme_light_memory_config": {
      "auto_memory_search_config": {
        "enabled": true,
        "max_results": 2
      }
    },
    "memory_backend_configs": {
      "jev-remelight": {
        "enabled": true,
        "threshold": 0.50,
        "timeout_ms": 2000,
        "proxy": "http://127.0.0.1:7897",
        "model": "jev-latest",
        "endpoint": "https://api.typesafe.ai/v1/systemone",
        "api_key": ""
      }
    }
  }
}
```

#### 配置参数说明：

| 字段 | 类型 | 推荐值 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| `enabled` | boolean | `true` | `true` | 是否启用门控（设为 `false` 则退化为每轮必搜） |
| `threshold` | float | `0.50` | `0.50` | 判定阈值：Jev 计算的 $P(\text{need\_memory}) \ge \text{threshold}$ 时触发检索 |
| `timeout_ms` | integer | `2000` | `2000` | 请求超时（毫秒）。跨域/代理推荐 2000ms，超时立即 Fail-Open 触发原生搜索 |
| `proxy` | string | 视网络而定 | `""` | HTTP/HTTPS 网络代理（如 `"http://127.0.0.1:7897"`），留空直连 |
| `model` | string | `"jev-latest"` | `"jev-latest"` | Jev 模型版本号 |
| `endpoint` | string | 官方接口 | 官方接口 | Jev SystemOne API 终端地址 |
| `api_key` | string | `""` | `""` | 可选。强烈建议留空并使用 `JEV_API_KEY` 环境变量 |

> **提示（网络与长连接）**：
> 插件内部采用了全局持久化 `httpx.AsyncClient` 连接池（Keep-Alive），首次冷启动握手后，后续轮次的决策延迟通常降至 200~400ms。

---

## 6. 容灾与 Fail-Open 设计

Jev 只是辅助门控，任何异常都**绝不允许阻塞主回复**：
- 当遇到请求超时、网络波动、HTTP 4xx/5xx、JSON 格式错误、API Key 未配置时：
- 插件会自动记录 Warning 级别的结构化日志，并**自动 Fail-Open 触发原生搜索（RETRIEVE）**。
- 系统表现如同 Jev 不存在一样安全回退至 QwenPaw 原生行为。

---

## 7. 可观测性与日志排查

插件的决策和网络日志已完全接入 QwenPaw 主日志系统（logger: `qwenpaw.plugins.jev_memory_gate`），并写入主日志文件 `~/.qwenpaw/qwenpaw.log`。

### 查看门控决策日志
```bash
grep -E "jev_memory_gate|JevGate" ~/.qwenpaw/qwenpaw.log
```

输出示例（严格保护用户隐私，不打印 query 原文）：
```text
INFO | event=jev_memory_gate gate_enabled=True decision=SKIP probability=0.0900 threshold=0.50 jev_latency_ms=284.2 query_length=12 fallback_used=False fallback_reason=none
INFO | event=jev_memory_gate gate_enabled=True decision=RETRIEVE probability=0.9400 threshold=0.50 jev_latency_ms=310.5 query_length=24 fallback_used=False fallback_reason=none
```

---

## 8. 自动化测试

在项目根目录运行插件的单元测试与集成测试套件：
```bash
~/.qwenpaw/venv/bin/python -m unittest discover -s ~/.qwenpaw/plugins/jev-memory-gate/tests -p "test_*.py" -v
```

包含 16 个测试用例：
- Jev 概率决策裁决（SKIP vs RETRIEVE）
- 各种通信结构兼容解析（`answers.noul`、`decisions.probability`）
- 容灾 Fail-Open 与超短超时保护
- 原生普通 `memory_search` 工具不受门控影响验证
- QwenPaw 官方 `PluginLoader` 扫描与后端注册冒烟测试

---

## 9. 开发者发布指南（Publishing & Zero-Leak）

如果你希望将本插件分享给其他开发者或开源到 GitHub：

### 9.1 零泄露安全自检 Checklist
在发布代码前，请确保：
- [x] **无硬编码 Key**：`plugin.py`、`gate.py`、`plugin.json` 中的 `api_key` 均为默认空字符串 `""`。
- [x] **忽略本地私有文件**：本仓库已配置 `.gitignore`，自动排除 `.env`、`agent.json`、`*.log`、`__pycache__` 等。
- [x] **测试通过**：本地 16 项自动化测试全部通过。
- [x] **规范校验**：通过 `qwenpaw plugin validate` 检查。

### 9.2 发布方式一：GitHub 开源仓库
1. 在 GitHub 上新建仓库（例如 `qwenpaw-jev-memory-gate`）。
2. 在本地插件目录提交并推送：
   ```bash
   cd ~/.qwenpaw/plugins/jev-memory-gate
   git init
   git add .
   git commit -m "feat: initial release of jev-memory-gate plugin v0.1.0"
   git branch -M main
   git remote add origin https://github.com/<your-username>/qwenpaw-jev-memory-gate.git
   git push -u origin main
   ```

### 9.3 发布方式二：打包为 ZIP 供用户下载安装
在 `~/.qwenpaw/plugins/` 目录下一键生成纯净的分发归档：
```bash
cd ~/.qwenpaw/plugins
zip -r jev-memory-gate-v0.1.0.zip jev-memory-gate \
    -x "*.pyc" \
    -x "*__pycache__*" \
    -x "*.DS_Store" \
    -x "*/.git/*" \
    -x "*/tests/__pycache__/*"
```
用户获取 ZIP 包后，只需在终端执行一行命令即可完成安装：
```bash
qwenpaw plugin install ./jev-memory-gate-v0.1.0.zip
```

---

## 10. 回退与卸载

### 临时回退到原生 ReMe
在 `agent.json` 中将 `memory_manager_backend` 改回 `"remelight"` 即可：
```json
"memory_manager_backend": "remelight"
```

### 彻底卸载
运行 CLI 命令或直接删除插件目录：
```bash
qwenpaw plugin uninstall jev-memory-gate
# 或 rm -rf ~/.qwenpaw/plugins/jev-memory-gate
```
原生 ReMe 存储的日记、知识库和向量索引数据**不受任何影响**。
