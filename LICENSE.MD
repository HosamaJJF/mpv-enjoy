# License / 许可说明

## mpv-enjoy 自有内容

除下文列出的第三方内容外，mpv-enjoy 的自有配置、构建脚本、测试和文档均采用
MIT License。

MIT License

Copyright (c) 2026 mpv-enjoy contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 第三方内容

本仓库与发行包整合了多个上游项目，这些内容不适用 mpv-enjoy 的 MIT
License，而是分别遵循其上游许可证：

| 组件 | 上游项目 | 许可证 |
| --- | --- | --- |
| mpv | [mpv-player/mpv](https://github.com/mpv-player/mpv) | GPL-2.0-or-later / LGPL-2.1-or-later 混合代码库 |
| uosc | [tomasklaen/uosc](https://github.com/tomasklaen/uosc) | LGPL-2.1-only |
| uosc_danmaku | [Tony15246/uosc_danmaku](https://github.com/Tony15246/uosc_danmaku) | MIT |
| thumbfast | [po5/thumbfast](https://github.com/po5/thumbfast) | MPL-2.0 |
| yt-dlp | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense 及其二进制文件声明的第三方许可证 |

上表不锁定组件版本。当前构建使用的精确版本、下载地址、SHA-256 和 SPDX
许可标识以 `dependencies.lock.json` 与发行包中的 `SBOM.spdx.json` 为准。
发行包的 `LICENSES` 目录保存所集成版本的许可证原文，`sources` 目录保存对应
上游源码归档。

mpv 还会链接 FFmpeg、libass、libplacebo、LuaJIT 及平台构建环境提供的其他库。
实际构建所用依赖记录在发行包的 `BUILD-DEPENDENCIES.txt` 中，各依赖保留其自身的
上游许可证。
