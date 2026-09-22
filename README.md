# 底图瓦片下载脚本

交互式下载 XYZ 瓦片底图，内置防反爬，速度接近浏览器。纯标准库、零依赖，也提供免安装的独立可执行文件。

## 下载使用

到 [Releases](https://github.com/giszhc/tile-download/releases) 页面按系统下载，解压即用：

| 系统 | 压缩包 | 怎么跑 |
|---|---|---|
| Windows | `tile-download-windows-x64.zip` | 双击 `tile-download.exe` |
| Linux | `tile-download-linux-x64.tar.gz` | `bash run.sh` |
| macOS（Apple Silicon） | `tile-download-macos-arm64.tar.gz` | `bash run.sh` |

不用装 Python，压缩包里已经带好了。

## 从源码运行

需要 Python ≥ 3.10，不需要装任何包：

```bash
python tile_download.py
```

Windows 也可以直接双击 `run.bat`，macOS / Linux 执行 `bash run.sh`（两个启动脚本都会优先用同目录下的独立可执行文件，没有就退回源码运行）。

也可以用 uv 跑：

```bash
uv run tile_download.py
```

## 中断

运行中随时按 `Ctrl+C`：

- 立刻停止发起新请求，在途的请求收尾后退出，不会卡住
- 已下载的瓦片全部保留
- 已下载的部分照常生成预览页，可以马上检查下到哪了
- 收尾途中再按一次 `Ctrl+C` 强制退出，已下载的瓦片同样保留

## 交互输入

运行后依次提示，直接回车使用默认值。

| 提示 | 说明 | 默认 |
|------|------|------|
| 底图服务地址 | 必填，含 `{z}/{x}/{y}`，可含 `{s}` | — |
| 最大级别 | 爬到哪一级 | 18 |
| 最小级别 | 从哪一级开始 | 0 |
| 子域（可选） | `["1","2","3"]`、`1,2,3`、`[1,2,3]` 均可 | `["a","b","c"]` |
| 并发线程数 | 调大更快，也更容易被限流 | 16 |
| 输出目录 | 瓦片保存位置 | 当前目录 |
| 裁剪范围 | `minlon,minlat,maxlon,maxlat` | 回车=全球 |

## 地址模板变量

| 变量 | 含义 |
|------|------|
| `{z}` | 级别 |
| `{x}` | 列号 |
| `{y}` | 行号 |
| `{s}` | 子域，每个线程固定取列表里的一个（轮询分配） |

## 输出结构

```
输出目录/
  index.html        ← 下载完自动生成的 Leaflet 预览页
  10/
    843/
      388.png
```

即 `输出目录/z/x/y.png`，根目录附带 `index.html` 预览页。

## 预览

下载完成后自动在输出根目录生成 `index.html`（Leaflet，CDN 引入，轻量）。

- 双击打开即可看拼合效果，起点自动对准你裁剪范围的中心
- 右上角实时显示**当前地图级别**，缩放后立即刷新
- 拖动缩放可逐级查看，范围外瓦片显示为空白
- 若双击后瓦片加载不出来（浏览器 `file://` 限制），在输出目录起个静态服务：

```bash
cd 输出目录
python -m http.server 8000
# 浏览器访问 http://localhost:8000
```

> 预览页从 CDN 加载 Leaflet，需要联网。

## 示例

### 高德影像

```
底图服务地址: https://webst0{s}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}
最大级别 [18]: 12
最小级别 [0]:
子域（可选）: ["1","2","3","4"]
并发线程数 [16]:
输出目录 [.]: F:\tiles\gaode
裁剪范围: 116.2,39.7,116.6,40.1
```

`style=6` 影像图，`style=7` 矢量图；`{s}` 为纯数字。

### OpenStreetMap

```
底图服务地址: https://tile.openstreetmap.org/{z}/{x}/{y}.png
最大级别 [18]: 10
输出目录 [.]: F:\tiles\osm
裁剪范围: 116.2,39.7,116.6,40.1
```

## 速度

实测（本地 HTTP 服务，400 张瓦片）：

| 配置 | 吞吐 |
|------|------|
| 旧：4 线程 + 0.3~0.9s 延时 + 每请求新建连接 | 6.6 张/秒 |
| 现：16 线程 + 0.02~0.08s 抖动 + 连接复用 | 295 张/秒 |

慢的根因不是 Python：

- 每次请求固定睡 0.3~0.9s，4 线程 → 上限约 6.7 张/秒，与网络无关
- 每张瓦片重新走一次 TCP + TLS 握手，浏览器是复用连接的

现在每线程持有一条 keep-alive 长连接，只保留轻微抖动打散节奏，想更快就把「并发线程数」往上加。

## 防反爬策略

- 随机 User-Agent 池
- 随机 Referer
- 请求前随机抖动，避免整齐脉冲
- 线程级固定子域，`{s}` 天然分散到多台服务器
- 失败指数退避重试，429 单独长退避
- 并发上限可调，别把线程数开到被限流的程度

参数在脚本顶部常量区：

```python
CONCURRENCY = 16     # 默认并发线程数（运行时还可再输入覆盖）
DELAY_MIN = 0.02     # 请求前随机抖动下限(秒)
DELAY_MAX = 0.08     # 上限
MAX_RETRIES = 3      # 单瓦片最大重试次数
TIMEOUT = 20         # 单请求超时(秒)
```

## 使用代理

脚本自动读取环境变量代理（`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`），https 走 CONNECT 隧道，http 走绝对 URI。

```bash
# Windows CMD
set HTTPS_PROXY=http://127.0.0.1:7890
python tile_download.py

# Git Bash / Linux / macOS
export HTTPS_PROXY=http://127.0.0.1:7890
python tile_download.py
```

若代理影响了访问国内底图，清掉变量即可：

```bash
set HTTPS_PROXY=
# 或
unset HTTPS_PROXY
```

## 自己打包

Windows 单文件 exe：

```bash
pip install pyinstaller
pyinstaller --onefile --console --clean --noconfirm \
  --name tile-download \
  --distpath dist --workpath build --specpath build \
  tile_download.py
```

产物在 `dist/tile-download.exe`（约 9 MB，已内嵌 Python 运行时）。

三个平台的正式发布包由 GitHub Actions 构建：给仓库推一个 `v*` 标签，或在 Actions 页面手动触发 `Release` 工作流并填版本号，会自动编译并发布到 Releases。

## 注意

- 不填裁剪范围 = 全球，高倍级瓦片数量爆炸（18 级全球约数百亿张），脚本会在预估超过 100 万张时二次确认，建议始终填写裁剪范围。
- 并发开太高会被目标服务限流甚至封 IP，遇到大量 429 就降并发。
- Windows 版 exe 由 PyInstaller 打包，个别杀软会误报，放行即可；不放心就用源码跑。
- macOS 版是 Apple Silicon（arm64）构建，Intel Mac 请用源码运行；若提示「无法验证开发者」，`run.sh` 会自动去掉隔离标记，不行就手动执行 `xattr -d com.apple.quarantine tile-download`。
- Linux 版在较新的发行版上构建，老系统（如 CentOS 7）可能因 glibc 版本报错，这类情况用源码跑。
- 请遵守目标服务的服务条款与版权要求，勿用于商业分发未授权数据。
