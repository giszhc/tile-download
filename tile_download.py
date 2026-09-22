#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
底图瓦片下载脚本（标准 XYZ 切片）

防反爬：随机 UA / 随机 Referer / 请求抖动 / 并发限流 / 指数退避重试 / 429 退避
性能：每线程持久连接（keep-alive）+ 高并发，实测吞吐可达浏览器的同量级
中断：Ctrl+C 优雅收工，已下载的瓦片与预览页照常保留
零三方依赖（纯标准库），uv run 或 python 直接跑均可。
下载完自动在输出根目录生成 Leaflet 预览页 index.html（256 / 512 等非标准瓦片尺寸自动识别）。
"""
import os
import sys
import math
import time
import random
import threading
import itertools
import http.client
import urllib.request
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed, CancelledError

# ---------- 参数（按需调）----------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]
DEFAULT_SUBDOMAINS = ["a", "b", "c"]   # 未填子域时的默认
CONCURRENCY = 16                  # 并发线程数（每线程一条持久连接）
DELAY_MIN = 0.02                  # 每次请求前随机延时下限(秒)，只为打散节奏
DELAY_MAX = 0.08                  # 上限
MAX_RETRIES = 3
TIMEOUT = 20
VALID_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
TILE_SIZE = 0     # 预览页瓦片像素尺寸；0 = 按下载到的第一张图自动识别（256/512/128 都支持）

_tls = threading.local()          # 线程本地：持久连接 + 固定子域
_sub_seq = itertools.count()
_PROXY = {}                       # 环境变量代理，init_proxy() 时填充
_stop = threading.Event()         # Ctrl+C 时置位，worker 立刻收工

# 1x1 透明 GIF，预览页缺图兜底用
TRANSPARENT_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00"
                   b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


def init_proxy():
    global _PROXY
    try:
        _PROXY = urllib.request.getproxies() or {}
    except Exception:
        _PROXY = {}


_LOOPBACK = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _proxy_url(scheme, host=None):
    """该走代理就返回代理地址，否则返回 None。

    本地地址一律直连 —— 自建 / 内网瓦片服务很常见，套上代理反而连不上。
    """
    if host and (host in _LOOPBACK or host.endswith(".local")):
        return None
    if host:
        try:
            if urllib.request.proxy_bypass(host):    # 尊重 no_proxy / 系统绕过列表
                return None
        except Exception:
            pass
    return _PROXY.get(scheme) or _PROXY.get("all")


def _sleep(sec):
    """可分片打断的 sleep；返回 False 表示期间收到了停止信号。"""
    end = time.time() + sec
    while True:
        remain = end - time.time()
        if remain <= 0:
            return True
        if _stop.is_set():
            return False
        time.sleep(min(0.1, remain))


def build_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": random.choice([
            "https://www.google.com/",
            "https://www.openstreetmap.org/",
            "https://map.baidu.com/",
        ]),
        "Connection": "keep-alive",
    }


def ask(prompt, default):
    val = input(f"{prompt} [{default}]: ").strip()
    return val or default


def parse_subdomains(text):
    """支持 ["1","2","3"] / 1,2,3 / [1,2,3] 三种写法，返回字符串列表。"""
    text = text.strip()
    if not text:
        return DEFAULT_SUBDOMAINS
    try:
        import json
        if text.startswith("["):
            return [str(v) for v in json.loads(text)]
    except Exception:
        pass
    text = text.strip("[]")
    return [v.strip().strip('"').strip("'") for v in text.split(",") if v.strip()]


def guess_ext(url_template):
    """从地址推测瓦片扩展名，识别不出就按 png。"""
    path = urlparse(url_template).path
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in VALID_EXT else ".png"


def parse_template(url_template):
    """把 {s}/{z}/{x}/{y} 模板拆成 协议 / 主机模板 / 路径模板。"""
    u = urlparse(url_template)
    scheme = u.scheme or "http"
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    return scheme, u.netloc, path


def image_size(data):
    """从字节头读图片像素尺寸，认不出返回 None。纯标准库，只解析头部。

    每种格式各自检查所需的最短长度，别用一个统一的阈值一刀切 ——
    否则短的合法头（比如小尺寸 WebP）会被误判成未知格式。
    """
    if len(data) < 16:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:   # PNG: IHDR
        return (int.from_bytes(data[16:20], "big"),
                int.from_bytes(data[20:24], "big"))
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:  # GIF: 逻辑屏幕
        return (int.from_bytes(data[6:8], "little"),
                int.from_bytes(data[8:10], "little"))
    if data[:2] == b"\xff\xd8":                                 # JPEG: 找 SOF 段
        i, n = 2, len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seglen = int.from_bytes(data[i + 2:i + 4], "big")
            if seglen < 2:                                      # 段长非法，别继续瞎跳
                return None
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return (int.from_bytes(data[i + 7:i + 9], "big"),
                        int.from_bytes(data[i + 5:i + 7], "big"))
            i += 2 + seglen
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":            # WebP 三种子格式
        fmt = data[12:16]
        if fmt == b"VP8X" and len(data) >= 30:                   # 扩展格式：24 位画布尺寸
            return (int.from_bytes(data[24:27], "little") + 1,
                    int.from_bytes(data[27:30], "little") + 1)
        if fmt == b"VP8 " and len(data) >= 30:                   # 有损：帧标签+起始码后是 2 字节宽高
            return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                    int.from_bytes(data[28:30], "little") & 0x3FFF)
        if fmt == b"VP8L" and len(data) >= 25:                   # 无损：14 位宽 + 14 位高
            bits = int.from_bytes(data[21:25], "little")
            return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return None


def lon_to_x(lon, z):
    return int((lon + 180.0) / 360.0 * (2 ** z))


def lat_to_y(lat, z):
    rad = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(rad)) / math.pi) / 2.0 * (2 ** z))


def tile_range_for_bbox(bbox, z):
    if bbox is None:
        n = 2 ** z
        return 0, n - 1, 0, n - 1
    lon1, lat1, lon2, lat2 = bbox
    x1, x2 = lon_to_x(lon1, z), lon_to_x(lon2, z)
    y1, y2 = lat_to_y(lat1, z), lat_to_y(lat2, z)
    return min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)


def _drop_conn():
    conn = getattr(_tls, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _tls.conn = None
    _tls.key = None


def _get_conn(scheme, host, port):
    """取本线程的持久连接；目标或代理变了、连接不可用就重建。"""
    key = (scheme, host, port)
    conn = getattr(_tls, "conn", None)
    if conn is not None and getattr(_tls, "key", None) == key:
        return conn
    _drop_conn()
    purl = _proxy_url(scheme, host)
    if purl:
        p = urlparse(purl if "://" in purl else "http://" + purl)
        pport = p.port or (443 if p.scheme == "https" else 80)
        if scheme == "https":
            conn = http.client.HTTPSConnection(p.hostname, pport, timeout=TIMEOUT)
            conn.set_tunnel(host, port)                 # CONNECT 隧道
        else:
            conn = http.client.HTTPConnection(p.hostname, pport, timeout=TIMEOUT)
        conn._via_proxy = True
    elif scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
    _tls.conn = conn
    _tls.key = key
    return conn


def _thread_sub(subdomains):
    """每个线程固定一个子域 —— 连接可复用，请求也自然分散到多台服务器。"""
    sub = getattr(_tls, "sub", None)
    if sub is None:
        sub = subdomains[next(_sub_seq) % len(subdomains)]
        _tls.sub = sub
    return sub


def fetch(tpl, z, x, y, subdomains):
    if _stop.is_set():                                  # 已收到 Ctrl+C，不再发起请求
        return z, x, y, None
    scheme, netloc_t, path_t = tpl
    host_t, _, port_t = netloc_t.partition(":")
    host = host_t.replace("{s}", _thread_sub(subdomains))
    port = int(port_t) if port_t else (443 if scheme == "https" else 80)
    path = (path_t.replace("{z}", str(z))
                  .replace("{x}", str(x))
                  .replace("{y}", str(y)))
    if not _sleep(random.uniform(DELAY_MIN, DELAY_MAX)):  # 轻微抖动，避免整齐脉冲
        return z, x, y, None
    for attempt in range(MAX_RETRIES + 1):
        if _stop.is_set():
            return z, x, y, None
        try:
            conn = _get_conn(scheme, host, port)
            req_path = path
            headers = build_headers()
            if getattr(conn, "_via_proxy", False) and scheme == "http":
                req_path = f"http://{host}:{port}{path}"   # 代理走绝对 URI
                headers["Host"] = f"{host}:{port}"
            conn.request("GET", req_path, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            data = resp.read()                          # 必须读完，否则连接无法复用
            if status == 200 and data:
                return z, x, y, data
            if status == 404:
                return z, x, y, None                    # 瓦片不存在，正常情况
            if status == 429:                           # 被限流
                _drop_conn()
                if not _sleep(3 * (2 ** attempt)):
                    return z, x, y, None
                continue
            if status in (500, 502, 503, 504):
                _drop_conn()
                if not _sleep(1 + attempt):
                    return z, x, y, None
                continue
            return z, x, y, None
        except Exception:
            _drop_conn()                                # 服务端关连接等，重连重试
            if not _sleep(0.5 * (attempt + 1)):
                return z, x, y, None
    return z, x, y, None


PREVIEW_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>底图瓦片预览</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  html,body{margin:0;height:100%;background:#f5f6f8}
  #map{position:absolute;inset:0}
  #info{position:absolute;z-index:1000;left:10px;bottom:10px;background:#fff;color:#222;
        padding:6px 10px;border-radius:6px;font:12px/1.6 system-ui,-apple-system,sans-serif;
        box-shadow:0 1px 4px rgba(0,0,0,.25)}
  #zoom{position:absolute;z-index:1000;right:10px;top:10px;background:#1f6feb;color:#fff;
        padding:6px 12px;border-radius:6px;font:600 13px/1.4 system-ui,-apple-system,sans-serif;
        box-shadow:0 1px 4px rgba(0,0,0,.3);user-select:none}
</style>
</head>
<body>
<div id="map"></div>
<div id="zoom">级别 --</div>
<div id="info">本地瓦片 · 级别 __MINZ__–__MAXZ__ · 瓦片 __TILESIZE__px · 只爬了范围内瓦片，范围外为空白</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  var map = L.map('map', {attributionControl: false, zoomControl: true})
             .setView([__LAT__, __LNG__], __ZOOM__);
  var zoomEl = document.getElementById('zoom');
  // 显示的是瓦片所在目录的级别，不是 Leaflet 的内部缩放刻度
  function showZoom(){ zoomEl.textContent = '级别 ' + (map.getZoom() + __ZOFFSET__); }
  map.on('zoomend', showZoom);       // 缩放结束就刷新
  map.on('load', showZoom);
  showZoom();
  L.tileLayer('{z}/{x}/{y}__EXT__', {
    minZoom: __MINZOOM__,
    maxZoom: __MAXZOOM__,
    minNativeZoom: __MINZOOM__,
    maxNativeZoom: __MAXZOOM__,
    tileSize: __TILESIZE__,
    zoomOffset: __ZOFFSET__,
    keepBuffer: 4,
    errorTileUrl: 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=='
  }).addTo(map);
</script>
</body>
</html>
"""


def write_preview(out_dir, ext, min_z, max_z, bbox, tile_size=256):
    """在输出根目录生成 Leaflet 预览页。

    瓦片不一定是 256px —— 512px 的「高清」瓦片很常见。Leaflet 要求
    tileSize * 2^(zoom + zoomOffset) == 256 * 2^zoom 地理位置才对得上，
    所以按实际像素尺寸反推 zoomOffset，再把 minZoom/maxZoom 换算到地图刻度。
    """
    if bbox:
        lon1, lat1, lon2, lat2 = bbox
        lat, lng = (lat1 + lat2) / 2.0, (lon1 + lon2) / 2.0
    else:
        lat, lng = 0.0, 0.0
    tile_size = tile_size or 256
    zoom_offset = int(round(math.log2(256.0 / tile_size)))
    zoom = min_z if min_z > 0 else (1 if max_z >= 1 else 0)
    html = (PREVIEW_HTML
            .replace("__LAT__", f"{lat:.6f}")
            .replace("__LNG__", f"{lng:.6f}")
            .replace("__ZOOM__", str(zoom - zoom_offset))
            .replace("__MINZ__", str(min_z))
            .replace("__MAXZ__", str(max_z))
            .replace("__MINZOOM__", str(min_z - zoom_offset))
            .replace("__MAXZOOM__", str(max_z - zoom_offset))
            .replace("__TILESIZE__", str(tile_size))
            .replace("__ZOFFSET__", str(zoom_offset))
            .replace("__EXT__", ext))
    path = os.path.join(out_dir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    print("=== 底图瓦片下载 ===")
    print("（运行中按 Ctrl+C 可随时中断，已下载的瓦片会保留）")
    url = input("底图服务地址（含 {z}/{x}/{y}，可选 {s}）: ").strip()
    if not url:
        print("地址不能为空，退出。")
        sys.exit(1)
    max_z = int(ask("最大级别", "18"))
    min_z = int(ask("最小级别", "0"))
    subdomains = parse_subdomains(
        input('子域（可选）格式 ["1","2","3"] 或 1,2,3，回车=默认: ')
    )
    if "{s}" in url:
        print(f"  -> 使用的子域：{subdomains}")
    concurrency = int(ask("并发线程数", str(CONCURRENCY)))
    out_dir = ask("输出目录", ".")
    bbox_str = input("裁剪范围 经纬度(minlon,minlat,maxlon,maxlat)，回车=全球: ").strip()
    bbox = None
    if bbox_str:
        try:
            bbox = tuple(float(v) for v in bbox_str.split(","))
        except Exception:
            print("范围格式错误，按全球处理。")

    total = 0
    for z in range(min_z, max_z + 1):
        x0, x1, y0, y1 = tile_range_for_bbox(bbox, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    print(f"预计约 {total:,} 张瓦片。")
    if total > 1_000_000:
        if input("数量很大，确认继续？(y/N): ").strip().lower() != "y":
            print("已取消。")
            sys.exit(0)

    init_proxy()
    if _PROXY:
        print(f"  -> 环境变量里检测到代理：{_PROXY}（本地/内网地址自动直连）")
    ext = guess_ext(url)
    tpl = parse_template(url)
    os.makedirs(out_dir, exist_ok=True)
    tasks = []
    for z in range(min_z, max_z + 1):
        x0, x1, y0, y1 = tile_range_for_bbox(bbox, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                tasks.append((z, x, y))

    done = saved = failed = 0
    tile_size = TILE_SIZE               # 0 = 还没测出来，拿第一张成功的图当样本
    interrupted = False
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=concurrency)
    futures = [ex.submit(fetch, tpl, z, x, y, subdomains) for (z, x, y) in tasks]
    try:
        for fut in as_completed(futures):
            try:
                z, x, y, data = fut.result()
            except CancelledError:
                continue
            done += 1
            if data:
                p = os.path.join(out_dir, str(z), str(x))
                os.makedirs(p, exist_ok=True)
                with open(os.path.join(p, f"{y}{ext}"), "wb") as f:
                    f.write(data)
                if not tile_size:                       # 首个成功样本，读真实像素尺寸
                    sz = image_size(data)
                    if sz:
                        tile_size = sz[0]
                saved += 1
            else:
                failed += 1
            if done % 200 == 0 or done == total:
                el = max(time.time() - t0, 1e-9)
                print(f"进度 {done}/{total}  已存 {saved}  失败 {failed}  "
                      f"耗时 {el:.0f}s  {done/el:.0f} 张/秒")
    except KeyboardInterrupt:
        interrupted = True
        _stop.set()
        print("\n[中断] 收到 Ctrl+C，正在收工……（再按一次可强制退出）")
    finally:
        for f in futures:
            f.cancel()                                  # 未开始的任务直接作废
        try:
            ex.shutdown(wait=True, cancel_futures=True)
        except KeyboardInterrupt:                       # 收工途中又按了一次
            print("\n[强制退出] 已下载的瓦片保留在输出目录。")
            os._exit(130)

    elapsed = max(time.time() - t0, 1e-9)
    if interrupted:
        print(f"[已中断] 成功 {saved}，失败 {failed}，进度 {done}/{total}，"
              f"耗时 {elapsed:.1f}s。输出：{os.path.abspath(out_dir)}")
    else:
        print(f"完成：成功 {saved}，失败 {failed}，总 {total}，"
              f"耗时 {elapsed:.1f}s（{total/elapsed:.0f} 张/秒）。输出：{os.path.abspath(out_dir)}")

    if saved > 0:
        if not tile_size:
            tile_size = 256
            print("  -> 没读出瓦片像素尺寸，预览页按 256px 处理")
        else:
            print(f"  -> 瓦片像素尺寸：{tile_size}px")
        preview = write_preview(out_dir, ext, min_z, max_z, bbox, tile_size)
        print(f"预览页已生成：{preview}")
        if interrupted:
            print("（按级别范围逐个下的话，前面级别的瓦片通常是完整的，可以直接看）")
        print("打开方式：双击 index.html；若瓦片加载不出来，在输出目录执行 python -m http.server 后访问 http://localhost:8000")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:                           # 输入阶段或收尾阶段被中断
        print("\n已取消。")
        sys.exit(130)
