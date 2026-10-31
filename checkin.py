"""
Cloak Browser 自动签到助手 | Cloak Browser Check-in Assistant
https://cloakbrowser.dev

所有站点配置从 config.json 读取，首次运行请执行 run.sh / run.bat。
"""

import json
import sys
import time
from pathlib import Path
import os
import urllib.parse
import re
import socket

from cloakbrowser import launch_persistent_context

BASE_DIR = Path(__file__).parent

# ── 内置站点模板（可在 config.json 中覆盖或新增任意 NexusPHP 站点） ─────────
TEMPLATES: dict[str, dict] = {
    "ourbits": {
        "url": "https://ourbits.club",
        "login_path": "/login.php",
        "checkin_path": "/attendance.php",
        "checkin_type": "turnstile",
        "login_captcha": False,
        "login_fields": {"username": "username", "password": "password"},
    },
    "hdkyl": {
        "url": "https://www.hdkyl.in",
        "login_path": "/login.php",
        "checkin_path": "/attendance.php",
        "checkin_type": "auto",
        "login_captcha": False,
        "login_fields": {"username": "username", "password": "password"},
    },
    "hitpt": {
        "url": "https://www.hitpt.com",
        "login_path": "/login.php",
        "checkin_path": "/attendance.php",
        "checkin_type": "auto",
        "login_captcha": False,
        "login_fields": {"username": "username", "password": "password"},
    },
    "piggo": {
        "url": "https://piggo.me",
        "login_path": "/login.php",
        "checkin_path": "/attendance.php",
        "checkin_type": "auto",
        # piggo 绑了异地登录检测：从 Azure 直连的 IP 去动这份会话，站点会锁号
        # （2026-10-31 就这么锁过一次）。没有可用代理时宁可这一轮不签。
        "require_proxy": True,
        "login_captcha": False,
        "login_fields": {"username": "username", "password": "password"},
    },
    "audiences": {
        "url": "https://audiences.me",
        "login_path": "/login.php",
        "checkin_path": "/attendance.php",
        "checkin_type": "turnstile",
        "login_captcha": True,
        "login_fields": {
            "username": "username",
            "password": "password",
            "captcha_code": "imagestring",
            "captcha_hash": "imagehash",
        },
        "captcha_image_selector": 'img[alt="CAPTCHA"]',
    },
}


# ── 配置加载 ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg_path = BASE_DIR / "config.json"
    if not cfg_path.exists():
        print("[!] 未找到 config.json。请先运行 run.sh 或 run.bat 进行配置。")
        print("    config.json not found. Run run.sh or run.bat first.")
        sys.exit(1)
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[!] config.json 格式错误: {e}")
        sys.exit(1)


def build_sites(config: dict) -> list[dict]:
    """将 config.json 中的站点列表与内置模板合并，返回完整站点配置。"""
    sites = []
    for entry in config.get("sites", []):
        if not entry.get("enabled", True):
            continue
        name = entry.get("name", "")
        if not name:
            continue
        # 以内置模板为基础，config.json 中的字段可覆盖
        site = dict(TEMPLATES.get(name, {}))
        site.update(entry)
        if not site.get("url"):
            print(f"[!] 站点 '{name}' 缺少 url，已跳过。")
            continue
        if not site.get("username") or not site.get("password"):
            print(f"[!] 站点 '{name}' 缺少账号或密码，已跳过。")
            continue
        sites.append(site)
    return sites


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def get_profile_dir(site_name: str) -> str:
    return str(BASE_DIR / f"profile_{site_name}")


def get_cookies_file(site_name: str) -> Path:
    return BASE_DIR / f"cookies_{site_name}.json"


def save_cookies(context, site_name: str):
    cookies = context.cookies()
    get_cookies_file(site_name).write_text(json.dumps(cookies, indent=2))


def load_cookies(context, site_name: str) -> bool:
    f = get_cookies_file(site_name)
    if not f.exists():
        return False
    try:
        cookies = json.loads(f.read_text())
        context.add_cookies(cookies)
        return True
    except (json.JSONDecodeError, KeyError):
        return False


def wait_past_cf(page, timeout=300) -> bool:
    """等待 Cloudflare 验证完成。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            title = page.title()
            body = page.evaluate("() => document.body ? document.body.innerText.substring(0, 300) : ''")
            if "安全验证" not in body and "Just a moment" not in title:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def wait_past_leichi(page, timeout=300) -> bool:
    """等待雷池WAF验证完成，返回是否成功。"""
    print("    [*] 检测到雷池WAF验证，等待自动通过...")
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            body = page.evaluate("() => document.body ? document.body.innerText : ''")
            # 检查是否还在雷池验证页面
            if "雷池WAF" not in body and "安全检测能力由雷池WAF驱动" not in body:
                print(f"    [+] 雷池WAF验证通过 (尝试 {attempt})")
                return True
            # 检查是否有签到成功关键词
            success_keywords = ["已经签到", "已签到", "签到成功", "今日已签到"]
            for kw in success_keywords:
                if kw in body:
                    print(f"    [+] 雷池WAF验证通过，已自动签到 (检测到: {kw})")
                    return True
            if attempt % 5 == 0:
                print(f"    [*] 等待雷池WAF验证... ({attempt})")
        except Exception:
            pass
        time.sleep(3)
    print("    [!] 雷池WAF验证超时")
    return False


# ── 代理配置 ──────────────────────────────────────────────────────────────────

def build_cloak_proxy_config(proxy_url: str) -> dict | str | None:
    """
    构建 CloakBrowser 的代理配置。
    
    CloakBrowser 的 proxy 参数支持:
        - 字符串: "http://host:port" 或 "socks5://host:port"
        - 字典: {"server": "host:port", "type": "socks5", "username": "...", "password": "..."}
    """
    if not proxy_url:
        return None
    
    parsed = urllib.parse.urlparse(proxy_url)
    
    # 支持的代理协议（和 test_proxy 同一份，别各写一遍）
    if parsed.scheme not in PROXY_SCHEMES:
        print(f"    [!] 不支持的代理协议: {parsed.scheme}，支持: {PROXY_SCHEMES}")
        return None
    
    if not parsed.hostname or not parsed.port:
        print(f"    [!] 代理 URL 缺少 host 或 port: {proxy_url}")
        return None
    
    # SOCKS5 代理返回字符串格式（CloakBrowser 原生支持）
    if parsed.scheme.startswith('socks5'):
        if parsed.username and parsed.password:
            return f"socks5://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"
        else:
            return f"socks5://{parsed.hostname}:{parsed.port}"
    
    # HTTP/HTTPS 代理使用字典格式
    proxy_config = {
        "server": f"{parsed.hostname}:{parsed.port}",
    }
    if parsed.username and parsed.password:
        proxy_config["username"] = parsed.username
        proxy_config["password"] = parsed.password
    
    return proxy_config


# 探测站点必须满足两条：① 只有 A 记录（PySocks 不认 IPv6，urllib 挑中 AAAA 就抛
# "PySocks doesn't support IPv6"，代理明明能用却被判失败）；② 不嫌弃云机房出口 IP。
# api.ipify.org 看着两条都行，实际第②条不过 —— 它会把机房出口的 TLS 直接重置
# （本机 curl 也复现出同一个 "Connection was reset"），2026-10-31 因此连坏四轮，
# 把「探针插不通」报成「代理服务端坏了」。checkip.amazonaws.com 两条都过。
SOCKS_PROBE_URL = "https://checkip.amazonaws.com"

# HTTP 代理那条走 requests，不受 PySocks 的 IPv6 限制，所以老地址照用
HTTP_PROBE_URL = "https://www.cloudflare.com"

# 代理协议白名单：build_cloak_proxy_config 和 test_proxy 共用一份，别各写一遍
PROXY_SCHEMES = ["http", "https", "socks5", "socks5h"]


def _hide(msg, proxy_url: str = "") -> str:
    """代理的报错要进公开日志，里面常混着主机名和账号密码 —— 按代理 URL 自己遮。"""
    out = mask_personal(str(msg or ""))
    if proxy_url:
        parsed = urllib.parse.urlparse(proxy_url)
        out = out.replace(proxy_url, mask_proxy_url(proxy_url))
        if parsed.password:
            out = out.replace(parsed.password, "***")
        if parsed.username:
            out = out.replace(parsed.username, "***")
        if parsed.hostname:
            host = parsed.hostname
            out = out.replace(host, f"***.{host.split('.')[-1]}" if "." in host else "***")
    return out


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("对端在握手中途断开")
        buf += chunk
    return buf


def socks5_handshake(host, port, username=None, password=None, timeout: int = 10):
    """只跟代理服务器把 SOCKS5 招呼打完，不碰任何第三方站点。

    以前只有一句 SSL 错，把「连不上你的服务器」「账密不对」「服务器活着但出口到不了外面」
    压成同一条；这三层的修法完全不同，所以先单独问第一层。
    """
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError as e:
        return False, f"连不上代理服务器（{e.__class__.__name__}: {e}）"
    s.settimeout(timeout)
    try:
        user = username.encode() if username else None
        pwd = password.encode() if password else None
        s.sendall(bytes([5, 2, 0, 2]) if (user and pwd) else bytes([5, 1, 0]))
        chosen = _recv_exact(s, 2)
        if chosen[0] != 5:
            return False, (f"对端首字节是 {chosen[0]}、不是 SOCKS5 —— 这个端口多半是 HTTP 代理，"
                           f"PROXY_URL 要写成 http://")
        if chosen[1] == 0:
            return True, "SOCKS5 握手成功"
        if chosen[1] == 2:
            if not (user and pwd):
                return False, ("代理服务器要求账号密码，URL 里没带 "
                               "（写成 socks5://用户:密码@主机:端口）")
            s.sendall(bytes([1, len(user)]) + user + bytes([len(pwd)]) + pwd)
            st = _recv_exact(s, 2)
            if st[1] == 0:
                return True, "SOCKS5 握手成功（账号密码已通过）"
            return False, "账号密码被代理服务器拒绝"
        return False, f"服务端选了不支持的认证方法 {chosen[1]}"
    except OSError as e:
        return False, f"握手时断开（{e.__class__.__name__}: {e}）"
    finally:
        s.close()


def _socks_probe(parsed, timeout: int, retries: int):
    """隔着代理够一下探测站点 —— 问的是「出口到不到得了外面」，不是「代理活着没」。"""
    import socks

    for attempt in range(retries):
        try:
            if parsed.username and parsed.password:
                socks.set_default_proxy(socks.SOCKS5, parsed.hostname, parsed.port,
                                        username=parsed.username.encode(),
                                        password=parsed.password.encode())
            else:
                socks.set_default_proxy(socks.SOCKS5, parsed.hostname, parsed.port)
            original_socket = socket.socket
            socket.socket = socks.socksocket
            try:
                import urllib.request
                req = urllib.request.Request(SOCKS_PROBE_URL,
                                             headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status = resp.getcode()
                    if 200 <= status < 400:
                        # 正文就是出口 IP 本身，公开日志里不能出现，只报状态码
                        return True, f"状态码 {status}"
                    return False, f"状态码异常: {status}"
            finally:
                socket.socket = original_socket
        except ImportError:
            return False, "sock 模块未安装，请安装: pip install PySocks"
        except Exception as e:
            error_msg = _hide(str(e))
            if attempt < retries - 1:
                print(f"    [*] 探测站点没回话，重试 ({attempt + 1}/{retries}): {error_msg}")
                time.sleep(2)
                continue
            return False, error_msg
    return False, f"重试 {retries} 次后仍然失败"


def _tcp_open(host, port, timeout: int = 10) -> bool:
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError:
        return False
    s.close()
    return True


def test_proxy(proxy_url: str, timeout: int = 10, retries: int = 3):
    """分两层问：① 代理服务器本身通不通、账密对不对；② 它的出口够不够得着探测站点。

    第一个返回值是「这一轮要不要用代理」，所以只有第①层失败才否决。第②层失败只是
    探测站点自己的态度（它可能就是不欢迎机房出口 IP），照用代理，话留在日志里。
    """
    if not proxy_url:
        return False, "代理 URL 为空"

    parsed = urllib.parse.urlparse(proxy_url)
    if parsed.scheme not in PROXY_SCHEMES:
        return False, _hide(
            f"不支持的代理协议: {parsed.scheme or '（没写协议名）'}；支持 "
            + "、".join(f"{s}://" for s in PROXY_SCHEMES), proxy_url)
    if not parsed.hostname or not parsed.port:
        return False, _hide(
            "代理 URL 格式无效：必须是 协议://[账号:密码@]主机:端口，端口不能省", proxy_url)

    if parsed.scheme.startswith("socks5"):
        hand_ok, hand_msg = socks5_handshake(parsed.hostname, parsed.port,
                                             parsed.username or None,
                                             parsed.password or None, timeout)
        if not hand_ok:
            return False, _hide(f"和代理服务器握手失败: {hand_msg}", proxy_url)
        probe_ok, probe_msg = _socks_probe(parsed, timeout, retries)
        if probe_ok:
            return True, _hide(f"SOCKS5 握手成功，出口可达（{probe_msg}）", proxy_url)
        return True, _hide(
            f"SOCKS5 握手成功，但探测站点没回话: {probe_msg} —— 那是探测站点的态度，"
            f"不代表代理坏了，这一轮照样用代理签到", proxy_url)

    if not _tcp_open(parsed.hostname, parsed.port, timeout):
        return False, _hide("连不上代理服务器（TCP 端口不通）", proxy_url)
    probe_ok, probe_msg = _http_probe(proxy_url, timeout, retries)
    if probe_ok:
        return True, _hide(f"TCP 可达，出口可达（{probe_msg}）", proxy_url)
    return True, _hide(f"TCP 可达，但探测站点没回话: {probe_msg} —— 这一轮照样用代理签到",
                       proxy_url)


def _http_probe(proxy_url: str, timeout: int, retries: int) -> tuple[bool, str]:
    """隔着 HTTP 代理够一下探测站点（IPv6 由 requests 处理，不受 PySocks 那个限制）"""
    import requests

    proxies = {
        'http': proxy_url,
        'https': proxy_url,
    }
    
    for attempt in range(retries):
        try:
            response = requests.get(HTTP_PROBE_URL, proxies=proxies, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0"
            })
            status = response.status_code
            if 200 <= status < 400:
                return True, f"状态码 {status}"
            else:
                return False, f"状态码异常: {status}"
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if attempt < retries - 1:
                print(f"    [*] 探测站点没回话，重试 ({attempt + 1}/{retries}): {error_msg}，重试...")
                time.sleep(2)
                continue
            else:
                return False, f"请求异常: {error_msg}"
        except Exception as e:
            if attempt < retries - 1:
                print(f"    [*] 探测站点没回话，重试 ({attempt + 1}/{retries}): {e}，重试...")
                time.sleep(2)
                continue
            else:
                return False, f"未知错误: {e}"
    
    return False, f"重试 {retries} 次后仍然失败"


# ── 代理显示辅助函数 ─────────────────────────────────────────────────────────

def mask_proxy_url(proxy_url: str) -> str:
    """
    隐藏代理 URL 中的敏感信息（完全隐藏主机名）
    
    Args:
        proxy_url: 原始代理 URL
    
    Returns:
        隐藏后的代理 URL
    """
    if not proxy_url:
        return ""
    
    result = proxy_url
    
    # 1. 先隐藏主机名
    def mask_hostname(host):
        parts = host.split('.')
        if len(parts) >= 2:
            tld = parts[-1]
            return f"***.{tld}"
        else:
            return "***"
    
    # 匹配并替换主机名：://(用户名:密码@)?主机名
    def replace_host(match):
        auth = match.group(1) or ""  # 认证信息 (user:pass@)
        host = match.group(2)        # 主机名
        full = match.group(0)
        suffix = full[len(auth) + len(host) + 3:]  # +3 是 ://
        return f"://{auth}{mask_hostname(host)}{suffix}"
    
    result = re.sub(r'://([^:@]+:[^@]+@)?([^:/@]+)', replace_host, result)
    
    # 2. 隐藏认证信息 (user:pass@)
    result = re.sub(r'://[^:]+:[^@]+@', r'://***:***@', result)
    
    # 3. 隐藏端口号
    def mask_port(match):
        port = match.group(1)
        if len(port) <= 2:
            return f":{port[0]}{'*' * len(port)}"
        else:
            return f":{port[:2]}{'*' * (len(port) - 2)}"
    
    result = re.sub(r':(\d+)(?=/|$)', mask_port, result)
    
    return result


# ── Bark 通知 ──────────────────────────────────────────────────────────────────

def send_bark_notification(title: str, body: str, is_critical: bool = False) -> bool:
    """
    发送 Bark 通知到 iOS 设备
    
    Args:
        title: 通知标题
        body: 通知正文
        is_critical: 是否为紧急通知（失败时设为 True，会带上 critical 参数）
    """
    import requests
    
    bark_key = os.getenv("BARK_KEY")
    bark_url = os.getenv("BARK_URL", "https://api.day.app")
    
    if not bark_key:
        print("    [!] 未配置 BARK_KEY 环境变量，跳过通知")
        return False
    
    # 检查是否需要通过代理发送通知
    proxy_url = os.getenv("PROXY_URL")
    proxies = None
    if proxy_url and proxy_url.startswith(('http://', 'https://')):
        proxy_dict = {
            'http': proxy_url,
            'https': proxy_url,
        }
        proxies = proxy_dict
    
    try:
        # Bark API 格式: https://api.day.app/{key}/{title}/{body}
        encoded_title = urllib.parse.quote(title, safe='')
        encoded_body = urllib.parse.quote(body, safe='')
        
        # 构建基础 URL
        base_url = f"{bark_url}/{bark_key}/{encoded_title}/{encoded_body}"
        
        # 构建查询参数
        params = {
            "sound": "minuet",
            "icon": "https://github.com/fluidicon.png",
            "group": "PT签到"
        }
        
        # 如果是紧急通知，添加 critical 和 volume 参数
        if is_critical:
            params["level"] = "critical"
            params["volume"] = "0"
            print("    [*] 发送紧急通知（签到失败）")
        else:
            params["level"] = "active"
        
        # 发送请求
        response = requests.get(base_url, params=params, timeout=10, proxies=proxies)
        
        if response.status_code == 200:
            print("    [+] Bark 通知发送成功")
            return True
        else:
            print(f"    [!] Bark 通知发送失败: HTTP {response.status_code}")
            print(f"        响应内容: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"    [!] Bark 通知异常: {e}")
        return False


# ── 验证码处理 ────────────────────────────────────────────────────────────────

def handle_captcha(page, site_config: dict) -> bool:
    if not site_config.get("login_captcha"):
        return True

    captcha_field = site_config["login_fields"].get("captcha_code", "imagestring")
    has_captcha = page.evaluate(f"() => document.querySelector('input[name=\"{captcha_field}\"]') !== null")
    if not has_captcha:
        return True

    img_selector = site_config.get("captcha_image_selector", 'img[alt="CAPTCHA"]')
    img_src = page.evaluate(f"""() => {{
        const img = document.querySelector('{img_selector}');
        return img ? img.src : null;
    }}""")

    if not img_src:
        print("    [!] 未找到验证码图片")
        return False

    print(f"    [*] 需要验证码，图片地址：{img_src[:120]}")

    cap_path = BASE_DIR / f"captcha_{site_config['name']}.png"
    try:
        import urllib.request
        cookies = page.context.cookies()
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        req = urllib.request.Request(img_src.replace("&amp;", "&"), headers={
            "Cookie": cookie_header,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            cap_path.write_bytes(resp.read())
        print(f"    [*] 验证码已保存至：{cap_path}")
    except Exception as e:
        print(f"    [!] 无法下载验证码：{e}")

    code = input(f"    请输入 {site_config['name']} 的验证码：").strip()
    if not code:
        print("    [-] 未输入验证码，跳过登录")
        return False

    page.fill(f'input[name="{captcha_field}"]', code)

    hash_field = site_config["login_fields"].get("captcha_hash")
    if hash_field:
        imagehash = img_src.split("imagehash=")[-1] if "imagehash=" in img_src else ""
        if imagehash:
            page.evaluate(f"""() => {{
                const el = document.querySelector('input[name="{hash_field}"]');
                if (el) el.value = '{imagehash}';
            }}""")

    return True


# ── 登录 ──────────────────────────────────────────────────────────────────────

def login(page, site_config: dict) -> bool:
    site_url = site_config["url"]
    login_url = f"{site_url}{site_config['login_path']}"
    fields = site_config["login_fields"]

    print(f"    [*] 登录 {site_url} ...")
    page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    # 检查是否被雷池WAF拦截
    page_text = page.evaluate("() => document.body ? document.body.innerText : ''")
    if "雷池WAF" in page_text or "安全检测能力由雷池WAF驱动" in page_text:
        if not wait_past_leichi(page, timeout=180):
            return False

    if not wait_past_cf(page, timeout=180):
        print("    [-] Cloudflare 验证超时")
        return False

    print(f"        页面：{page.title()}")

    already = page.evaluate("""() => {
        for (const a of document.querySelectorAll('a')) {
            if (a.href && (a.href.includes('userdetails') || a.href.includes('logout')))
                return true;
        }
        return false;
    }""")
    if already:
        save_cookies(page.context, site_config["name"])
        print("    [+] Cookies 有效，已登录")
        return True

    if not handle_captcha(page, site_config):
        return False

    page.fill(f'input[name="{fields["username"]}"]', site_config["username"])
    page.fill(f'input[name="{fields["password"]}"]', site_config["password"])
    page.click('input[type="submit"]')
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    time.sleep(5)

    logged_in = page.evaluate("""() => {
        for (const a of document.querySelectorAll('a')) {
            if (a.href && (a.href.includes('userdetails') || a.href.includes('logout')))
                return true;
        }
        return false;
    }""")

    if logged_in:
        save_cookies(page.context, site_config["name"])
        print("    [+] 登录成功，cookies 已保存")
        return True

    error = page.evaluate("""() => {
        const row = document.querySelector('td.embedded, td.text, .error');
        return row ? row.textContent.substring(0, 200) : '';
    }""")
    print(f"    [-] 登录失败：{error[:150]}" if error else "    [-] 登录失败")
    return False


# ── 签到 ──────────────────────────────────────────────────────────────────────

# 页面出现过这些词，才算真的签到过（或今天不用再签）
SUCCESS_KEYWORDS = ["已经签到", "已签到", "已經簽到", "已簽到", "签到成功", "簽到成功", "今日已签到", "重复签到", "请勿重复打卡"]
# 站点明确告诉我们没签成
FAIL_KEYWORDS = ["验证失败", "验证错误", "请重新验证", "验证码错误", "请先完成验证", "签到失败"]


def classify_page(page_text: str, url_path: str = "", title: str = "", http_status: int = 0) -> tuple[str, str]:
    """把一页正文判成 success / fail / waf / blocked / login / unknown 之一。

    只有正面证据才给 success；unknown 表示这页上没有任何签到结果，调用方必须当作
    "没签成"处理，不能当作成功。第二项是判据，只含关键词、路径、状态码和长度，不含页面原文。
    """
    t = page_text or ""
    low = (t + " " + (title or "")).lower()
    for kw in SUCCESS_KEYWORDS:
        if kw in t:
            return "success", f"关键词「{kw}」"
    for kw in FAIL_KEYWORDS:
        if kw in t:
            return "fail", f"关键词「{kw}」"
    if http_status in (401,):
        return "login", f"HTTP {http_status}，登录态已失效"
    if http_status in (403, 429, 503):
        return "blocked", f"HTTP {http_status}，被拦截"
    if http_status >= 400:
        # piggo 那天回的是 468（WAF 自定义码），不在标准名单里也一样是挡架
        return "blocked", f"HTTP {http_status}，站点没给出签到页"
    if "雷池" in t:
        return "waf", "仍停在雷池WAF验证页"
    if "just a moment" in low or "attention required" in low or "have been blocked" in low:
        return "blocked", "停在 Cloudflare 拦截/验证页"
    if url_path.endswith("login.php"):
        return "login", f"被送回登录页（{url_path}），Cookie 已失效或不足"
    if "用户名" in t and "密码" in t:
        return "login", "页面上是登录表单，Cookie 已失效或不足"
    return "unknown", f"页面上没有任何签到结果证据（正文 {len(t)} 字符）"


def url_path_of(page) -> str:
    try:
        return urllib.parse.urlparse(page.url).path
    except Exception:
        return ""


def await_page_text(page, tries: int = 4) -> str:
    """页面正在跳转时读 innerText 会抛异常；等它稳定后把正文读出来，读不到返回空串。"""
    for _ in range(tries):
        try:
            return page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            time.sleep(3)
    return ""


# 分辨"是谁在挡"用的词：只报命中与否，绝不把原文打进日志
_BLOCK_MARKERS = ["雷池", "safeline", "waf", "防火墙", "拦截", "blocked", "forbidden",
                  "拒绝", "cloudflare", "just a moment", "captcha", "机器人"]
# 这一页到底是干什么用的：签到表单？还是要先过站点自己那道验证？只看这些词在不在场。
_CLUE_WORDS = ["安全码", "验证码", "验证", "滑块", "人机", "点击", "刷新", "输入", "签到", "补签",
               "连续", "魔力", "排名", "登录", "登陆", "用户名", "密码", "已签到", "尚未", "错误",
               "出错", "抱歉", "无法", "暂时", "关闭", "退出", "注册", "重新登录", "权限", "维护"]

# 页面的骨架：数个数、不抄内容。判据只能说「这页没签到证据」，这些数能说出为什么。
# piggo 这一站压根没有签到按钮 —— 过掉验证之后站点自己就签了，所以「验证部件在不在」
# 才是关键形状，光数表单看不出来。
_PAGE_SHAPE_JS = """() => {
    const q = (s) => document.querySelectorAll(s).length;
    const href = (frag) => Array.from(document.querySelectorAll('a'))
        .filter(a => (a.href || '').indexOf(frag) >= 0).length;
    const urls = (sel, key) => Array.from(document.querySelectorAll(sel)).map(e => {
        try { const u = new URL(e[key] || '', location.href); return key === 'src' ? u.host : u.pathname; }
        catch (err) { return ''; }
    }).filter(Boolean).slice(0, 4).join(',');
    const dims = (sel) => Array.from(document.querySelectorAll(sel))
        .map(e => Math.round(e.offsetWidth) + 'x' + Math.round(e.offsetHeight)).join(',');
    return {
        forms: q('form'), inputs: q('input'), links: q('a'),
        att_form: q('#attendance') + q('form[action*="attendance"]'),
        login_links: href('login.php'),
        member_links: href('logout') + href('userdetails'),
        captcha: q('input[name*="code"]') + q('input[id*="code"]')
                 + q('img[src*="imagecode"]') + q('img[src*="captcha"]'),
        pass_inputs: q('input[type="password"]'),
        iframes: q('iframe'),
        frame_hosts: urls('iframe', 'src'),
        frame_dims: dims('iframe'),
        form_actions: urls('form', 'action'),
        verify_widgets: q('.cf-turnstile') + q('[id*="cf-challenge"]')
                        + q('input[name*="turnstile"]') + q('input[name*="challenge-response"]')
                        + q('[class*="challenge"]') + q('[id*="seccode"]')
                        + q('[id*="safeline"]') + q('[class*="safeline"]')
                        + q('[id*="captcha"]') + q('canvas'),
        buttons: q('button') + q('input[type="submit"]')
    };
}"""


def mask_personal(s: str) -> str:
    """把可能落在公开日志里的东西抠掉：完整 IPv4、长数字/十六进制串（uid、token 片段）。"""
    s = re.sub(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "*.*.*.*", str(s or ""))
    return re.sub(r"[0-9a-f]{16,}", "***", s)


def _diag_page(page, resp) -> str:
    """非成功判定时补一行信号：标题、响应头 Server、拦截词/线索词、正文长度、页面骨架计数。

    判据只能告诉我们「这页不是签到结果」，告诉不了是谁挡的、这一页是干什么的 —— 而下一步
    该改 Cookie、该换出口，还是这站压根要人工过一道验证，取决于这个。所以只报站点自己的
    措辞（标题、响应头、白名单词是否在场）和元素个数，绝不报正文：NexusPHP 页面的正文里有
    账号名，库是公开的。
    """
    parts = []
    title = ""
    try:
        title = (page.title() or "").strip().replace("\n", " ")
    except Exception:
        title = ""
    parts.append(f"标题={mask_personal(title[:60]) if title else '空'}")
    server = ""
    try:
        headers = resp.headers if resp is not None else None
        if headers:
            server = headers.get("server") or headers.get("Server") or ""
    except Exception:
        server = ""
    parts.append(f"Server={mask_personal(server[:40]) if server else '无'}")
    try:
        body = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        body = ""
    low = (body + " " + title).lower()
    hits = [m for m in _BLOCK_MARKERS if m in low]
    clues = [w for w in _CLUE_WORDS if w in low]
    parts.append(f"拦截词={'、'.join(hits) if hits else '无'}")
    parts.append(f"线索词={'、'.join(clues) if clues else '无'}")
    parts.append(f"正文={len(body)}字符")
    try:
        sh = page.evaluate(_PAGE_SHAPE_JS) or {}
    except Exception:
        sh = {}
    if isinstance(sh, dict) and sh:
        parts.append(
            "页面形状=表单{}/输入{}/链接{}/签到表单{}/登录链接{}/会员链接{}/iframe{}/验证部件{}/按钮{}".format(
                sh.get("forms", "?"), sh.get("inputs", "?"), sh.get("links", "?"),
                sh.get("att_form", "?"), sh.get("login_links", "?"),
                sh.get("member_links", "?"), sh.get("iframes", "?"),
                sh.get("verify_widgets", "?"), sh.get("buttons", "?")))
        frames = str(sh.get("frame_hosts", ""))[:120]
        dims = str(sh.get("frame_dims", ""))[:80]
        acts = str(sh.get("form_actions", ""))[:80]
        if frames:
            parts.append(f"iframe域名={mask_personal(frames)}")
        if dims:
            parts.append(f"iframe尺寸={mask_personal(dims)}")
        if acts:
            parts.append(f"表单动作={mask_personal(acts)}")
        if sh.get("captcha") or sh.get("pass_inputs"):
            parts.append(f"验证码框={sh.get('captcha', 0)} 密码框={sh.get('pass_inputs', 0)}")
    return " / ".join(parts)


def _retry_after_home(page, site_config: dict) -> tuple[str, str, int]:
    """硬挡（4xx / 拦截页）时的最后一搏：先回首页让 WAF 认下这个会话，再进签到页。

    hdkyl、hitpt 直接进签到页是通的，piggo 回 468 —— 「首页通不通」和「签到页通不通」
    分开报出来，本身就能分辨是整站封出口还是只拦 attendance.php 这一条深链。
    返回第二次的 (判定, 判据, 状态码)：状态码要交回调用方顶掉第一次那个 468，否则后面
    每一步判读都顶着早已过去的错误码说话。
    """
    site_url = site_config["url"]
    checkin_url = f"{site_url}{site_config['checkin_path']}"
    print("    [*] 先访问首页再进签到页重试一次...")
    home_status = 0
    try:
        home = page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
        home_status = home.status if home is not None else 0
    except Exception as e:
        print(f"    [!] 首页没打开: {type(e).__name__}")
    time.sleep(5)
    att_status = 0
    resp2 = None
    try:
        resp2 = page.goto(checkin_url, wait_until="domcontentloaded", timeout=60000)
        att_status = resp2.status if resp2 is not None else 0
    except Exception as e:
        print(f"    [!] 签到页没打开: {type(e).__name__}")
    time.sleep(5)
    verdict2, reason2 = classify_page(await_page_text(page), url_path_of(page),
                                      page.title(), att_status)
    print(f"    [DEBUG] 重试：首页 HTTP {home_status} / 签到页 HTTP {att_status} / 判定: {verdict2}")
    print(f"    [诊断] {_diag_page(page, resp2)}")
    return verdict2, reason2, att_status



def do_checkin(page, site_config: dict) -> bool:
    site_url = site_config["url"]
    checkin_url = f"{site_url}{site_config['checkin_path']}"
    checkin_type = site_config.get("checkin_type", "turnstile")
    name = site_config.get("name", "")

    print(f"    [*] 访问签到页面 {checkin_url} ...")
    
    # 访问页面，等待网络空闲（确保页面完全加载，包括可能的跳转）
    http_status = 0
    try:
        resp = page.goto(checkin_url, wait_until="networkidle", timeout=60000)
        http_status = resp.status if resp is not None else 0
    except Exception:
        # 如果 networkidle 超时，改用 domcontentloaded
        resp = page.goto(checkin_url, wait_until="domcontentloaded", timeout=60000)
        http_status = resp.status if resp is not None else 0
    
    # 等待页面稳定，给跳转足够时间
    time.sleep(8)
    
    # 尝试获取页面内容，如果发生跳转则等待新页面
    page_text = ""
    for attempt in range(3):
        try:
            page_text = page.evaluate("() => document.body ? document.body.innerText : ''")
            break
        except Exception as e:
            if "Execution context was destroyed" in str(e):
                print(f"    [*] 页面跳转中，等待跳转完成... (尝试 {attempt + 1}/3)")
                time.sleep(5)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=30000)
                    time.sleep(3)
                except Exception:
                    pass
                continue
            raise e
    else:
        # 3次尝试都失败，页面可能仍在跳转，再读一次
        print("    [*] 页面持续跳转，再读一次当前状态...")
        page_text = await_page_text(page)
        if not page_text:
            print("    [-] 读不到页面正文，无法确认签到结果")
            return False
    
    # 检查是否被雷池WAF拦截（如果当前页面是雷池验证页面）
    if "雷池WAF" in page_text or "安全检测能力由雷池WAF驱动" in page_text:
        if not wait_past_leichi(page, timeout=300):
            return False
        # 雷池验证通过后，等待页面跳转/刷新
        print("    [*] 雷池验证通过，等待页面跳转...")
        time.sleep(8)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        time.sleep(3)
        # 重新获取页面内容
        try:
            page_text = page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception as e:
            if "Execution context was destroyed" in str(e):
                page_text = await_page_text(page)
                if not page_text:
                    print("    [-] 雷池通过后页面仍读不到，无法确认签到结果")
                    return False
            else:
                raise e
        print(f"    [DEBUG] 跳转后页面内容长度: {len(page_text)}")

    # 等待 Cloudflare 验证完成
    if not wait_past_cf(page, timeout=240):
        print("    [-] Cloudflare 验证超时")
        return False

    # 检查签到状态
    time.sleep(3)
    try:
        page_text = page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception as e:
        if "Execution context was destroyed" in str(e):
            page_text = await_page_text(page)
            if not page_text:
                print("    [-] 页面持续跳转，读不到签到结果")
                return False
        else:
            raise e

    verdict, reason = classify_page(page_text, url_path_of(page), page.title(), http_status)
    print(f"    [DEBUG] HTTP {http_status} / 页面内容长度: {len(page_text)} / 判定: {verdict}")

    if verdict == "success":
        print(f"    [OK] 签到状态已确认（{reason}）")
        return True
    print(f"    [诊断] {_diag_page(page, resp)}")
    if verdict in ("blocked", "waf"):
        # 硬挡过一次不代表这站不给签：回首页 warm-up 后再看一眼拿到的是什么页。
        # 仍是 blocked/waf 就顺着下面的 verdict != unknown 报红；unknown 则往下真提交一次。
        verdict, reason, http_status = _retry_after_home(page, site_config)
        if verdict == "success":
            print(f"    [OK] 重试后确认已签到（{reason}）")
            return True
    if verdict != "unknown":
        print(f"    [-] 签到未成功: {reason}")
        return False

    # auto 模式过去在这里直接算成功——那是假的：这一页上没有任何签到证据。
    # 现在真去提交一次，签没签成以页面回话为准。
    if checkin_type == "auto":
        print("    [*] 未见签到证据，尝试提交签到表单...")
        if _checkin_form(page) or _checkin_link_click(page):
            return True
        verdict2, reason2 = classify_page(await_page_text(page), url_path_of(page),
                                         page.title(), http_status)
        if verdict2 == "success":
            print(f"    [OK] 提交后确认已签到（{reason2}）")
            return True
        print(f"    [-] 提交后仍未确认签到（判定: {verdict2}，{reason2}）")
        return False

    # 其他签到模式
    if checkin_type == "turnstile":
        return _checkin_turnstile(page)
    elif checkin_type == "form":
        return _checkin_form(page)
    elif checkin_type == "link_click":
        return _checkin_link_click(page)

    # 通用回退：依次尝试
    return _checkin_form(page) or _checkin_link_click(page) or _checkin_turnstile(page)


def _checkin_form(page) -> bool:
    submitted = page.evaluate("""() => {
        const form = document.getElementById('attendance');
        if (form) { form.submit(); return true; }
        for (const f of document.querySelectorAll('form')) {
            if (f.action && f.action.includes('attendance')) { f.submit(); return true; }
        }
        return false;
    }""")
    if submitted:
        time.sleep(3)
        body = page.evaluate("() => document.body ? document.body.innerText : ''")
        if any(k in body for k in ["成功", "已经签到", "已签到", "已經簽到"]):
            print("    [+] 签到成功！")
            return True
    return False


def _checkin_link_click(page) -> bool:
    clicked = page.evaluate("""() => {
        for (const a of document.querySelectorAll('a')) {
            const t = a.textContent || '';
            if (t.includes('签到') || t.includes('簽到') || t.includes('魔力')) {
                a.click(); return true;
            }
        }
        return false;
    }""")
    if clicked:
        time.sleep(3)
        body = page.evaluate("() => document.body ? document.body.innerText : ''")
        if any(k in body for k in ["成功", "已经签到", "已签到", "已經簽到"]):
            print("    [+] 签到成功！")
            return True
    return False


def _checkin_turnstile(page) -> bool:
    """
    处理 Cloudflare Turnstile 验证并提交签到
    """
    # 1. 等待 Turnstile 容器加载
    try:
        print("    [*] 检测 Turnstile 验证组件...")
        page.wait_for_selector('div.cf-turnstile', timeout=15000)
        print("    [+] Turnstile 容器已加载，等待自动验证...")
    except Exception:
        print("    [*] 未显式检测到 Turnstile 容器，继续尝试")

    # 2. 等待 Turnstile 自动完成（最长等待 90 秒）
    verified = False
    for attempt in range(30):  # 30 次 * 3 秒 = 90 秒
        time.sleep(3)
        try:
            # 检测 Turnstile 是否已完成（通过检查隐藏响应字段或状态变化）
            has_response = page.evaluate("""() => {
                // 检查是否存在 Turnstile 的响应字段
                const responseInput = document.querySelector('input[name="cf-turnstile-response"]');
                if (responseInput && responseInput.value) return true;
                // 或者检查 Turnstile 状态（通过 iframe 或类变化）
                const turnstile = document.querySelector('.cf-turnstile');
                if (turnstile && turnstile.getAttribute('data-response')) return true;
                return false;
            }""")
            if has_response:
                print(f"    [+] Turnstile 验证完成 (尝试 {attempt+1}/30)")
                verified = True
                break
            print(f"    [*] 等待 Turnstile 验证... ({attempt+1}/30)")
        except Exception:
            pass

    if not verified:
        print("    [!] Turnstile 验证超时，尝试强制提交")
        # 即使未检测到验证完成，也尝试提交（有些站点在无头环境下可能自动通过）

    # 3. 等待提交按钮出现并点击
    time.sleep(2)
    try:
        print("    [*] 尝试提交签到表单...")
        submit_result = page.evaluate("""() => {
            // 优先查找提交按钮
            const submitBtn = document.querySelector('input[type="submit"][value*="签到"]');
            if (submitBtn) { 
                submitBtn.click(); 
                return 'clicked_button';
            }
            // 通过表单提交
            const attendanceForm = document.querySelector('form[action*="attendance"]');
            if (attendanceForm) {
                attendanceForm.submit();
                return 'submitted_form';
            }
            // 回退：点击签到链接
            for (const a of document.querySelectorAll('a')) {
                const text = a.textContent || '';
                if (text.includes('签到') || text.includes('簽到')) {
                    a.click();
                    return 'clicked_link';
                }
            }
            return 'no_action';
        }""")
        print(f"    [+] 提交操作: {submit_result}")
    except Exception as e:
        print(f"    [!] 提交签到失败: {e}")
        return False

    # 4. 等待并验证签到结果（最长等待 60 秒）
    time.sleep(5)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            body = page.evaluate("() => document.body ? document.body.innerText : ''")
            # 检查成功关键词
            success_keywords = ["签到成功", "已经签到", "已签到", "已經簽到", "已簽到", "簽到成功", "今日已签到", "重复签到", "请勿重复打卡"]
            for kw in success_keywords:
                if kw in body:
                    print(f"    [+] 签到成功！(检测到关键词: {kw})")
                    return True
            # 检查失败关键词
            fail_keywords = ["验证失败", "验证错误", "请重新验证", "验证码错误", "请先完成验证"]
            for kw in fail_keywords:
                if kw in body:
                    print(f"    [-] 签到失败: {kw}")
                    return False
        except Exception:
            pass
        time.sleep(3)

    print("    [-] 签到提交后未检测到明确结果")
    return False


# ── 已登录检测 ────────────────────────────────────────────────────────────────

def check_logged_in(page, site_config: dict) -> bool:
    site_url = site_config["url"]
    page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    # 检查雷池WAF
    page_text = page.evaluate("() => document.body ? document.body.innerText : ''")
    if "雷池WAF" in page_text or "安全检测能力由雷池WAF驱动" in page_text:
        if not wait_past_leichi(page, timeout=180):
            return False

    if not wait_past_cf(page, timeout=120):
        return False

    return page.evaluate("""() => {
        const body = document.body ? document.body.innerText : '';
        if (body.includes('已登陆') || body.includes('控制面板') || body.includes('用户面板')) return true;
        for (const a of document.querySelectorAll('a')) {
            if (a.href && (a.href.includes('userdetails') || a.href.includes('logout')))
                return true;
        }
        return false;
    }""")


# ── 单站点处理 ────────────────────────────────────────────────────────────────

def process_site(site_config: dict, proxy_config: dict | str | None = None, use_proxy: bool = False) -> bool:
    name = site_config["name"]
    print(f"\n{'=' * 50}")
    print(f"  站点：{name} ({site_config['url']})")
    print(f"{'=' * 50}")

    # 自动签到站点列表（使用 Cookie 直接签到，跳过登录检查）
    auto_sites = ["piggo", "hdkyl", "hitpt"]
    extra_args = []
    if name in auto_sites:
        extra_args = [
            "--disable-blink-features=AutomationControlled",
        ]
        print(f"    [*] {name} 特殊模式：启用雷池WAF兼容，使用Cookie直接签到")

    # 构建 launch 参数
    launch_args = {
        "user_data_dir": get_profile_dir(name),
        "headless": False,
        "geoip": True if use_proxy else False,
        "locale": "zh-CN",
        "timezone": "Asia/Shanghai",
        "humanize": True,
        "human_preset": "careful",
        "viewport": {"width": 1920, "height": 1080},
        "args": [
            "--disable-features=TrustedTypes",
            f"--fingerprint=checkin_{name}",
            "--fingerprint-noise=false",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
            "--start-maximized",
        ] + extra_args,
    }
    
    # 添加代理配置（如果可用）
    if use_proxy and proxy_config:
        launch_args["proxy"] = proxy_config
        print(f"    [*] 使用代理进行签到")
    
    context = launch_persistent_context(**launch_args)
    page = context.new_page()

    try:
        has_cookies = load_cookies(context, name)
        
        # 对使用 Cookie 直接签到的站点
        if name in auto_sites:
            if has_cookies:
                print(f"    [*] {name} 使用 Cookies 直接签到...")
                return do_checkin(page, site_config)
            else:
                print(f"    [!] {name} 未找到 Cookies，尝试登录...")
                if not login(page, site_config):
                    return False
                return do_checkin(page, site_config)
        
        # 其他站点正常流程
        logged_in = has_cookies and check_logged_in(page, site_config)

        if not logged_in:
            print("    [!] 未登录，尝试登录...")
            if not login(page, site_config):
                return False

        return do_checkin(page, site_config)

    except Exception as e:
        print(f"    [-] 错误：{e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        context.close()


# ── 主函数 ────────────────────────────────────────────────────────────────────

def skip_for_missing_proxy(site: dict, use_proxy: bool) -> str | None:
    """站点要求必须走代理、而这一轮没有代理时，返回跳过原因；None 表示照常签到。"""
    if site.get("require_proxy") and not use_proxy:
        return "需要代理，本轮代理不可用（直连会被站点判异地登录并锁号）"
    return None


def main():
    config = load_config()
    sites = build_sites(config)

    if not sites:
        print("[!] config.json 中没有启用的站点，请运行 run.sh --setup 添加站点。")
        sys.exit(1)

    # 支持只对单个站点签到：python checkin.py <site_name>
    if len(sys.argv) > 1:
        target = sys.argv[1]
        sites = [s for s in sites if s["name"] == target]
        if not sites:
            names = [s["name"] for s in build_sites(config)]
            print(f"[!] 未知站点：{target}")
            print(f"    可用站点：{', '.join(names)}")
            sys.exit(1)

    print("=" * 50)
    print("  Cloak Browser 自动签到助手")
    print("=" * 50)

    # ── 全局代理配置（只检测一次） ──────────────────────────────────────────
    proxy_url = os.getenv("PROXY_URL")
    use_proxy = False
    proxy_config = None
    proxy_test_result = None
    
    if proxy_url:
        # 显示代理信息（隐藏敏感内容）
        proxy_display = mask_proxy_url(proxy_url)
        print(f"    [*] 检测到代理配置: {proxy_display}")
        
        # 测试代理是否可用（只测试一次）
        print(f"    [*] 正在测试代理连通性...")
        proxy_test_result = test_proxy(proxy_url, timeout=10, retries=3)
        
        if proxy_test_result[0]:
            use_proxy = True
            proxy_config = build_cloak_proxy_config(proxy_url)
            if proxy_config:
                print(f"    [✓] 代理可用: {proxy_test_result[1]}")
            else:
                print(f"    [✗] 代理解析失败")
                use_proxy = False
        else:
            print(f"    [✗] 代理不可用: {proxy_test_result[1]}")
    else:
        print("    [*] 未配置代理")

    if not use_proxy:
        strict = [s["name"] for s in sites if s.get("require_proxy")]
        if strict:
            print(f"    [*] 其余站点走直连；{'、'.join(strict)} 这一轮跳过（直连会被站点判异地登录）")
        else:
            print("    [*] 全部站点走直连")

    results: dict[str, str] = {}
    for site in sites:
        skip_reason = skip_for_missing_proxy(site, use_proxy)
        if skip_reason:
            print(f"    [!] {site['name']}：{skip_reason}")
            results[site["name"]] = f"SKIP: {skip_reason}"
            continue
        try:
            ok = process_site(site, proxy_config, use_proxy)
            results[site["name"]] = "OK" if ok else "FAIL"
        except Exception as e:
            results[site["name"]] = f"ERR: {e}"

    print(f"\n{'=' * 50}")
    print("  签到结果汇总")
    print(f"{'=' * 50}")
    
    # 统计结果并构建通知内容
    success_count = 0
    fail_count = 0
    details = []
    
    for name, status in results.items():
        icon = "✓" if status == "OK" else ("!" if status.startswith("SKIP") else "✗")
        print(f"  {icon} {name}: {status}")
        if status == "OK":
            success_count += 1
        else:
            fail_count += 1
        details.append(f"{icon} {name}: {status}")
    
    # 发送 Bark 通知
    if success_count > 0 or fail_count > 0:
        is_critical = (fail_count > 0)
        
        if is_critical:
            title = f" PT签到异常 - {fail_count} 个站点未完成"
            body = "\n".join(details)
        else:
            title = f" PT签到完成 - 全部成功 ({success_count} 个站点)"
            body = "\n".join(details)
        
        send_bark_notification(title, body, is_critical)

    # 有站点没签成就要让 Actions 红灯：过去进程总是 0 退出，"实际没签到"在
    # Actions 页面上也是一片绿，只有 Bark 那一条会响。
    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
