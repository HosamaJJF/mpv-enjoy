# License / 许可说明

本仓库的构建产物由多个上游项目组装而成，各组件分别遵循其自身许可证。
_未在本文件或文件内部单独声明许可证的 mpv-enjoy 自有内容，默认视为_ **UNLICENSED**。

UNLICENSED 表示保留所有权利，不因源码可见或产物可下载而自动授予复制、修改、分发或再许可
权利。第三方组件的既有许可证不受此声明影响。

## mpv-enjoy 自有内容

仓库中的配置、构建脚本、测试和文档，如未在文件内部另行声明，均为 **UNLICENSED**。

## 发行包组件

### mpv

- [Copyright 与许可证说明](https://github.com/mpv-player/mpv/blob/41f6a645068483470267271e1d09966ca3b9f413/Copyright)
- [GPL-2.0](https://github.com/mpv-player/mpv/blob/41f6a645068483470267271e1d09966ca3b9f413/LICENSE.GPL)
- [LGPL-2.1](https://github.com/mpv-player/mpv/blob/41f6a645068483470267271e1d09966ca3b9f413/LICENSE.LGPL)

### uosc

[LGPL-2.1-only](https://github.com/tomasklaen/uosc/blob/748bb238a962f8aecd0af51ee4b46651b58f6c51/LICENSE.LGPL)

### uosc_danmaku

[MIT](https://github.com/Tony15246/uosc_danmaku/blob/8fb2107d1e04ce1fd700496ca7d2e4a62182016a/LICENSE)

### thumbfast

[MPL-2.0](https://github.com/po5/thumbfast/blob/0f711de3138c9bd6718209d819ac54022c23ded2/LICENSE)

### yt-dlp

- [Unlicense](https://github.com/yt-dlp/yt-dlp/blob/2026.06.09/LICENSE)
- [官方可执行文件包含的第三方许可证](https://github.com/yt-dlp/yt-dlp/blob/2026.06.09/THIRD_PARTY_LICENSES.txt)

## mpv 运行库

mpv 构建会动态链接 FFmpeg、libass、libplacebo、LuaJIT 及构建环境提供的其他库；这些文件继续
遵循各自的上游许可证。每个发行包中的 `BUILD-DEPENDENCIES.txt` 记录实际构建版本，
`THIRD_PARTY_NOTICES.md` 提供组件概览，`LICENSES` 保存已收集的许可证全文，`sources` 保存
相应的上游源码归档。
