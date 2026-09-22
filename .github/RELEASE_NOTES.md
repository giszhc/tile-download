交互式下载 XYZ 瓦片底图，纯标准库、零依赖。下载完自动生成离线预览页，不用再猜「下全没有」。

## 下载

| 系统 | 文件 | 怎么用 |
|---|---|---|
| Windows x64 | `tile-download-windows-x64.zip` | 解压后双击 `tile-download.exe` |
| Linux x64 | `tile-download-linux-x64.tar.gz` | 解压后执行 `bash run.sh` |
| macOS（Apple Silicon） | `tile-download-macos-arm64.tar.gz` | 解压后执行 `bash run.sh` |

压缩包里同时有独立可执行文件、`tile_download.py` 源码和启动脚本。启动脚本会优先执行同目录的二进制，找不到才回退到 `python3` / `py` / `python`（这种情况需要 Python ≥ 3.10）。

**Windows 上不用装 Python。**

## 主要特性

- **快**：每线程保留一条 keep-alive 长连接，并发限流可调。本地实测 295 张/秒（4 线程 + 每请求 sleep 的写法只有 6.6 张/秒）
- **防反爬**：随机 User-Agent / Referer、请求抖动打散脉冲、子域按线程分散、错误分类退避——429 长退避、5xx 短退避
- **通用**：任何标准 `{z}/{x}/{y}` 切片服务都能用，支持 `{s}` 子域，例如高德影像
  `https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}`
- **支持代理**：自动读取 `HTTP_PROXY` / `HTTPS_PROXY`，https 走 CONNECT 隧道
- **随时中断**：`Ctrl+C` 立刻停止发起新请求，已下载的瓦片全部保留，预览页照常生成
- **自带预览页**：下载完在输出根目录生成单文件 `index.html`（Leaflet，CDN 引入），双击看拼合效果，右上角实时显示当前地图级别
- **防误操作**：预估瓦片数超过 100 万张时二次确认，避免手滑爬全球高倍级

## 快速开始

运行后依次提示，直接回车用默认值：

```
底图服务地址（含 {z}/{x}/{y}，可选 {s}）: https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}
最大级别 [18]: 12
最小级别 [0]:
子域（可选）格式 ["1","2","3"] 或 1,2,3，回车=默认: ["1","2","3","4"]
并发线程数 [16]:
输出目录 [.]: F:\tiles\gaode
裁剪范围 经纬度(minlon,minlat,maxlon,maxlat)，回车=全球: 116.2,39.7,116.6,40.1
```

裁剪范围建议一定填。不填就是全球，18 级全球瓦片是数百亿量级。

输出结构：

```
输出目录/
  index.html        ← 自动生成的 Leaflet 预览页
  12/
    3374/
      1571.png
```

## 注意

- **macOS** 目前只提供 Apple Silicon（arm64）构建，Intel Mac 请用源码运行。首次打开若提示「无法验证开发者」，`run.sh` 会尝试去掉下载隔离标记；不行就手动执行 `xattr -d com.apple.quarantine tile-download`
- **Linux** 包在较新的发行版上构建，老系统（如 CentOS 7）可能因 glibc 版本报错，这类情况用源码跑
- **Windows** 的 exe 由 PyInstaller 打包，个别杀软会误报，放行即可；不放心就用源码跑，反正零依赖
- 请遵守目标服务的服务条款与版权要求，勿用于商业分发未授权数据

## 链接

- 仓库与完整文档：<https://github.com/giszhc/tile-download>
- 使用说明与实现细节：[README](https://github.com/giszhc/tile-download#readme)
