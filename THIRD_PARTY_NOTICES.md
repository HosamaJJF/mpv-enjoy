# Third-party notices

mpv-enjoy 的自有配置、构建脚本和文档采用 MIT 许可证。该许可不覆盖随包分发的第三方程序。

| 组件 | 固定版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| mpv | 0.41.0 | GPL-2.0-or-later / LGPL-2.1-or-later 混合代码库 | 播放器主体；本项目按 GPL 兼容方式分发构建产物 |
| uosc | 5.12.0 | LGPL-2.1-only | 完整 UI 脚本、字体与 `ziggy` 辅助程序 |
| uosc_danmaku | 2.2.0，提交 `8fb2107` | MIT | 弹幕搜索、匹配、渲染和 uosc 菜单 |
| thumbfast | 提交 `0f711de` | MPL-2.0 | 时间轴缩略图生成 |
| yt-dlp | 2026.06.09 | Unlicense；独立可执行文件还包含其声明的第三方许可 | 网络媒体解析 |

精确提交、下载地址和 SHA-256 位于 `dependencies.lock.json`。上游许可证全文位于发行包的
`LICENSES` 目录，相应源码归档位于 `sources` 目录。uosc 与 uosc_danmaku 的包内更新入口经过
修改，只提示更新整个 mpv-enjoy 包；此修改不改变其原许可证。

mpv 会动态链接 FFmpeg、libass、libplacebo、LuaJIT 及平台构建环境提供的其他库。实际构建所用
包版本记录在发行包的 `BUILD-DEPENDENCIES.txt`；这些库各自保留其上游许可证。公开再分发者应
保留本文件、完整许可证、SBOM、精确构建清单及对应源码。CI 依赖仓库是当前首版中尚未完全
冻结的部分，因此正式长期归档前还应把清单中每个二进制依赖的源码镜像一并保存。

本文件是工程合规记录，不构成法律意见。
