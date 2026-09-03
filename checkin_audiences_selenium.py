# checkin_audiences_selenium.py
import os
import sys
import time
import re
import socket
from urllib.parse import urlparse
from seleniumbase import Driver

# --- 配置 ---
PROXY_URL = os.environ.get("PROXY_URL")
AUDIENCES_COOKIE = os.environ.get("AUDIENCES_COOKIE")
SITE_URL = "https://audiences.me"
CHECKIN_URL = f"{SITE_URL}/attendance.php"

# --- 代理测试函数 (从您已有的代码移植) ---
def test_proxy(proxy_url: str, timeout: int = 10, retries: int = 3) -> tuple[bool, str]:
    """测试 SOCKS5 代理是否可用"""
    if not proxy_url:
        return False, "代理 URL 为空"
    
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return False, "代理 URL 格式无效"
    
    if parsed.scheme.startswith('socks5'):
        return _test_socks5_proxy(parsed, timeout, retries)
    elif parsed.scheme in ['http', 'https']:
        return _test_http_proxy(proxy_url, timeout, retries)
    else:
        return False, f"不支持的代理协议: {parsed.scheme}"

def _test_socks5_proxy(parsed, timeout: int, retries: int) -> tuple[bool, str]:
    import socks
    import urllib.request
    
    for attempt in range(retries):
        try:
            if parsed.username and parsed.password:
                socks.set_default_proxy(
                    socks.SOCKS5,
                    parsed.hostname,
                    parsed.port,
                    username=parsed.username,
                    password=parsed.password
                )
            else:
                socks.set_default_proxy(socks.SOCKS5, parsed.hostname, parsed.port)
            
            original_socket = socket.socket
            socket.socket = socks.socksocket
            
            try:
                req = urllib.request.Request(
                    "https://www.cloudflare.com",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status = resp.getcode()
                    if 200 <= status < 400:
                        return True, f"测试成功 (状态码: {status})"
                    else:
                        return False, f"状态码异常: {status}"
            except Exception as e:
                if attempt < retries - 1:
                    print(f"    [*] SOCKS5 代理测试失败 ({attempt + 1}/{retries}): {e}，重试...")
                    time.sleep(2)
                    continue
                else:
                    return False, f"连接失败: {e}"
            finally:
                socket.socket = original_socket
        except Exception as e:
            if attempt < retries - 1:
                print(f"    [*] SOCKS5 代理测试失败 ({attempt + 1}/{retries}): {e}，重试...")
                time.sleep(2)
                continue
            else:
                return False, f"测试异常: {e}"
    
    return False, f"重试 {retries} 次后仍然失败"

def _test_http_proxy(proxy_url: str, timeout: int, retries: int) -> tuple[bool, str]:
    import requests
    for attempt in range(retries):
        try:
            proxies = {'http': proxy_url, 'https': proxy_url}
            response = requests.get("https://www.cloudflare.com", proxies=proxies, timeout=timeout)
            if 200 <= response.status_code < 400:
                return True, f"测试成功 (状态码: {response.status_code})"
            else:
                return False, f"状态码异常: {response.status_code}"
        except Exception as e:
            if attempt < retries - 1:
                print(f"    [*] HTTP 代理测试失败 ({attempt + 1}/{retries}): {e}，重试...")
                time.sleep(2)
                continue
            else:
                return False, f"请求异常: {e}"
    return False, f"重试 {retries} 次后仍然失败"

# --- 主签到逻辑 ---
def main():
    print("=" * 50)
    print("  audiences.me SeleniumBase 签到测试")
    print("=" * 50)

    # 1. 代理测试 (使用您现有的逻辑)
    proxy_test_passed = False
    if PROXY_URL:
        # 隐藏密码输出 (与您现有代码一致)
        proxy_display = re.sub(r'://[^:]+:[^@]+@', r'://***:***@', PROXY_URL)
        print(f"    [*] 检测到代理配置: {proxy_display}")
        print(f"    [*] 正在测试代理连通性...")
        is_ok, msg = test_proxy(PROXY_URL)
        if is_ok:
            proxy_test_passed = True
            print(f"    [✓] 代理测试通过: {msg}")
        else:
            print(f"    [✗] 代理测试失败: {msg}")
            print(f"    [*] 将不使用代理进行签到")
    else:
        print("    [*] 未配置代理，使用直连")

    # 2. 检查 Cookie
    if not AUDIENCES_COOKIE:
        print("[!] 未设置 AUDIENCES_COOKIE 环境变量，无法继续")
        sys.exit(1)
    print("    [*] 检测到 AUDIENCES_COOKIE")

    # 3. 启动 SeleniumBase (集成代理)
    print("    [*] 启动浏览器...")
    driver = None
    try:
        # 构建 SeleniumBase 参数
        driver_args = {
            "uc": True,           # 启用 UC 模式 (核心)
            "headless": False,    # 建议本地测试时改为 False 观察，Actions 中可保持 True
            "uc_cdp": True,       # 启用 CDP (Chrome DevTools Protocol) 以增强 UC 模式
        }

        # 如果代理测试通过，添加代理配置
        if proxy_test_passed and PROXY_URL:
            parsed = urlparse(PROXY_URL)
            # SeleniumBase 的代理参数是 "proxy=协议://ip:端口"
            if parsed.scheme.startswith('socks5'):
                proxy_str = f"socks5://{parsed.hostname}:{parsed.port}"
                if parsed.username and parsed.password:
                    proxy_str = f"socks5://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"
                driver_args["proxy"] = proxy_str
                print(f"    [*] 浏览器使用代理: {parsed.hostname}:{parsed.port}")
            elif parsed.scheme in ['http', 'https']:
                proxy_str = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
                if parsed.username and parsed.password:
                    proxy_str = f"{parsed.scheme}://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"
                driver_args["proxy"] = proxy_str
                print(f"    [*] 浏览器使用代理: {parsed.hostname}:{parsed.port}")

        # 创建 Driver
        driver = Driver(**driver_args)
        driver.maximize_window()

        # 4. 访问签到页面并设置 Cookie
        print(f"    [*] 访问签到页面: {CHECKIN_URL}")
        driver.get(CHECKIN_URL)

        # 设置 Cookie (必须先访问页面)
        print("    [*] 注入 AUDIENCES_COOKIE...")
        for item in AUDIENCES_COOKIE.split(';'):
            if '=' in item:
                name, value = item.strip().split('=', 1)
                driver.add_cookie({"name": name, "value": value, "domain": ".audiences.me"})
        driver.refresh()
        time.sleep(3)

        # 5. 使用 UC 模式的核心方法绕过 Turnstile
        # 注意: uc_open_with_reconnect 会尝试自动处理验证并重连
        print("    [*] 尝试使用 UC 模式绕过 Turnstile (最多等待 60 秒)...")
        try:
            # 这个方法会在遇到验证时尝试自动处理
            driver.uc_open_with_reconnect(CHECKIN_URL, reconnect_time=3)
        except Exception as e:
            # 如果该方法失败，也可以使用更简单的等待方式作为备选
            print(f"    [!] uc_open_with_reconnect 失败: {e}")
            print("    [*] 切换到备用等待策略...")
            driver.wait_for_element_not_visible("iframe[src*='turnstile']", timeout=60)
            driver.wait_for_element_present("input[name='cf-turnstile-response'][value!='']", timeout=30)

        # 6. 检查签到结果
        print("    [*] 检查签到结果...")
        time.sleep(3)
        page_text = driver.page_source

        # 检查关键词 (与您现有逻辑一致)
        success_keywords = ["签到成功", "已经签到", "已签到", "今日已签到", "重复签到", "请勿重复打卡"]
        fail_keywords = ["验证失败", "验证错误", "请重新验证"]

        check_success = False
        for kw in success_keywords:
            if kw in page_text:
                check_success = True
                print(f"    [OK] 今日已签到 (检测到关键词: {kw})")
                break

        if not check_success:
            for kw in fail_keywords:
                if kw in page_text:
                    print(f"    [-] 签到失败: {kw}")
                    break
            else:
                # 如果页面内容较少，可能是还在验证中
                if len(page_text) < 100:
                    print("    [WARN] 页面内容较少，可能仍在验证中")
                    print("    [*] 等待 15 秒后再次检查...")
                    time.sleep(15)
                    page_text = driver.page_source
                    for kw in success_keywords:
                        if kw in page_text:
                            check_success = True
                            print(f"    [OK] 今日已签到 (检测到关键词: {kw})")
                            break
                    if not check_success:
                        print("    [WARN] 未检测到明确签到结果，请查看页面截图")
                        driver.save_screenshot("audiences_result.png")
                else:
                    print("    [WARN] 未检测到明确签到结果")
                    driver.save_screenshot("audiences_result.png")

        # 7. 如果签到成功，也保存页面源码用于调试
        if check_success:
            with open("audiences_success.html", "w", encoding="utf-8") as f:
                f.write(page_text)

    except Exception as e:
        print(f"    [-] 签到过程出错: {e}")
        if driver:
            try:
                driver.save_screenshot("audiences_error.png")
            except:
                pass
        return False
    finally:
        if driver:
            driver.quit()
            print("    [*] 浏览器已关闭")

    return check_success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
