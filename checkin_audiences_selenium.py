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

# --- 代理测试函数 ---
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

def mask_proxy_url(proxy_url: str) -> str:
    """隐藏代理 URL 中的敏感信息"""
    if not proxy_url:
        return ""
    result = proxy_url
    result = re.sub(r'://[^:]+:[^@]+@', r'://***:***@', result)
    def mask_hostname(match):
        host = match.group(1)
        parts = host.split('.')
        if len(parts) >= 2:
            tld = parts[-1]
            return f"***.{tld}"
        else:
            return "***"
    def replace_host(match):
        auth = match.group(1) or ""
        host = match.group(2)
        full = match.group(0)
        suffix = full[len(auth) + len(host) + 3:]
        return f"://{auth}{mask_hostname(match)}{suffix}"
    result = re.sub(r'://([^:@]+:[^@]+@)?([^:/@]+)', replace_host, result)
    result = re.sub(r'://[^:]+:[^@]+@', r'://***:***@', result)
    def mask_port(match):
        port = match.group(1)
        if len(port) <= 2:
            return f":{port[0]}{'*' * len(port)}"
        else:
            return f":{port[:2]}{'*' * (len(port) - 2)}"
    result = re.sub(r':(\d+)(?=/|$)', mask_port, result)
    return result


# --- 主签到逻辑 ---
def main():
    print("=" * 50)
    print("  audiences.me SeleniumBase 签到测试")
    print("=" * 50)

    # 1. 代理测试
    proxy_test_passed = False
    if PROXY_URL:
        proxy_display = mask_proxy_url(PROXY_URL)
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

    # 3. 启动 SeleniumBase
    print("    [*] 启动浏览器...")
    driver = None
    check_success = False
    
    try:
        driver_args = {
            "uc": True,
            "headless": True,
            "uc_cdp": True,
        }

        # 如果代理测试通过，添加代理配置
        if proxy_test_passed and PROXY_URL:
            parsed = urlparse(PROXY_URL)
            if parsed.scheme.startswith('socks5'):
                proxy_str = f"socks5://{parsed.hostname}:{parsed.port}"
                if parsed.username and parsed.password:
                    proxy_str = f"socks5://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"
                driver_args["proxy"] = proxy_str
                print(f"    [*] 浏览器使用代理")
            elif parsed.scheme in ['http', 'https']:
                proxy_str = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
                if parsed.username and parsed.password:
                    proxy_str = f"{parsed.scheme}://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"
                driver_args["proxy"] = proxy_str
                print(f"    [*] 浏览器使用代理")

        driver = Driver(**driver_args)
        driver.maximize_window()

        # 4. 访问签到页面并设置 Cookie
        print(f"    [*] 访问签到页面...")
        driver.get(CHECKIN_URL)

        print("    [*] 注入 Cookie...")
        for item in AUDIENCES_COOKIE.split(';'):
            if '=' in item:
                name, value = item.strip().split('=', 1)
                driver.add_cookie({"name": name, "value": value, "domain": ".audiences.me"})
        driver.refresh()
        time.sleep(5)

        # 5. 使用 UC 模式绕过 Turnstile
        print("    [*] 尝试绕过 Turnstile (最多等待 120 秒)...")
        
        # 方法1: uc_open_with_reconnect
        try:
            driver.uc_open_with_reconnect(CHECKIN_URL, reconnect_time=5)
            print("    [*] uc_open_with_reconnect 执行完成")
        except Exception as e:
            print(f"    [!] uc_open_with_reconnect 失败: {e}")
            print("    [*] 尝试备用策略...")
            # 备用策略：等待 Turnstile iframe 消失或 token 出现
            try:
                driver.wait_for_element_not_visible("iframe[src*='turnstile']", timeout=90)
            except:
                pass
            try:
                driver.wait_for_element_present("input[name='cf-turnstile-response'][value!='']", timeout=30)
            except:
                pass

        # 6. 等待并检查签到结果
        print("    [*] 检查签到结果...")
        time.sleep(5)
        
        # 尝试点击提交按钮（如果存在）
        try:
            submit_btn = driver.find_element("css selector", 'input[type="submit"][value*="签到"]')
            if submit_btn:
                submit_btn.click()
                print("    [*] 点击了签到按钮")
                time.sleep(5)
        except:
            pass

        page_text = driver.page_source
        print(f"    [DEBUG] 页面内容长度: {len(page_text)}")

        success_keywords = ["签到成功", "已经签到", "已签到", "今日已签到", "重复签到", "请勿重复打卡"]
        fail_keywords = ["验证失败", "验证错误", "请重新验证"]

        for kw in success_keywords:
            if kw in page_text:
                check_success = True
                print(f"    [OK] 签到成功 (检测到关键词: {kw})")
                break

        if not check_success:
            for kw in fail_keywords:
                if kw in page_text:
                    print(f"    [-] 签到失败: {kw}")
                    break
            else:
                print("    [WARN] 未检测到明确签到结果")
                # 保存截图和源码用于调试
                try:
                    driver.save_screenshot("audiences_result.png")
                except:
                    pass
                with open("audiences_page.html", "w", encoding="utf-8") as f:
                    f.write(page_text)

    except Exception as e:
        print(f"    [-] 签到过程出错: {e}")
        if driver:
            try:
                driver.save_screenshot("audiences_error.png")
            except:
                pass
    finally:
        if driver:
            driver.quit()
            print("    [*] 浏览器已关闭")

    print("=" * 50)
    print(f"  结果: {'✅ 成功' if check_success else '❌ 失败'}")
    print("=" * 50)
    return check_success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
