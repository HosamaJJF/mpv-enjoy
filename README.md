# mpv-enjoy

因为喜欢 [mpv_PlayKit](https://github.com/hooke007/mpv_PlayKit)（mpv-lazy）配合
[uosc_danmaku](https://github.com/Tony15246/uosc_danmaku) 的播放体验，我参考并学习了
mpv_PlayKit，制作了这套面向 Windows 和 macOS 的开箱即用 mpv 整合包。

项目提供 Windows x64、macOS Apple Silicon 和 macOS Intel 三个独立版本。统一入口使用
[mpv-enjoy Home](https://github.com/HosamaJJF/mpv-enjoy-home) 浏览本地媒体及
Emby/Jellyfin，播放仍由同一发行包内的独立 mpv 进程负责。发行包还集成完整
[uosc](https://github.com/tomasklaen/uosc)、uosc_danmaku、
[uosc_videotogether](https://github.com/HosamaJJF/uosc_videotogether)、thumbfast 与
yt-dlp。由于需要兼顾多个平台，未沿用 mpv_PlayKit 中的各类着色器整合；有需要时可自行
添加适合设备和平台的着色器与配置。

弹幕数据服务由[弹弹play开放弹幕网络](https://www.dandanplay.com/)提供。请按需调用服务，
不要将其用于批量抓取或下载弹幕数据库。

## 修改配置

Windows 解压后运行 `mpv-enjoy.exe`，macOS 打开 `mpv-enjoy.app`。需要绕过首页直接播放时，
Windows 仍可运行同目录的 `mpv.exe`；macOS 的播放器由首页通过包内受管启动器调用。

Windows 版本的配置位于程序目录下的 `portable_config`。推荐把自定义 mpv 选项写入
`portable_config/user.conf`；快捷键、uosc、弹幕和一起看插件配置分别位于
`portable_config/input.conf`、`portable_config/script-opts/uosc.conf`、
`portable_config/script-opts/uosc_danmaku.conf` 和
`portable_config/script-opts/uosc_videotogether.conf`。

macOS 版本首次启动后会在 `~/Library/Application Support/mpv-enjoy/config` 初始化配置。
推荐把自定义 mpv 选项写入其中的 `user.conf`；快捷键、uosc、弹幕和一起看插件配置分别
位于同一目录下的 `input.conf`、`script-opts/uosc.conf`、
`script-opts/uosc_danmaku.conf` 和
`script-opts/uosc_videotogether.conf`。Apple Silicon 与 Intel 版本使用相同的配置目录。

uosc 控制栏提供弹幕搜索、开关、设置和 VideoTogether 一起看按钮。一起看功能只同步
播放状态，不会传输本地媒体文件；房间成员需要各自准备可播放的相同内容。如需额外设置
快捷键，可在 `uosc_videotogether.conf` 中填写 `menu_key`。

在画面上点击右键或中键打开 uosc 菜单，再选择“音画同步”，可分别调整字幕延迟和音频
延迟。键盘也可用 `z`/`x` 调整字幕延迟、`c`/`v` 调整音频延迟，或用
`Shift+Backspace` 将两者同时归零。

macOS 升级安装会保留已有的 `uosc.conf`。老用户如未看到新的“弹幕设置”按钮，可在
`controls` 中把 `button:danmaku` 改为 `button:danmaku,button:danmaku_menu`，或备份
自定义内容后删除 `script-opts/uosc.conf` 并重启应用，以重新生成默认配置。

## 软件更新

首页启动时会静默检查 mpv-enjoy 的最新正式 Release，也可在设置页手动检查并查看更新说明。
下载前会严格匹配当前平台的附件名称和 GitHub Release 地址，并核对声明大小与 SHA-256；
不匹配或缺少摘要的附件不会被打开或执行。

Windows 版会下载并定位便携 ZIP。请先退出首页和播放器，备份 `portable_config` 中的自定义
内容，再手动解压覆盖原目录；不要在程序运行时覆盖文件。macOS 版会下载并打开对应架构的
DMG，请将新版本拖入“应用程序”完成安装，应用支持目录中的已有用户配置会继续保留。

## 构建

正式构建必须提供 mpv-enjoy 专用的弹弹play AppId 和 AppSecret 密文。密文格式与锁定的
uosc_danmaku 上游实现一致，仅用于避免在仓库和构建日志中直接出现明文；由于解密代码和
密钥都会随客户端分发，它不构成对发行包使用者的秘密保护。

在可信本机上通过 OpenSSL 交互生成密文：

```text
python3 scripts/encode_dandanplay_credentials.py
```

脚本不会通过命令行传递明文，并会复现锁定版 uosc_danmaku 的实际运行时密钥行为；
不要根据上游 `main.lua` 中的表面密钥手工生成密文。更新本脚本后应重新生成密文，不能
沿用旧版脚本的输出。把输出的前两个值分别保存为 GitHub
`release-credentials` Environment 中的以下 secrets：

```text
MPV_ENJOY_DANDANPLAY_APP_ID_AES_B64
MPV_ENJOY_DANDANPLAY_APP_SECRET_AES_B64
```

不要提交输出值或原始 AppSecret。GitHub Actions 的三个平台构建都会在缺少、格式错误或
仍为上游凭据时失败。本地构建时，应在当前 shell 中导出同名环境变量，并保持到组装和
`verify_release.py` 验证完成。

推荐在发布准备分支上运行三平台 `Build` 验证；合并到 `main` 后只运行快速 `CI`，推送
`v<版本>` 标签时再由 `Build` 从源码重新构建三个正式产物并发布 Release。分支上的连续
推送会取消同一分支尚未完成的旧构建。也可以在对应的原生环境中手动构建，或在 GitHub
Actions 中手动运行 `Build`：

```text
# Windows 10/11 x64：在 MSYS2 CLANG64 shell 中执行
./scripts/build-windows-msys2.sh

# Apple Silicon macOS
./scripts/build-macos.sh macos-arm64

# Intel macOS
./scripts/build-macos.sh macos-x64
```

构建还需要 Node.js 22 和 Rust 1.92；首页源码与其他组件一样由
`dependencies.lock.json` 固定并在构建时下载。Windows 脚本从源码构建首页和 mpv、收集
运行库并生成便携 ZIP。macOS 脚本从源码构建首页和对应架构的 mpv，仅生成 DMG，并在完成
应用组装后统一使用 ad-hoc 身份签名。没有 Developer ID 公证时，首次启动可能
需要在 Finder 中右键选择“打开”，或前往“系统设置 → 隐私与安全性 → 仍要打开”。

## 本地校验

```text
python3 -m unittest discover -s tests -v

python3 scripts/verify_release.py \
  --platform windows-x64 \
  --release build/release/mpv-enjoy-1.2.3-windows-x64

python3 scripts/verify_release.py \
  --platform macos-arm64 \
  --release build/release/mpv-enjoy-1.2.3-macos-arm64

python3 scripts/verify_release.py \
  --platform macos-x64 \
  --release build/release/mpv-enjoy-1.2.3-macos-x64
```
