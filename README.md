# mpv-lazy-enjoy

面向 Windows 10/11 x64 与 macOS 14+ Apple Silicon 的 mpv 整合包实现。它参考本地旧配置的操作
习惯，但从锁定的最新上游源码重新组装，不复制旧包的缓存、历史、私有路径、许可不明脚本、
VapourSynth 环境、外置 GLSL 着色器或厂商专用 GPU 组件。

## 当前锁定版本

- mpv 0.41.0
- 完整 uosc 5.12.0；`ziggy` 分别原生编译为 Windows amd64 和 macOS arm64
- uosc_danmaku 2.2.0，提交 `8fb2107d1e04ce1fd700496ca7d2e4a62182016a`
- thumbfast 提交 `0f711de3138c9bd6718209d819ac54022c23ded2`
- yt-dlp 2026.06.09 官方可执行文件

所有下载均使用 `dependencies.lock.json` 中的固定 URL 和 SHA-256 校验。uosc 使用完整上游配置
作为基底，仅程序化修改控制栏和本项目需要的少数选项。

## 使用体验

uosc 控制栏包含弹幕搜索、弹幕开关和弹幕设置。默认 `Ctrl+d` 搜索弹幕、`j` 开关弹幕；自动
哈希匹配、网络 URL 自动加载和保存弹幕默认关闭。播放器保留逐帧、AB 循环、速度、音量、
音画/字幕延迟、视频均衡器、截图、续播、统计信息和右键菜单等常用操作。

Windows 使用 `portable_config`。macOS 启动器使用独立目录
`~/Library/Application Support/mpv-lazy-enjoy/config`，不会覆盖 `~/.config/mpv`；脚本代码随应用
更新，用户可编辑的配置只在缺失时初始化。

## 构建

推荐在 GitHub Actions 中手动运行 `Build` 工作流。也可以在对应原生环境中执行：

```text
# MSYS2 CLANG64 shell
./scripts/build-windows-msys2.sh

# Apple Silicon macOS，先安装脚本所列 Homebrew 依赖
./scripts/build-macos-arm64.sh
```

Windows 脚本从源码编译 mpv，递归收集非系统 DLL，再组装便携包。macOS 脚本从源码编译原生
arm64 `mpv.app`，使用原生 arm64 启动器初始化独立配置并调用 mpv，嵌入 yt-dlp，最后使用
ad-hoc 身份签名。CI 会通过 LaunchServices 实际启动应用，避免仅验证签名却上传无法由 Finder
打开的包。无付费 Apple Developer
账号时无法提供 Developer ID 公证；首次启动应使用 Finder 右键“打开”或系统设置中的“仍要
打开”，不应全局关闭 Gatekeeper。

## 本地校验

```text
python3 -m unittest discover -s tests -v
python3 scripts/verify_release.py --platform macos-arm64 --release build/release/mpv-lazy-enjoy-0.1.0-dev-macos-arm64
```

## 现阶段边界

应用、脚本和网络解析器已经精确锁定；MSYS2/Homebrew 提供的编译依赖目前由 CI 记录实际版本，
但尚未逐项冻结仓库快照。这不影响日常构建，却意味着正式长期可复现发布前还需增加工具链与
FFmpeg 等依赖的源码镜像。uosc_danmaku 主线代码已识别 `darwin`，但上游 README 仍只声明
Windows/Linux；因此 macOS 作为本项目维护的集成目标，需要以 CI 和真机烟雾测试为准。
