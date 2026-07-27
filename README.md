# mpv-enjoy

因为喜欢 [mpv_PlayKit](https://github.com/hooke007/mpv_PlayKit)（mpv-lazy）配合
[uosc_danmaku](https://github.com/Tony15246/uosc_danmaku) 的播放体验，我参考并学习了
mpv_PlayKit，制作了这套面向 Windows 和 macOS 的开箱即用 mpv 整合包。

项目提供 Windows x64、macOS Apple Silicon 和 macOS Intel 三个独立版本，集成 mpv、完整
[uosc](https://github.com/tomasklaen/uosc)、uosc_danmaku、
[uosc_videotogether](https://github.com/HosamaJJF/uosc_videotogether)、thumbfast 与
yt-dlp。由于需要兼顾多个平台，未沿用 mpv_PlayKit 中的各类着色器整合；有需要时可自行
添加适合设备和平台的着色器与配置。

弹幕数据服务由[弹弹play开放弹幕网络](https://www.dandanplay.com/)提供。请按需调用服务，
不要将其用于批量抓取或下载弹幕数据库。

## 修改配置

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

uosc 控制栏提供 VideoTogether 一起看按钮。该功能只同步播放状态，不会传输本地媒体
文件；房间成员需要各自准备可播放的相同内容。如需额外设置快捷键，可在
`uosc_videotogether.conf` 中填写 `menu_key`。

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

推荐在 GitHub Actions 中手动运行 `Build` 工作流。也可以在对应的原生环境中构建：

```text
# Windows 10/11 x64：在 MSYS2 CLANG64 shell 中执行
./scripts/build-windows-msys2.sh

# Apple Silicon macOS
./scripts/build-macos.sh macos-arm64

# Intel macOS
./scripts/build-macos.sh macos-x64
```

Windows 脚本从源码编译 mpv、收集运行库并生成便携 ZIP。macOS 脚本从源码编译对应架构的
应用，仅生成 DMG，并使用 ad-hoc 身份签名。没有 Developer ID 公证时，首次启动可能
需要在 Finder 中右键选择“打开”，或前往“系统设置 → 隐私与安全性 → 仍要打开”。

## 本地校验

```text
python3 -m unittest discover -s tests -v

python3 scripts/verify_release.py \
  --platform windows-x64 \
  --release build/release/mpv-enjoy-1.1.2-windows-x64

python3 scripts/verify_release.py \
  --platform macos-arm64 \
  --release build/release/mpv-enjoy-1.1.2-macos-arm64

python3 scripts/verify_release.py \
  --platform macos-x64 \
  --release build/release/mpv-enjoy-1.1.2-macos-x64
```
