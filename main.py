"""DeepSeek 联网插件（Web Search + Web Fetch）。

把 DeepSeek 官方的两个联网接口接进 AstrBot：

- POST https://api.deepseek.com/v1/tools/search   —— 网页搜索（自研搜索引擎，结构化结果）
- POST https://api.deepseek.com/v1/tools/fetch    —— 网页抓取（返回 Markdown 正文）

对模型暴露两个函数工具（名称带 deepseek_ 前缀，避免和别的插件/MCP 工具撞名）：
- deepseek_web_search(query, count, need_content, ori_question)
- deepseek_web_fetch(url)

同时提供人工可用的指令：/搜索、/抓取、/dsweb（状态）。
本插件只向 api.deepseek.com 发请求，不上报任何数据，不记录 API Key。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

# ---------------------------------------------------------------------------
# 内置第三方库：requests / urllib3 / certifi / idna 直接随插件分发
# ---------------------------------------------------------------------------
# 目的：不依赖宿主 Python 环境里装没装 requests，插件复制过去就能跑。
# 用 append 而不是 insert(0)：宿主环境若已有 requests，优先用宿主的（避免影响别的插件），
# 只有在宿主缺失时才回退到插件内置的这份 - 保证「一定能用」且「不污染环境」。
_VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.append(str(_VENDOR_DIR))

# requests 在没有 charset_normalizer/chardet 时会给一条可选依赖告警，
# 这里只是编码探测能力缺失，不影响 JSON 接口，导入前先屏蔽掉。
warnings.filterwarnings(
    "ignore", message="Unable to find acceptable character detection dependency"
)

_REQUESTS_IMPORT_ERROR: str | None = None
try:
    import requests  # type: ignore
except Exception as _e:  # pragma: no cover - 只有内置库损坏才会走到
    requests = None  # type: ignore
    _REQUESTS_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

from pydantic import Field  # noqa: E402
from pydantic.dataclasses import dataclass  # noqa: E402

import astrbot.api.message_components as Comp  # noqa: E402
from astrbot.api import AstrBotConfig, logger  # noqa: E402
from astrbot.api.event import AstrMessageEvent, filter  # noqa: E402
from astrbot.api.star import Context, Star  # noqa: E402
from astrbot.core.agent.tool import FunctionTool, ToolExecResult  # noqa: E402

PLUGIN_VERSION = "1.2.3"
DEFAULT_BASE_URL = "https://api.deepseek.com"
SEARCH_PATH = "/v1/tools/search"
FETCH_PATH = "/v1/tools/fetch"

# 官方文档：408 / 429 / 5xx 可等待后重试；其它 4xx 不要重试
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_RETRY = 3

# 错误码 -> 人话（来自官方文档的错误与限速章节）
STATUS_HINTS = {
    400: "请求参数不合法（query 需 1~128 字符且不能全为空白；count 需 1~50）",
    401: "API Key 无效，请检查插件配置里的 api_key",
    402: "账号余额不足，请先充值",
    403: "Fetch 请求被目标站点的安全策略拒绝",
    404: "未能获取到可用的网页内容（URL 不可用、被站点拦截或没有可提取的正文）",
    408: "读取请求体超时，请稍后重试",
    413: "请求体超过大小限制（URL 长度需不超过 8192 字符）",
    429: "触发 QPS 限速（Search 100 次/秒、Fetch 25 次/秒，按账号计算），请稍后重试",
    500: "DeepSeek 服务端内部错误，请稍后重试",
    504: "DeepSeek 下游请求超时，请稍后重试",
}


class DeepSeekWebError(Exception):
    """联网接口调用失败。status_code 为 HTTP 状态码，网络异常时为 None。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class DeepSeekWebClient:
    """DeepSeek Search / Fetch 的同步客户端（requests 是阻塞库，调用方用 to_thread 包一层）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30,
        proxies: dict[str, str] | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self.timeout = max(5.0, float(timeout or 30))
        self.proxies = proxies or None

    # -- 内部 ------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"AstrBot-DeepSeekWeb/{PLUGIN_VERSION}",
        }

    @staticmethod
    def _wait_seconds(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(10.0, max(0.5, float(retry_after)))
            except ValueError:
                pass
        return min(8.0, 1.5 * (2**attempt))

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if requests is None:
            raise DeepSeekWebError(
                "内置的 requests 库未能加载"
                + (f"（{_REQUESTS_IMPORT_ERROR}）" if _REQUESTS_IMPORT_ERROR else "")
            )
        if not self.api_key:
            raise DeepSeekWebError(
                "未配置 DeepSeek API Key。请在插件配置里填写 api_key，"
                "或设置环境变量 DEEPSEEK_API_KEY，或在 AstrBot 里配置一个 DeepSeek 模型提供商。"
            )

        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(MAX_RETRY):
            try:
                resp = requests.post(
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=(10, self.timeout),
                    proxies=self.proxies,
                )
            except Exception as e:  # 连接错误、超时等
                last_error = e
                if attempt < MAX_RETRY - 1:
                    time.sleep(self._wait_seconds(attempt, None))
                    continue
                raise DeepSeekWebError(
                    f"网络请求失败：{type(e).__name__}: {e}"
                ) from e

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError as e:
                    raise DeepSeekWebError("接口返回了非 JSON 内容") from e
                if not isinstance(data, dict):
                    raise DeepSeekWebError("接口返回格式异常")
                return data

            retry_after = resp.headers.get("Retry-After")
            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_RETRY - 1:
                logger.debug(
                    f"[deepseek_web] {resp.status_code}，第 {attempt + 1} 次重试"
                )
                time.sleep(self._wait_seconds(attempt, retry_after))
                continue

            raise DeepSeekWebError(
                self._friendly_error(resp.status_code, resp), resp.status_code
            )

        raise DeepSeekWebError(
            f"请求失败：{type(last_error).__name__ if last_error else '未知错误'}: {last_error}"
        )

    @staticmethod
    def _friendly_error(status_code: int, resp: Any) -> str:
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    detail = str(err.get("message") or "").strip()
                elif err:
                    detail = str(err).strip()
        except Exception:
            detail = (resp.text or "").strip()[:200]

        hint = STATUS_HINTS.get(status_code, "请求失败")
        msg = f"HTTP {status_code}：{hint}"
        if detail:
            msg += f"｜详情：{detail}"
        request_id = None
        try:
            request_id = resp.headers.get("x-request-id") or resp.headers.get(
                "X-Request-Id"
            )
        except Exception:
            request_id = None
        if request_id:
            msg += f"｜request_id：{request_id}"
        return msg

    # -- 对外接口 --------------------------------------------------------
    def search(
        self,
        query: str,
        count: int = 10,
        need_content: bool = False,
        ori_question: str | None = None,
        localization: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "count": max(1, min(50, int(count))),
            "need_content": bool(need_content),
        }
        if ori_question:
            payload["ori_question"] = ori_question[:128]
        if localization:
            payload["localization"] = localization
        return self._post(SEARCH_PATH, payload)

    def fetch(self, url: str) -> dict[str, Any]:
        return self._post(FETCH_PATH, {"url": url})


# ---------------------------------------------------------------------------
# 结果格式化
# ---------------------------------------------------------------------------
def _clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    s = s.strip()
    if limit > 0 and len(s) > limit:
        return s[:limit].rstrip() + f"...（已截断，原长 {len(s)} 字符）"
    return s


def _fmt_date(value: Any) -> str:
    s = str(value or "").strip()
    return s[:10] if len(s) >= 10 else (s or "未知")


def format_search_results(
    data: dict[str, Any],
    query: str,
    *,
    count: int,
    max_summary: int = 800,
    max_content: int = 8000,
    total_limit: int = 12000,
    for_model: bool = True,
) -> str:
    results = data.get("results") or []
    if not isinstance(results, list) or not results:
        return f"没有搜到与「{query}」相关的结果。"

    header = f"搜索「{query}」共返回 {len(results)} 条结果："
    blocks: list[str] = [header]
    used = len(header)

    for i, item in enumerate(results[:count], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "无标题").strip()
        url = str(item.get("url") or "").strip()
        site = str(item.get("site_name") or "").strip()
        date = _fmt_date(item.get("date"))
        authority = item.get("authority")
        score = item.get("rerank_score")

        meta_bits = []
        if site:
            meta_bits.append(f"来源 {site}")
        meta_bits.append(f"时间 {date}")
        if authority is not None and authority != -1:
            meta_bits.append(f"权威度 {authority}/4")
        if score is not None:
            try:
                meta_bits.append(f"相关度 {float(score):.2f}")
            except (TypeError, ValueError):
                pass

        lines = [f"[{i}] {title}", f"URL: {url}", " | ".join(meta_bits)]

        summary = item.get("summary") or item.get("snippet")
        if summary:
            label = "摘要" if item.get("summary") else "短摘要"
            lines.append(f"{label}: {_clip(summary, max_summary)}")

        content = item.get("content")
        if for_model and content:
            lines.append(f"正文: {_clip(content, max_content)}")

        block = "\n".join(lines)
        if used + len(block) > total_limit:
            blocks.append(f"（结果过多，后面 {len(results) - i + 1} 条已省略）")
            break
        blocks.append(block)
        used += len(block) + 2

    return "\n\n".join(blocks)


def format_search_for_chat(
    data: dict[str, Any], query: str, max_items: int = 5, max_snippet: int = 160
) -> str:
    results = data.get("results") or []
    if not isinstance(results, list) or not results:
        return f"没有搜到与「{query}」相关的结果。"

    lines = [f"🔎 「{query}」共 {len(results)} 条结果："]
    for i, item in enumerate(results[:max_items], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "无标题").strip()
        url = str(item.get("url") or "").strip()
        site = str(item.get("site_name") or "").strip()
        date = _fmt_date(item.get("date"))
        snippet = _clip(item.get("snippet") or item.get("summary") or "", max_snippet)
        head = f"{i}. {title}"
        if site or date != "未知":
            head += f"（{' · '.join(x for x in [site, date] if x)}）"
        lines.append(head)
        if url:
            lines.append(url)
        if snippet:
            lines.append(snippet)
        lines.append("")

    if len(results) > max_items:
        lines.append(f"（还有 {len(results) - max_items} 条未显示）")
    return "\n".join(lines).strip()


def format_fetch_result(
    data: dict[str, Any], url: str, max_chars: int = 8000
) -> str:
    title = str(data.get("title") or "").strip() or "（无标题）"
    content = str(data.get("content") or "").strip()
    if not content:
        return f"抓取到「{title}」，但正文为空。"
    body = _clip(content, max_chars)
    return f"抓取成功：{title}\n来源: {url}\n正文长度: {len(content)} 字符\n\n{body}"



# ---------------------------------------------------------------------------
# Markdown 正文 -> 长图（用 Pillow，不依赖 playwright / 浏览器）
# ---------------------------------------------------------------------------
_FONT_CANDIDATES_REGULAR = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/NotoSansSC-VF.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
_FONT_CANDIDATES_BOLD = [
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/NotoSansSC-VF.ttf",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\u2B00-\u2BFF\uFE0F\u200D"
    "]"
)


def _load_font(size: int, bold: bool = False):
    """加载中文字体，找不到就退回 Pillow 内置字体（会变很小，但不会崩）。"""
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue
    return ImageFont.load_default()


def markdown_to_plain(md: str) -> str:
    """把 Markdown 粗略降级成适合绘制的纯文本（保留段落与标题层级感）。"""
    text = str(md or "").replace("\r\n", "\n").replace("\r", "\n")
    # DeepSeek 的 Markdown 会对 # - ( | : / _ 等做反斜杠转义，逐层还原
    for _ in range(3):
        new_text = re.sub(r"\\([^\w\s]|_)", r"\1", text)
        if new_text == text:
            break
        text = new_text
    text = re.sub(r"```[a-zA-Z0-9_+-]*\n?", "", text)  # 代码围栏标记
    text = _MD_IMAGE_RE.sub("", text)  # 图片
    text = _MD_LINK_RE.sub(lambda m: m.group(1) or m.group(2), text)  # 链接保留文字
    text = _EMOJI_RE.sub("", text)  # emoji 字体不一定有，去掉避免豆腐块
    text = re.sub(r"<[^>]+>", "", text)  # HTML 标签

    out: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        if _HEADING_RE.match(line):
            heading = _HEADING_RE.sub("", line).strip()
            if heading:
                if out and out[-1].strip():
                    out.append("")
                out.append("■ " + heading)
                out.append("")
            continue
        stripped = line.strip()
        if "|" in stripped and len(stripped) > 2:
            if re.fullmatch(r"[|\-\s:]+", stripped):  # 表格分隔行，直接丢弃
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                line = " | ".join(cells)
        line = re.sub(r"^\s*[-*+]\s+", "· ", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = line.replace("**", "").replace("__", "").replace("`", "")
        out.append(line)

    # 折叠连续空行
    folded: list[str] = []
    for line in out:
        if not line.strip() and folded and not folded[-1].strip():
            continue
        folded.append(line)
    return "\n".join(folded).strip()


def _text_width(font: Any, text: str) -> float:
    try:
        return float(font.getlength(text))
    except Exception:
        return float(len(text)) * 14.0


_WRAP_UNIT_RE = re.compile(r"[A-Za-z0-9_@:/%#.+=?'\"&~-]+|\s+|.", re.S)


def _wrap_lines(text: str, font: Any, max_width: float) -> list[str]:
    """按像素宽度折行：中文逐字断，英文单词尽量整体保留，超宽才硬断。"""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        current = ""
        for unit in _WRAP_UNIT_RE.findall(para):
            if unit.isspace():
                if current:
                    current += " "
                continue
            trial = current + unit
            if _text_width(font, trial) <= max_width:
                current = trial
                continue
            if current:
                lines.append(current.rstrip())
                current = ""
            if _text_width(font, unit) > max_width:  # 超长单词/URL 硬断
                buf = ""
                for ch in unit:
                    if _text_width(font, buf + ch) <= max_width:
                        buf += ch
                    else:
                        lines.append(buf)
                        buf = ch
                current = buf
            else:
                current = unit
        if current:
            lines.append(current.rstrip())
    return lines


def render_markdown_to_png(
    title: str,
    url: str,
    content: str,
    out_path: Path,
    *,
    width: int = 1080,
    max_chars: int = 6000,
    max_height: int = 16000,
    font_path: str = "",
) -> Path:
    """把抓取到的 Markdown 正文画成一张长图，返回图片路径。"""
    from PIL import Image, ImageDraw

    global _FONT_CANDIDATES_REGULAR, _FONT_CANDIDATES_BOLD
    if font_path:
        _FONT_CANDIDATES_REGULAR = [font_path] + _FONT_CANDIDATES_REGULAR
        _FONT_CANDIDATES_BOLD = [font_path] + _FONT_CANDIDATES_BOLD

    plain = markdown_to_plain(content)

    # 正文首个标题通常就是页面标题，去掉避免与图片标题重复
    title_norm = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", (title or "").lower())
    plain_lines = plain.split("\n")
    checked = 0
    for idx, line in enumerate(plain_lines):
        if not line.strip():
            continue
        checked += 1
        if checked > 5:
            break
        if line.startswith("■ ") and title_norm:
            if re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", line[2:].lower()) == title_norm:
                plain_lines = plain_lines[idx + 1 :]
                break
    plain = "\n".join(plain_lines).lstrip("\n")

    if not plain.strip():
        plain = "（正文为空）"
    truncated = len(plain) > max_chars
    if truncated:
        plain = plain[:max_chars]

    margin = 48
    inner_w = width - margin * 2
    title_size, meta_size, body_size = 40, 22, 28
    title_font = _load_font(title_size, bold=True)
    meta_font = _load_font(meta_size)
    body_font = _load_font(body_size)
    line_h = int(body_size * 1.62)
    title_line_h = int(title_size * 1.35)

    title_lines = _wrap_lines(title or "（无标题）", title_font, inner_w)
    body_lines = _wrap_lines(plain, body_font, inner_w)

    header_h = margin + len(title_lines) * title_line_h + 14 + meta_size + 28 + 18 + 28
    footer_h = 56
    max_body_lines = max(1, (max_height - header_h - footer_h) // line_h)
    if len(body_lines) > max_body_lines:
        body_lines = body_lines[: max_body_lines - 1] + ["……（内容过长，已截断）"]
        truncated = True

    height = header_h + len(body_lines) * line_h + footer_h
    height = max(600, min(max_height, height))

    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # 左侧强调色条 + 顶部标题区
    accent = (77, 107, 254)  # DeepSeek 蓝
    draw.rectangle([0, 0, 8, height], fill=accent)

    y = margin
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=(24, 24, 27))
        y += title_line_h

    y += 14
    meta = f"{url}"
    if truncated:
        meta += "  ·  已截断"
    draw.text((margin, y), meta[:160], font=meta_font, fill=(120, 120, 130))
    y += meta_size + 20

    draw.line([(margin, y), (width - margin, y)], fill=(232, 232, 236), width=2)
    y += 28

    for line in body_lines:
        if line:
            draw.text((margin, y), line, font=body_font, fill=(38, 38, 42))
        y += line_h

    created = time.strftime("%Y-%m-%d %H:%M")
    draw.line([(margin, height - 44), (width - margin, height - 44)], fill=(240, 240, 244), width=1)
    draw.text(
        (margin, height - 36),
        f"DeepSeek Fetch · AstrBot 插件  ·  {created}",
        font=meta_font,
        fill=(150, 150, 158),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return out_path


# ---------------------------------------------------------------------------
# 插件
# ---------------------------------------------------------------------------
class DeepSeekWebPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}

        # 用 self（而不是提前构造的 client）作为工具的 runner，
        # 这样每次调用才解析密钥，配置改了不用重载插件也能生效。
        try:
            # handler_module_path 必须填本模块名：AstrBot 靠它把工具归属到插件，
            # 才能在插件重载/停用/卸载时正确清理和置灰，避免残留旧工具。
            self.context.add_llm_tools(
                DeepSeekWebSearchTool(runner=self, handler_module_path=__name__),
                DeepSeekWebFetchTool(runner=self, handler_module_path=__name__),
            )
            logger.info(
                f"[deepseek_web] v{PLUGIN_VERSION} 已注册 LLM 工具："
                "deepseek_web_search / deepseek_web_fetch"
            )
        except Exception as e:
            logger.error(f"[deepseek_web] 注册 LLM 工具失败：{type(e).__name__}: {e}")

    async def initialize(self) -> None:
        if _REQUESTS_IMPORT_ERROR:
            logger.error(
                f"[deepseek_web] 内置 requests 加载失败：{_REQUESTS_IMPORT_ERROR}"
            )
        if not self._resolve_api_key():
            logger.warning(
                "[deepseek_web] 尚未获得 DeepSeek API Key：请在插件配置填写 api_key，"
                "或设置环境变量 DEEPSEEK_API_KEY，或先在 AstrBot 配置一个 DeepSeek 提供商。"
            )

    async def terminate(self) -> None:
        logger.info("[deepseek_web] 插件已停用")

    # -- 配置与密钥 ------------------------------------------------------
    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _resolve_api_key(self) -> str:
        key = str(self._cfg("api_key", "") or "").strip()
        if key:
            return key

        key = str(os.environ.get("DEEPSEEK_API_KEY", "") or "").strip()
        if key:
            return key

        # 兜底：复用 AstrBot 里已经配置好的 DeepSeek 提供商密钥，省得填两遍
        try:
            astrbot_cfg = self.context.get_config()
            sources = astrbot_cfg.get("provider_sources", []) or []
            for src in sources:
                if not isinstance(src, dict):
                    continue
                if not src.get("enable", True):
                    continue
                provider = str(src.get("provider") or "").lower()
                api_base = str(src.get("api_base") or "").lower()
                if "deepseek" not in provider and "deepseek" not in api_base:
                    continue
                keys = src.get("key")
                if isinstance(keys, list):
                    for k in keys:
                        if str(k or "").strip():
                            return str(k).strip()
                elif isinstance(keys, str) and keys.strip():
                    return keys.strip()
        except Exception as e:
            logger.debug(f"[deepseek_web] 读取 AstrBot 提供商配置失败：{e}")

        return ""

    def _resolve_proxies(self) -> dict[str, str] | None:
        proxy = str(self._cfg("proxy", "") or "").strip()
        if not proxy:
            try:
                proxy = str(self.context.get_config().get("http_proxy", "") or "").strip()
            except Exception:
                proxy = ""
        if not proxy:
            return None
        return {"http": proxy, "https": proxy}

    def _build_client(self) -> DeepSeekWebClient:
        return DeepSeekWebClient(
            api_key=self._resolve_api_key(),
            base_url=str(self._cfg("base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL),
            timeout=float(self._cfg("timeout", 30) or 30),
            proxies=self._resolve_proxies(),
        )

    # -- 图片输出 --------------------------------------------------------
    def _plugin_data_dir(self) -> Path:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            base = Path(get_astrbot_data_path())
        except Exception:
            base = Path(tempfile.gettempdir()) / "astrbot_data"
        folder = base / "plugin_data" / (getattr(self, "name", None) or "astrbot_plugin_zhen_deepseek_web") / "images"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _image_out_path(self) -> Path:
        """生成本次图片路径，顺带清理旧图（只留最近 30 张）。"""
        folder = self._plugin_data_dir()
        try:
            files = sorted(
                folder.glob("fetch_*.png"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in files[30:]:
                old.unlink(missing_ok=True)
        except Exception:
            pass
        return folder / f"fetch_{int(time.time())}_{uuid4().hex[:6]}.png"

    # -- 供 LLM 工具调用 -------------------------------------------------
    async def search_for_model(
        self,
        query: str,
        count: int | None = None,
        need_content: bool | None = None,
        ori_question: str | None = None,
        fetch_top: int | None = None,
    ) -> ToolExecResult:
        query = str(query or "").strip()
        if not query:
            return "搜索失败：query 不能为空。"
        if len(query) > 128:
            query = query[:128]

        count = int(count if count is not None else self._cfg("search_count", 8))
        count = max(1, min(50, count))
        if need_content is None:
            need_content = bool(self._cfg("need_content", False))

        client = self._build_client()
        try:
            data = await asyncio.to_thread(
                client.search,
                query=query,
                count=count,
                need_content=need_content,
                ori_question=(ori_question or None),
            )
        except DeepSeekWebError as e:
            logger.warning(f"[deepseek_web] search 失败：{e.message}")
            return f"搜索失败：{e.message}"
        except Exception as e:
            logger.error(f"[deepseek_web] search 异常：{type(e).__name__}: {e}")
            return f"搜索失败：{type(e).__name__}: {e}"

        results = data.get("results") or []
        logger.info(f"[deepseek_web] 搜索 {query!r} -> {len(results)} 条")

        text = format_search_results(
            data,
            query,
            count=count,
            max_summary=int(self._cfg("max_summary_chars", 800) or 800),
            max_content=int(self._cfg("max_content_chars", 8000) or 8000),
            total_limit=int(self._cfg("max_tool_output_chars", 12000) or 12000),
            for_model=True,
        )

        # 二次检索：把排名靠前的几条结果正文直接抓回来，省掉模型再发一轮工具调用
        top_n = fetch_top if fetch_top is not None else self._cfg("auto_fetch_top", 0)
        try:
            top_n = max(0, min(3, int(top_n or 0)))
        except (TypeError, ValueError):
            top_n = 0
        if top_n > 0 and isinstance(results, list) and results:
            text += await self._append_fetched_pages(client, results, top_n)
        return text

    async def _append_fetched_pages(
        self, client: DeepSeekWebClient, results: list[Any], top_n: int
    ) -> str:
        """并发抓取前 top_n 条结果正文，拼到搜索结果后面。"""
        picked: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            picked.append(item)
            if len(picked) >= top_n:
                break
        if not picked:
            return ""

        per_cap = int(self._cfg("auto_fetch_chars", 4000) or 4000)
        logger.info(f"[deepseek_web] 二次抓取 {len(picked)} 条结果正文")
        outputs = await asyncio.gather(
            *[asyncio.to_thread(client.fetch, item["url"]) for item in picked],
            return_exceptions=True,
        )

        blocks = [f"\n\n—— 已自动抓取排名前 {len(picked)} 条的网页正文 ——"]
        for item, out in zip(picked, outputs):
            url = str(item.get("url") or "")
            title = str(item.get("title") or "").strip()
            if isinstance(out, Exception):
                blocks.append(f"\n【正文抓取失败】{title}\nURL: {url}\n原因: {out}")
                continue
            page_title = str(out.get("title") or title or "（无标题）").strip()
            content = str(out.get("content") or "").strip()
            blocks.append(
                f"\n【正文】{page_title}\nURL: {url}\n{_clip(content, per_cap)}"
            )
        return "\n".join(blocks)

    async def fetch_for_model(self, url: str) -> ToolExecResult:
        url = self._normalize_url(url)
        if not url:
            return (
                "抓取失败：url 无效。必须是含 host 的 http/https 地址，"
                "长度不超过 8192，且不能带用户名/密码。"
            )

        client = self._build_client()
        try:
            data = await asyncio.to_thread(client.fetch, url)
        except DeepSeekWebError as e:
            logger.warning(f"[deepseek_web] fetch 失败：{e.message}")
            return f"抓取失败：{e.message}"
        except Exception as e:
            logger.error(f"[deepseek_web] fetch 异常：{type(e).__name__}: {e}")
            return f"抓取失败：{type(e).__name__}: {e}"

        logger.info(f"[deepseek_web] 抓取 {url}")
        return format_fetch_result(
            data,
            url,
            max_chars=int(self._cfg("max_content_chars", 8000) or 8000),
        )

    # -- 指令 ------------------------------------------------------------
    @filter.command("搜索", alias={"ws", "websearch"})
    async def cmd_search(self, event: AstrMessageEvent):
        """联网搜索：/搜索 关键词（支持 site: 等语法）"""
        if not bool(self._cfg("enable_commands", True)):
            yield event.plain_result("插件配置里已关闭文本指令，可直接让模型调用联网工具。")
            return

        query = self._command_args(event, {"搜索", "ws", "websearch"})
        if not query:
            yield event.plain_result(
                "用法：/搜索 关键词\n"
                "支持搜索语法：site:github.com、-site:xxx、\"精确短语\"、lang:zh,en、after:2025 等。"
            )
            return

        client = self._build_client()
        count = max(1, min(50, int(self._cfg("search_count", 8) or 8)))
        try:
            data = await asyncio.to_thread(
                client.search, query=query, count=count, need_content=False
            )
        except DeepSeekWebError as e:
            yield event.plain_result(f"搜索失败：{e.message}")
            return
        except Exception as e:
            logger.error(f"[deepseek_web] 指令搜索异常：{e}")
            yield event.plain_result(f"搜索失败：{type(e).__name__}: {e}")
            return

        yield event.plain_result(
            format_search_for_chat(data, query, max_items=min(5, count))
        )

    @filter.command("抓取", alias={"wf", "webfetch"})
    async def cmd_fetch(self, event: AstrMessageEvent):
        """抓取网页：/抓取 https://example.com/article"""
        if not bool(self._cfg("enable_commands", True)):
            yield event.plain_result("插件配置里已关闭文本指令。")
            return

        raw = self._command_args(event, {"抓取", "wf", "webfetch"})
        url = self._normalize_url(raw)
        if not url:
            yield event.plain_result("用法：/抓取 https://example.com/article")
            return

        client = self._build_client()
        try:
            data = await asyncio.to_thread(client.fetch, url)
        except DeepSeekWebError as e:
            yield event.plain_result(f"抓取失败：{e.message}")
            return
        except Exception as e:
            logger.error(f"[deepseek_web] 指令抓取异常：{e}")
            yield event.plain_result(f"抓取失败：{type(e).__name__}: {e}")
            return

        preview_chars = int(self._cfg("chat_preview_chars", 1200) or 1200)
        title = str(data.get("title") or "").strip() or "（无标题）"
        content = str(data.get("content") or "").strip()
        preview = _clip(content, preview_chars) if content else "（无正文）"
        header = f"📄 {title}\n{url}\n正文 {len(content)} 字符"

        mode = str(self._cfg("fetch_output_mode", "image") or "image").strip().lower()
        if mode not in ("text", "image", "both"):
            mode = "image"

        if mode in ("image", "both") and content:
            image_path = None
            try:
                out_path = self._image_out_path()
                image_path = await asyncio.to_thread(
                    render_markdown_to_png,
                    title,
                    url,
                    content,
                    out_path,
                    width=int(self._cfg("image_width", 1080) or 1080),
                    max_chars=int(self._cfg("image_max_chars", 6000) or 6000),
                    font_path=str(self._cfg("image_font", "") or ""),
                )
            except Exception as e:
                logger.error(f"[deepseek_web] 正文转图失败：{type(e).__name__}: {e}")
                image_path = None

            if image_path is not None:
                chain: list[Any] = [Comp.Plain(header)]
                if mode == "both" and preview:
                    chain.append(Comp.Plain("\n\n" + preview))
                chain.append(Comp.Image.fromFileSystem(str(image_path)))
                yield event.chain_result(chain)
                return
            logger.warning("[deepseek_web] 转图失败，回退为文字预览")

        yield event.plain_result(f"{header}\n\n{preview}")

    @filter.command("dsweb", alias={"联网状态"})
    async def cmd_status(self, event: AstrMessageEvent):
        """查看 DeepSeek 联网插件状态"""
        key = self._resolve_api_key()
        if key:
            masked = f"{key[:3]}****{key[-4:]}" if len(key) > 8 else "****"
            key_state = f"已配置（{masked}）"
        else:
            key_state = "未配置"

        proxies = self._resolve_proxies()
        lines = [
            f"DeepSeek 联网插件 v{PLUGIN_VERSION}",
            f"接口: {self._cfg('base_url', DEFAULT_BASE_URL)}",
            f"API Key: {key_state}",
            f"默认条数: {self._cfg('search_count', 8)}｜超时: {self._cfg('timeout', 30)}s",
            f"二次抓取: 自动抓前 {self._cfg('auto_fetch_top', 0)} 条正文",
            f"抓取输出: {self._cfg('fetch_output_mode', 'image')}",
            f"代理: {(proxies or {}).get('https', '未设置（跟随环境变量）')}",
            f"内置 requests: {'可用' if requests is not None else '不可用'}",
            "计费: Search / Fetch 各 5 元 / 1000 次成功请求",
            "工具: deepseek_web_search、deepseek_web_fetch",
        ]
        yield event.plain_result("\n".join(lines))

    # -- 工具函数 --------------------------------------------------------
    @staticmethod
    def _command_args(event: AstrMessageEvent, names: set[str]) -> str:
        """从 message_str 里取出指令名之后的全部文本（指令名可能带 / 或已被唤醒前缀处理）。"""
        text = str(getattr(event, "message_str", "") or "").strip()
        if not text:
            return ""
        parts = text.split(maxsplit=1)
        head = parts[0].strip().lstrip("/!！／")
        if head in names:
            return parts[1].strip() if len(parts) > 1 else ""
        return ""

    @staticmethod
    def _normalize_url(raw: str) -> str:
        """校验并补全 URL：必须是含 host 的 http/https 地址，且不能带用户名/密码。"""
        url = str(raw or "").strip().strip("<>\"'").strip()
        if not url or len(url) > 8192:
            return ""
        if "://" not in url:
            url = "https://" + url.lstrip("/")
        try:
            parts = urlsplit(url)
        except ValueError:
            return ""
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        if parts.username or parts.password:
            return ""
        return url


# ---------------------------------------------------------------------------
# LLM 工具定义（pydantic dataclass 形式，参数 schema 手写，不依赖 docstring 解析）
# ---------------------------------------------------------------------------
@dataclass
class DeepSeekWebSearchTool(FunctionTool):
    name: str = "deepseek_web_search"
    description: str = (
        "联网搜索网页。使用 DeepSeek 自研搜索引擎，返回标题、URL、站点、发布时间、"
        "权威度与摘要，适合查询最新资讯、新闻、事实核查、技术文档等实时信息。"
        "query 支持搜索语法：site:github.com、-site:example.com、"
        '"精确短语"、lang:zh,en、after:2025、before:2026-06-01。'
        "need_content=true 时同时返回网页正文；fetch_top=1~3 会自动抓取排名靠前的结果正文一并返回，"
        "适合需要原文细节、或结果摘要不足以回答时使用（每条额外一次 Fetch 计费）。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词，1~128 个字符，支持 site:/lang:/after: 等语法。",
                },
                "count": {
                    "type": "integer",
                    "description": "返回条数，1~50，默认取插件配置（通常 8）。",
                },
                "need_content": {
                    "type": "boolean",
                    "description": "是否同时返回网页正文（Markdown），默认 false。",
                },
                "fetch_top": {
                    "type": "integer",
                    "description": "二次抓取前 N 条结果（0~3，默认 0）的网页正文并一起返回，适合需要原文细节时。会额外产生 N 次 Fetch 计费。",
                },
                "ori_question": {
                    "type": "string",
                    "description": "触发本次搜索的用户原始问题，有助于理解搜索意图，可选。",
                },
            },
            "required": ["query"],
        }
    )
    runner: Any = None

    async def call(self, context: Any, **kwargs) -> ToolExecResult:
        return await self.runner.search_for_model(
            query=kwargs.get("query", ""),
            count=kwargs.get("count"),
            need_content=kwargs.get("need_content"),
            ori_question=kwargs.get("ori_question"),
            fetch_top=kwargs.get("fetch_top"),
        )


@dataclass
class DeepSeekWebFetchTool(FunctionTool):
    name: str = "deepseek_web_fetch"
    description: str = (
        "抓取指定网页，返回页面标题与 Markdown 格式正文。"
        "适合在搜索结果里挑出一个 URL 后深入阅读全文，或读取用户给出的链接。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页地址，必须是完整的 http/https URL。",
                },
            },
            "required": ["url"],
        }
    )
    runner: Any = None

    async def call(self, context: Any, **kwargs) -> ToolExecResult:
        return await self.runner.fetch_for_model(kwargs.get("url", ""))
