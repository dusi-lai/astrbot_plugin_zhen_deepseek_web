# 真·DeepSeek 联网搜索与抓取

![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16%2C%3C5-4D6BFE)
![version](https://img.shields.io/badge/version-1.2.4-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

一个 AstrBot 插件：把 **DeepSeek 官方 Search / Fetch 接口**接成机器人可用的联网能力，
让模型能搜索实时网页、抓取正文，也能用指令手动搜索 / 抓取。

- 搜索：`POST https://api.deepseek.com/v1/tools/search` —— 自研搜索引擎，返回结构化结果
- 抓取：`POST https://api.deepseek.com/v1/tools/fetch` —— 返回网页标题与 Markdown 正文

## 特性

- **两个模型工具**：`deepseek_web_search`、`deepseek_web_fetch`，模型按需自动调用
- **搜索语法**：支持 `site:`、`-site:`、`"精确短语"`、`lang:zh,en`、`after:2025`、`before:2026-06-01`
- **二次抓取**：搜索结果不满足时，可用 `fetch_top=1~3` 自动抓取排名靠前结果的正文（每条多一次 Fetch 计费）
- **正文转长图**：`/抓取` 默认用 Pillow 在本地把 Markdown 正文画成长图发送，不依赖 playwright / 浏览器
- **零额外依赖**：内置 requests 全家桶，安装时不需要 `pip install`，也不写宿主 site-packages
- **免重复填 Key**：插件未配置 Key 时，会自动复用 AstrBot 里已配置的 DeepSeek 提供商密钥

## 环境要求

| 项 | 要求 |
| --- | --- |
| AstrBot | `>=4.16,<5`（见 `metadata.yaml` 的 `astrbot_version`） |
| Python 依赖 | 无，依赖已内置在 `_vendor/` |
| 消息平台 | 不限；长图需平台支持图片消息 |
| DeepSeek API Key | 需要。可在插件配置填写，或复用 AstrBot 已配置的 DeepSeek 提供商 |

## 安装

> 装完都在 AstrBot 管理面板的「插件 → 插件 → 已安装」确认状态：插件已启用、未出现在「加载失败插件」列表。

### 方式一：插件市场安装（推荐）

1. 管理面板 → 左侧 **插件** → 顶部 **插件** 标签 → **插件市场**；
2. 搜索「**真·DeepSeek**」或 `zhen_deepseek`；
3. 点插件卡片的 **安装**，确认来源后等待完成。

### 方式二：从仓库链接安装

1. 「插件」页右下角 **安装插件（+）** → **从链接安装**；
2. 填写仓库地址：`https://github.com/dusi-lai/astrbot_plugin_zhen_deepseek_web`；
3. 确认来源并安装。

> 这里填的是**代码仓库地址**，不是插件介绍页或 Cloud 页面地址。想要最新提交时用这个方式。

### 方式三：从 zip 安装（离线 / 内网）

1. 在 [Releases](https://github.com/dusi-lai/astrbot_plugin_zhen_deepseek_web/releases) 下载 `astrbot_plugin_zhen_deepseek_web-<版本>.zip`；
2. 「插件」页 → **安装插件（+）** → **从文件安装** → 上传该 zip。

### 方式四：手动放入插件目录（进阶）

| AstrBot 部署方式 | 插件目录 |
| --- | --- |
| 桌面版（Windows） | `%USERPROFILE%\.astrbot\data\plugins\` |
| Docker / 服务器 | 数据目录下的 `data/plugins/`（容器内通常 `/AstrBot/data/plugins`） |

把仓库文件夹整体放入（目录名保持 `astrbot_plugin_zhen_deepseek_web`），然后在 WebUI「插件」页点该插件的刷新图标（重载插件）。

### 更新与卸载

- **更新**：插件市场或「已安装」页点插件卡片的 **更新**；插件配置不会丢失。
- **卸载**：插件卡片菜单里卸载；`data/plugin_data/astrbot_plugin_zhen_deepseek_web/` 下的历史长图可手动清理。
- 插件是运行时注入的，重载插件不需要重启 AstrBot。

## 配置

必填项只有一个：**API Key**。其余都有合理默认值。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `api_key` | 空 | DeepSeek API Key。留空时依次尝试：环境变量 `DEEPSEEK_API_KEY` → AstrBot 里已配置的 DeepSeek 提供商密钥 |
| `base_url` | `https://api.deepseek.com` | 接口地址，一般不用改 |
| `timeout` | 30 | 请求超时（秒） |
| `search_count` | 8 | 默认返回条数（1~50） |
| `need_content` | false | 搜索时是否默认返回网页正文 |
| `auto_fetch_top` | 0 | 二次抓取：自动抓前 N 条结果正文（0~3，0 = 关闭） |
| `auto_fetch_chars` | 4000 | 二次抓取时单篇正文截断长度 |
| `max_summary_chars` | 800 | 每条结果摘要截断长度 |
| `max_content_chars` | 8000 | 单篇正文给模型的截断长度 |
| `max_tool_output_chars` | 12000 | 单次搜索结果总长度上限（防上下文爆炸） |
| `fetch_output_mode` | image | `/抓取` 输出方式：`text` / `image` / `both` |
| `image_width` | 1080 | 长图宽度（像素） |
| `image_max_chars` | 6000 | 长图最多容纳的正文字数 |
| `image_font` | 空 | 自定义字体路径（长图出现方块时填，如 `C:/Windows/Fonts/msyh.ttc`） |
| `chat_preview_chars` | 1200 | 文字预览长度 |
| `enable_commands` | true | 是否启用 `/搜索`、`/抓取` 指令 |
| `proxy` | 空 | 代理地址；留空则复用 AstrBot 全局 `http_proxy`，再留空跟随系统环境变量 |

配置保存在 `data/config/astrbot_plugin_zhen_deepseek_web_config.json`，修改后如未生效可在插件页重载一次。

## 使用

### 交给模型（主要用法）

直接描述需求即可，模型会自己决定是否调用工具：

> 搜一下 DeepSeek V3.2 的发布说明，把官网那篇的正文抓下来总结成要点

> 查一下这个链接讲了什么：https://api-docs.deepseek.com/zh-cn/guides/search

需要时模型可传 `fetch_top` 让搜索顺带抓取排名靠前结果的正文。

### 指令（需要唤醒前缀，默认 `/`）

| 指令 | 说明 |
| --- | --- |
| `/搜索 关键词` | 搜索并展示前 5 条结果（别名 `/ws`、`/websearch`） |
| `/抓取 URL` | 抓取网页，默认渲染成长图发送（别名 `/wf`、`/webfetch`） |
| `/dsweb` | 查看插件状态：Key 是否就绪（脱敏）、接口地址、代理、内置依赖（别名 `/联网状态`） |

示例：

```text
/搜索 DeepSeek 模型 site:github.com
/搜索 "精确短语" lang:zh,en after:2025
/抓取 https://api-docs.deepseek.com/zh-cn/guides/search
```

### 工具参数

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `deepseek_web_search` | `query`（必填） | 搜索词，≤128 字符，支持搜索语法 |
| | `count` | 返回条数 1~50，默认取插件配置 |
| | `need_content` | 是否同时返回网页正文 |
| | `fetch_top` | 0~3，自动抓取前 N 条结果正文（二次抓取） |
| | `ori_question` | 触发搜索的用户原始问题，帮助理解搜索意图 |
| `deepseek_web_fetch` | `url`（必填） | 要抓取的网页地址，需为含 host 的 http/https 地址，≤8192 字符 |

## 计费与限速

Search / Fetch 按**请求次数**计费，不按 token 或返回条数：

| 项目 | 说明 |
| --- | --- |
| 价格 | 各 **5 元 / 1000 次成功请求**（以 DeepSeek 官方为准） |
| 不计费 | 请求失败、搜索无结果、超时 |
| 限速 | Search 100 QPS、Fetch 25 QPS（账号级）；Fetch 对目标域名另有 QPS 限制 |

> 开启 `auto_fetch_top` 或模型传 `fetch_top` 时，每次搜索会额外产生对应次数的 Fetch 请求。

## 错误处理

插件按 DeepSeek 官方文档实现重试：**408 / 429 / 5xx 自动退避重试（最多 3 次，尊重 `Retry-After`）**，其它 4xx 不重试，并把状态码翻译成中文提示。

| 状态码 | 含义 |
| --- | --- |
| 400 | 参数不合法（query 长度 / count 范围 / URL 格式） |
| 401 | API Key 无效 |
| 402 | 账号余额不足 |
| 403 | Fetch 被目标站点安全策略拒绝 |
| 404 | 未能获取可用内容（被拦截 / 无正文） |
| 413 | URL 超过 8192 字符 |
| 429 | 超出 QPS（重试后仍失败会提示稍后再试） |
| 500 / 504 | 服务端错误 / 下游超时 |

## 隐私与安全

- **只连一个域名**：`https://api.deepseek.com`，代码中没有其它出站地址
- **密钥用途单一**：仅作为 `Authorization` 请求头发给 DeepSeek，不写日志、不回显、不拼进 URL
- **无遥测**：不引入统计 SDK，不向第三方发送数据
- **依赖可核对**：`_vendor/` 为 requests 及其依赖的原样副本，许可证见 `_vendor/_licenses/` 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
- **随时自检**：`/dsweb` 可查看 Key 是否就绪（脱敏显示）与当前接口地址

> 插件会在 AstrBot 所在环境执行代码，请只安装你信任的来源；本插件全部逻辑在 `main.py`，可直接阅读审计。

## 常见问题

**Q：模型不调用联网工具？**
确认模型支持工具调用（function calling），且当前人格没有限制可用工具列表；`/dsweb` 可确认插件已正常加载。

**Q：搜索指令没反应？**
指令需要唤醒前缀（默认 `/`）或 @机器人；也可以查看「插件 → 管理行为 → 指令」确认指令已注册。

**Q：长图出现方块或乱码？**
系统缺少中文字体。在配置 `image_font` 填一个字体文件路径，例如 `C:/Windows/Fonts/msyh.ttc`。

**Q：不想发长图？**
把 `fetch_output_mode` 改成 `text`（纯文字）或 `both`（文字 + 长图）。

**Q：为什么叫「真·」？**
仅用于和插件市场里同名的插件区分，无其它含义。

**Q：会产生费用吗？**
会。搜索与抓取按成功请求计费，失败或无结果不计费，详见上面「计费与限速」。

## 开发

```text
astrbot_plugin_zhen_deepseek_web/
├── main.py              # 全部实现：HTTP 客户端、结果格式化、Pillow 长图、指令、两个 LLM 工具
├── metadata.yaml        # 插件元数据
├── _conf_schema.json    # 配置 Schema
├── requirements.txt     # 空文件（依赖已内置）
├── _vendor/             # requests / urllib3 / certifi / idna 及其许可证
├── CHANGELOG.md
├── THIRD_PARTY_NOTICES.md
├── LICENSE
└── README.md
```

本地调试：把仓库目录放进 AstrBot 的 `data/plugins/` 下，修改 `main.py` 后在 WebUI 点「重载插件」即可生效；可用 `python -m py_compile main.py` 做语法检查。

提交 PR 前请保持单文件实现、不要删除 `_vendor/`（它保证插件在任意 AstrBot 环境可直接运行），并同步更新 `CHANGELOG.md` 与版本号。

## 许可证

[MIT](LICENSE)。随包分发的第三方依赖各自保留原许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 相关链接

- DeepSeek 接口文档：[网页搜索](https://api-docs.deepseek.com/zh-cn/api/search) · [网页抓取](https://api-docs.deepseek.com/zh-cn/api/fetch) · [使用指南](https://api-docs.deepseek.com/zh-cn/guides/search)
- AstrBot：[官网](https://astrbot.app) · [插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- 插件市场：[AstrBot Cloud](https://cloud.astrbot.app/plugin/dusi-lai/astrbot_plugin_zhen_deepseek_web)
