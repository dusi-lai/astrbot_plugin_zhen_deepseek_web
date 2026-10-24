# Changelog

## 1.2.0

### 新增
- **二次抓取**：搜索工具新增 `fetch_top` 参数（0~3），搜索后自动抓取排名靠前结果的正文一并返回；新增配置 `auto_fetch_top` / `auto_fetch_chars` 控制默认行为。
- **正文转长图**：`/抓取` 默认把 Markdown 正文用 Pillow 渲染成 PNG 长图发送（`fetch_output_mode` = text/image/both，可配宽度、字数、字体）。不依赖 playwright。
- Markdown 降级更完整：还原反斜杠转义、表格转行、去掉与标题重复的首标题、ASCII 单词不拆断。

### 变更
- 插件改名为 `astrbot_plugin_zhen_deepseek_web`，展示名「真·DeepSeek 联网搜索与抓取」，用于与市场里的同名插件区分（重名场景下的标识，不针对具体项目）。
- 状态指令增加二次抓取、输出模式信息。

## 1.0.0

- 首个版本：`deepseek_web_search` / `deepseek_web_fetch` 两个 LLM 工具，`/搜索`、`/抓取`、`/dsweb` 指令；
- 内置 requests 依赖；密钥可复用 AstrBot 已配置的 DeepSeek 提供商；按官方文档实现重试与错误码提示。
