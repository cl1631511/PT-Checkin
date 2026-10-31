#!/usr/bin/env python3
"""代理探测的回归测试（防「把好的代理报成坏的」复发）。

2026-10-31 的教训：探测站点被我换成 api.ipify.org，那站点会重置云机房出口的 TLS，
于是四轮 CI 把「探针插不通」报成「你的代理坏了」。所以这里钉四件事：
① 握手和出口可达性是两层，日志要分开说；② 探测站点不回话不否决代理本身；
③ 任何返回给公开日志的话里不能带代理主机名/账号/密码；④ 格式错要说自己是格式错。

运行: python tests/test_proxy_probe.py
"""
import importlib.util
import socket
import sys
import threading
import types
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

cb = types.ModuleType("cloakbrowser")
cb.launch_persistent_context = lambda **k: None
sys.modules["cloakbrowser"] = cb

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("checkin", ROOT / "checkin.py")
checkin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkin)

_passed = 0
_failed = 0


def ok(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


# ---- 假 SOCKS5 服务端：只在 127.0.0.1 上跑，一个场景一个监听套接字 ----
def with_server(mode):
    """返回 (port, seen, stop)。seen 里能看到客户端到底发了什么字节。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    seen = {}

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    head = conn.recv(2)
                    nmethods = head[1] if len(head) == 2 else 0
                    seen["greet"] = head + conn.recv(nmethods)
                    if mode == "close":
                        continue                        # 收了连接什么都不答
                    if mode == "http":
                        conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                        continue
                    if mode == "weird":
                        conn.sendall(b"\x05\x07")       # 没人认识的方法
                        continue
                    need_auth = mode.startswith("auth")
                    if not need_auth:
                        conn.sendall(b"\x05\x00")
                        continue
                    conn.sendall(b"\x05\x02")           # 要求 RFC1929 账号密码
                    if mode == "authneed":
                        continue                        # 客户端没带账密，到这就该自己认
                    seen["auth"] = conn.recv(64)
                    conn.sendall(b"\x01\x00" if mode == "auth_ok" else b"\x01\x01")
                except OSError:
                    pass

    threading.Thread(target=loop, daemon=True).start()
    return port, seen, srv.close


# 桩用的假探测：只验 test_proxy 怎么编排两层，不真的出门
_real = {"_socks_probe": getattr(checkin, "_socks_probe", None),
         "_http_probe": getattr(checkin, "_http_probe", None),
         "_tcp_open": getattr(checkin, "_tcp_open", None)}


def stub(**kw):
    for k, v in kw.items():
        setattr(checkin, k, v)


def restore():
    for k, v in _real.items():
        if v is not None:
            setattr(checkin, k, v)


def dead_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DEAD = dead_port()

# ① 探测站点不能是那个会重置机房 IP 的
ok("api.ipify.org" not in checkin.SOCKS_PROBE_URL,
   f"探测站点已从被重置的 ipify 换走（现在={checkin.SOCKS_PROBE_URL}）")

# ② 裸握手：只和你的代理服务器说话，不碰任何第三方站点
port, seen, stop = with_server("noneed")
try:
    r = checkin.socks5_handshake("127.0.0.1", port, timeout=3)
    ok(r[0] is True and "握手" in r[1], f"无认证 SOCKS5：握手过（{r[1]}）")
    ok(seen.get("greet") == b"\x05\x01\x00", "没给账密时只报「无认证」一种方法")
finally:
    stop()

port, seen, stop = with_server("auth_ok")
try:
    r = checkin.socks5_handshake("127.0.0.1", port, username="u1", password="p1", timeout=3)
    ok(r[0] is True, f"要账号密码、且带对了：握手过（{r[1]}）")
    ok(seen.get("greet") == b"\x05\x02\x00\x02", "带账密时把「无认证+账号密码」两种都报上去让服务端选")
    ok(seen.get("auth", b"")[:1] == b"\x01", "认证子协商按 RFC1929 发 0x01 帧")
finally:
    stop()

port, _, stop = with_server("auth_bad")
try:
    r = checkin.socks5_handshake("127.0.0.1", port, username="wrong", password="wrong", timeout=3)
    ok(r[0] is False and ("账号" in r[1] or "密码" in r[1]),
       f"账密被服务端拒 → 说清是被拒，不是连不上（{r[1]}）")
finally:
    stop()

port, _, stop = with_server("authneed")
try:
    r = checkin.socks5_handshake("127.0.0.1", port, timeout=3)
    ok(r[0] is False and ("账号" in r[1] or "密码" in r[1]),
       f"代理要账密但 URL 没带 → 直接说缺账密（以前只会吐一句 SSL 错）（{r[1]}）")
finally:
    stop()

port, _, stop = with_server("http")
try:
    r = checkin.socks5_handshake("127.0.0.1", port, timeout=3)
    ok(r[0] is False and ("HTTP" in r[1] or "不是 SOCKS5" in r[1]),
       f"端口其实是 HTTP 代理时点名，别再让人拿 socks5:// 去试（{r[1]}）")
finally:
    stop()

port, _, stop = with_server("weird")
try:
    r = checkin.socks5_handshake("127.0.0.1", port, timeout=3)
    ok(r[0] is False and "方法" in r[1], f"服务端回了不支持的方法 → 报方法号（{r[1]}）")
finally:
    stop()

port, _, stop = with_server("close")
try:
    r = checkin.socks5_handshake("127.0.0.1", port, timeout=3)
    ok(r[0] is False and ("断" in r[1] or "没有回应" in r[1] or "超时" in r[1]),
       f"服务端收了连接就断 → 说断开/超时，而不是笼统一句失败（{r[1]}）")
finally:
    stop()

r = checkin.socks5_handshake("127.0.0.1", DEAD, timeout=3)
ok(r[0] is False and ("连不上" in r[1] or "拒绝" in r[1]),
   f"端口没人听 → 说「连不上代理服务器」，这是服务端/防火墙那一层（{r[1]}）")

# ③ 两层编排：握手过了、探测站点没回话 —— 不能一票否决整个代理
port, _, stop = with_server("noneed")
try:
    stub(_socks_probe=lambda parsed, timeout, retries:
         (False, "<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING]>"))
    r = checkin.test_proxy(f"socks5://127.0.0.1:{port}")
    ok(r[0] is True and "探测" in r[1],
       f"握手成功但探测站点不回话 → 仍然用这个代理签到（{r[1]}）")
    stub(_socks_probe=lambda parsed, timeout, retries: (True, "状态码 200"))
    r = checkin.test_proxy(f"socks5://127.0.0.1:{port}")
    ok(r[0] is True and "出口可达" in r[1],
       f"两层都过 → 说清是「握手 + 出口可达」（{r[1]}）")
finally:
    stop()
    restore()

r = checkin.test_proxy("socks5://127.0.0.1:%d" % DEAD)
ok(r[0] is False, "握手都连不上 → 代理不可用（这才允许退回直连）")

# ④ 公开日志：返回的话里不能带代理主机名/账号/密码
# 两层都桩掉，好让「握手过了、探测报了带凭文的错」这条真路径走到 _hide ——
# 上一版直接拿假域名跑握手，握手先失败，探测那行根本没执行，检查是空的。
_LEAKY = "HTTPSConnectionPool(host='secret-proxy-host.example.net', port=1080): " \
         "myuser:mypass@secret-proxy-host.example.net SSL EOF " \
         "peer=203.0.113.9 token=abcdef0123456789abcdef0123456789"
_real_handshake = checkin.socks5_handshake
_real_tcp_open = getattr(checkin, "_tcp_open", None)
try:
    checkin.socks5_handshake = lambda *a, **k: (True, "SOCKS5 握手成功")
    stub(_socks_probe=lambda parsed, timeout, retries: (False, _LEAKY))
    msg = checkin.test_proxy("socks5://myuser:mypass@secret-proxy-host.example.net:1080")[1]
    ok("myuser" not in msg and "mypass" not in msg and "secret-proxy-host" not in msg,
       f"探测站点的报错整段带着代理 URL 回来，返回前仍被遮掉（{msg}）")
    ok("203.0.113.9" not in msg and "abcdef0123456789abcdef0123456789" not in msg,
       f"报错里混了出口 IP 和长 token 也一并抠掉（{msg}）")
    ok("握手成功" in msg and "探测站点没回话" in msg,
       f"遮完信息还得说清是哪一层没回话（{msg}）")
    checkin.socks5_handshake = lambda *a, **k: (False, f"连不上 {_LEAKY}")
    msg = checkin.test_proxy("socks5://myuser:mypass@secret-proxy-host.example.net:1080")[1]
    ok("myuser" not in msg and "secret-proxy-host" not in msg,
       f"握手那层自己失败也一样遮（{msg}）")
finally:
    checkin.socks5_handshake = _real_handshake
    restore()

# ⑤ HTTP 代理同样分两层：TCP 通了但探测失败不否决
stub(_tcp_open=lambda host, port, timeout: True,
     _http_probe=lambda url, timeout, retries: (False, "状态码异常: 503"))
r = checkin.test_proxy("http://u:p@secret-proxy-host.example.net:8080")
ok(r[0] is True, f"HTTP 代理：TCP 通、探测失败 → 不否决（{r[1]}）")
ok("secret-proxy-host" not in r[1] and "u:p" not in r[1], "HTTP 代理那条也不漏主机和账密")
stub(_tcp_open=lambda host, port, timeout: False)
r = checkin.test_proxy("http://secret-proxy-host.example.net:8080")
ok(r[0] is False and "连不上" in r[1], f"HTTP 代理：TCP 都不通才算不可用（{r[1]}）")
restore()

# ⑥ 真探测函数自己：走桩 urlopen，钉住「用哪个地址」和「正文不落日志」
import urllib.parse
import urllib.request as _ur


class _FakeResp:
    def getcode(self):
        return 200

    def read(self):
        return b"203.0.113.9"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_probe = {"urls": []}
_real_urlopen = _ur.urlopen
_ur.urlopen = lambda req, timeout=None: (_probe["urls"].append(req.full_url), _FakeResp())[1]
socks_stub = types.ModuleType("socks")
socks_stub.SOCKS5 = 5
socks_stub.socksocket = type("_SocksSock", (), {})
socks_stub.set_default_proxy = lambda *a, **k: None
sys.modules["socks"] = socks_stub
try:
    res, msg = checkin._socks_probe(
        urllib.parse.urlparse("socks5://u:p@proxy.example:1080"), timeout=5, retries=1)
    ok(res is True, "探测站点回 200 → 这一层报 True")
    ok(_probe["urls"] and _probe["urls"][0] == checkin.SOCKS_PROBE_URL,
       f"探测打的地址就是那个常量（{_probe['urls']}）")
    ok(_probe["urls"] and "cloudflare.com" not in _probe["urls"][0],
       "不探测带 AAAA 的 www.cloudflare.com（PySocks 不支持 IPv6 会假失败）")
    ok("203.0.113.9" not in msg, f"探测正文就是出口 IP，不进日志（{msg}）")
finally:
    _ur.urlopen = _real_urlopen

# ⑦ 格式判据要能自己说话：缺端口 / 不支持的协议，报的是格式，不是"连不上"
r = checkin.test_proxy("socks5://example.com")
ok(r[0] is False and "格式" in r[1], f"没写端口 → 说格式问题（{r[1]}）")
r = checkin.test_proxy("vless://uuid@example.com:443")
ok(r[0] is False and "不支持" in r[1] and "socks5" in r[1],
   f"vless:// 这种节点链接 → 明确说不支持，并列出支持的写法（{r[1]}）")

print(f"\n{_passed}/{_passed + _failed} 通过")
sys.exit(1 if _failed else 0)
