# 真·DeepSeek 联网搜索与抓取（AstrBot 插件）

给 AstrBot 的模型装上**联网能力**，接的是 DeepSeek 官方两个接口：不是爬虫，也不是第三方搜索代理。

| 工具 | 对应官方接口 | 作用 |
| --- | --- | --- |
| `deepseek_web_search` | `POST https://api.deepseek.com/v1/tools/search` | 网页搜索：标题/URL/站点/时间/权威度/摘要，可选正文、可二次抓取 |
| `deepseek_web_fetch` | `POST https://api.deepseek.com/v1/tools/fetch` | 抓取单个网页，返回标题 + Markdown 正文 |

## 为什么叫「真·」

市场里已经有一个同名的联网插件，我自己装过，但在我的环境里没跑通、也没得到想要的效果。
与其去猜别人是怎么实现的，不如照官方文档自己写一个——**「真·」只是重名时用来区分的前缀，没有别的意思，也不针对任何具体项目。**

下面这些是这个插件自己的做法，不是对谁的评价，你可以照着代码逐行核对：

1. **只连一个域名**：`https://api.deepseek.com`（用 `grep -n "http" main.py` 数一下就知道）。
2. **密钥只出现在一个地方**：请求头 `Authorization: Bearer <key>`。不写日志、不回显、不拼进 URL、除这两个接口外不发任何额外请求。
3. **代码是直白的**：全部逻辑在 `main.py`（约 800 行、中文注释），没有混淆、没有动态执行（无 `exec/eval/base64`）、没有下载后执行。
4. **没有遥测**：不引入任何统计 SDK。
5. **依赖可对账**：`_vendor/` 是 requests 全家桶的原样副本，附各包 LICENSE，可与 PyPI 官方包逐文件比对。
6. **随时自检**：`/dsweb` 会显示 key 是否就绪（脱敏）、接口地址、代理、内置 requests 状态。

> 插件名（`metadata.yaml` 的 `name`）是 `astrbot_plugin_zhen_deepseek_web`，插件身份 `author/name` 由插件市场按它生成，改名会变成另一个插件。

## 功能

### 1. 给模型的两个工具

- **`deepseek_web_search`**
  - `query`（≤128 字符，支持 `site:`、`-site:`、`"精确短语"`、`lang:zh,en`、`after:2025`、`before:2026-06-01`）
  - `count` 1~50、`need_content` 是否带正文、`ori_question` 原始问题
  - **`fetch_top=1~3`：二次抓取**——搜索完自动把排名靠前的结果正文一起抓回来（每条多一次 Fetch 计费）
- **`deepseek_web_fetch`**
  - `url`：必须是含 host 的 http/https 地址、≤8192 字符、不能带用户名密码

### 2. 三条指令（需要 wake 前缀，默认 `/`）

| 指令 | 说明 |
| --- | --- |
| `/搜索 关键词` | 搜索并展示前 5 条（别名 `/ws`、`/websearch`） |
| `/抓取 URL` | 抓取网页，默认**渲染成长图**发送（别名 `/wf`、`/webfetch`） |
| `/dsweb` | 状态自检（别名 `/联网状态`） |

### 3. 正文转长图（不依赖 playwright）

`/抓取` 默认把 Markdown 正文**用 Pillow 在本地画成一张长图**发出去：
- 自动清理 Markdown 转义（DeepSeek 的正文里 `\#`、`\|`、`\(` 很多）、表格转成 `a | b` 行、代码块保留
- 自动挑系统中文字体（Windows 优先微软雅黑，可在配置里指定 `image_font`）
- 宽度 / 最大字数 / 截断标记都可配，避免生成几万像素的图
- 绘制失败会自动回退成文字预览，不会让指令失败

## 安装

```powershell
Copy-Item -Recurse -Force ".\astrbot_plugin_zhen_deepseek_web" "$env:USERPROFILE\.astrbot\data\plugins\"
```

然后在 AstrBot WebUI 的「插件」页点该插件卡片上的刷新图标（重载插件）。

## 配置

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `api_key` | 空 | **留空也能用**：依次回退到环境变量 `DEEPSEEK_API_KEY` → AstrBot 里已配置的 DeepSeek 提供商密钥 |
| `base_url` | `https://api.deepseek.com` | 一般不用改 |
| `timeout` | 30 | 秒 |
| `search_count` | 8 | 默认返回条数（1~50） |
| `need_content` | false | 搜索默认是否带正文 |
| `auto_fetch_top` | 0 | 二次抓取：自动抓前 N 条结果正文（0~3，0=关） |
| `auto_fetch_chars` | 4000 | 二次抓取单篇截断 |
| `max_summary_chars` / `max_content_chars` | 800 / 8000 | 摘要、正文给模型的截断长度 |
| `max_tool_output_chars` | 12000 | 单次搜索总输出上限（防爆上下文） |
| `fetch_output_mode` | image | `/抓取` 输出：text / image / both |
| `image_width` | 1080 | 长图宽度 |
| `image_max_chars` | 6000 | 长图最多容纳字数 |
| `image_font` | 空 | 自定义字体路径（长图乱码时用） |
| `chat_preview_chars` | 1200 | 文字预览长度 |
| `enable_commands` | true | 是否启用文本指令 |
| `proxy` | 空 | 留空复用 AstrBot 全局 `http_proxy` / 系统环境变量 |

## 使用示例

**让模型自己用**（主要方式）：直接说
> 「搜一下 DeepSeek V3.2 的发布说明，把官网那篇的正文抓下来总结成要点」

模型会自己 `deepseek_web_search` → 必要时 `fetch_top=1` 或 `deepseek_web_fetch`。

**手动验证**：

```text
/dsweb
/搜索 DeepSeek 模型 site:github.com
/搜索 "精确短语" lang:zh,en after:2025
/抓取 https://api-docs.deepseek.com/zh-cn/guides/search
```

## 计费与限速（官方现行规则）

| 项目 | 数值 |
| --- | --- |
| 计费 | Search 与 Fetch **各 5 元 / 1000 次成功请求**（按请求次数，不按 token 或条数） |
| 不计费 | 请求失败、搜索无结果、超时 |
| 限速 | Search 100 QPS、Fetch 25 QPS（账号级），Fetch 对目标域名另有 QPS 限制 |

> 开了 `auto_fetch_top` 或模型传 `fetch_top` 时，每次搜索会多算 N 次 Fetch 请求。

## 错误处理

按官方文档：**408 / 429 / 5xx 自动退避重试（最多 3 次，尊重 `Retry-After`）**，其它 4xx 不重试。

| 状态码 | 含义 |
| --- | --- |
| 400 | 参数不合法（query 长度 / count 范围 / URL 格式） |
| 401 | API Key 无效 |
| 402 | 余额不足 |
| 403 | Fetch 被目标站点安全策略拒绝 |
| 404 | 抓不到可用内容（被拦截 / 无正文） |
| 413 | URL 超过 8192 字符 |
| 429 | 超出 QPS（重试后仍失败会提示稍后再试） |

## 为什么内置 `_vendor/` 而不是 pip install

要求用户 `pip install requests` 意味着换机器、换镜像、升级版本就可能 `ModuleNotFoundError`。所以依赖原样带上：

```text
_vendor/
├── requests/      2.34.2    Apache-2.0
├── urllib3/       2.7.0     MIT
├── certifi/       2026.07.22 MPL-2.0（含根证书）
├── idna/          3.18      BSD-3-Clause
└── _licenses/     各包 LICENSE 原文
```

加载顺序是 **宿主优先、内置兜底**（`sys.path.append`）：宿主有 requests 就用宿主的（不影响别的插件），没有就用插件自带这份。不写宿主 site-packages、不联网装包。

没有内置 `charset_normalizer`（可选编码探测库，官方 wheel 带平台相关 `.pyd`，不适合分发）；缺它只影响 requests 的自动编码探测，对 JSON 接口无影响，相关告警已在代码里屏蔽。

## 目录结构

```text
astrbot_plugin_zhen_deepseek_web/
├── main.py              # 全部逻辑：HTTP 客户端、格式化、Pillow 长图、指令、两个 LLM 工具
├── metadata.yaml        # 插件元数据（发布前改 author / repo）
├── _conf_schema.json    # 配置 Schema
├── requirements.txt     # 空（依赖已内置）
├── _vendor/             # requests 全家桶 + LICENSE
├── CHANGELOG.md
├── LICENSE
└── README.md
```

## 实测记录（AstrBot 4.28.1 / Python 3.12）

- 注册：3 个指令 + 2 个 LLM 工具；`py_compile` 通过
- 真实调用：Search 返回真实结果；Fetch 返回 Markdown 正文；无效 Key → `HTTP 401：API Key 无效`
- 二次抓取：`fetch_top=1` 正确把第一名结果正文附在搜索结果后
- 转图：把官网文档页（6405 字符）渲染成 1080×7018 PNG，中文/表格/代码块正常，无乱码、无溢出

## 常见问题

**Q：模型不调用联网工具？**
A：`/dsweb` 确认插件已加载；当前模型需支持 function calling；检查人格是否限制了工具列表。

**Q：长图里出现方块/乱码？**
A：系统缺中文字体。配置 `image_font` 指向一个 .ttf/.ttc，例如 `C:/Windows/Fonts/msyh.ttc`。

**Q：想把长图换成文字？**
A：配置 `fetch_output_mode` 改为 `text` 或 `both`。

**Q：会扣我的钱吗？**
A：会。5 元 / 1000 次成功请求，失败或无结果不计费。
