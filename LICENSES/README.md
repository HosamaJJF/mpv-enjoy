# License material

本目录说明项目源码本身不内嵌第三方组件。`scripts/assemble.py` 会从经过 SHA-256 校验的上游
源码中提取许可证全文，写入每个发行包的 `LICENSES` 目录。项目自有内容的 MIT
许可证与第三方组件说明统一见根目录 `LICENSE.MD`。构建 Home 壳层时还会根据其锁定的
`package-lock.json` 和 `Cargo.lock` 生成 npm/Rust 依赖许可证清单，并把这些组件展开到
最终的 SPDX SBOM。
