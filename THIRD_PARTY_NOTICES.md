# 第三方依赖声明

本插件的 `_vendor/` 目录随包分发以下第三方库（原样副本，未做修改），
其许可证原文存放在 `_vendor/_licenses/`，点击下面链接可对照 PyPI 官方包。

| 包 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| requests | 2.34.2 | Apache-2.0 | HTTP 客户端 |
| urllib3 | 2.7.0 | MIT | requests 的底层连接池 |
| certifi | 2026.07.22 | MPL-2.0 | 内置 CA 根证书（cacert.pem） |
| idna | 3.18 | BSD-3-Clause | 国际化域名处理 |

为什么内置而不是写进 requirements.txt：
AstrBot 插件运行在 AstrBot 自带的 Python 中，要求用户 `pip install requests`
会在换机器 / 换镜像 / 升级时出现 ModuleNotFoundError。插件用
`sys.path.append(_vendor)` 的方式做「宿主优先、内置兜底」，不写宿主
site-packages、不联网装包。

许可证兼容性：Apache-2.0 / MIT / BSD-3-Clause / MPL-2.0 均与本项目的 MIT
许可兼容；各包版权声明保留在 `_vendor/_licenses/` 对应文件中。
