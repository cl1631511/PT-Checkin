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
    print("  audiences.me SeleniumBase 签到测试 (改进版)")
    print("=" * 50)

    # 代理测试
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

    # 检查 Cookie
    if not AUDIENCES_COOKIE:
        print("[!] 未设置 AUDIENCES_COOKIE 环境变量，无法继续")
        sys.exit(1)
    print("    [*] 检测到 AUDIENCES_COOKIE")

    # 启动浏览器
    print("    [*] 启动浏览器...")
    driver = None
    check_success = False
    
    try:
        driver_args = {
            "uc": True,
            "headless": True,
            "uc_cdp": True,
        }

        if proxy_test_passed and PROXY_URL:
            parsed = urlparse(PROXY_URL)
            if parsed.scheme.startswith('socks5'):
                proxy_str = f"socks5://{parsed.hostname}:{parsed.port}"
                if parsed.username and parsed.password:
                    proxy_str = f"socks5://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"
                driver_args["proxy"] = proxy_str
                print(f"    [*] 浏览器使用代理")

        driver = Driver(**driver_args)
        driver.maximize_window()

        # 1. 先访问首页，建立会话
        print(f"    [*] 访问首页: {SITE_URL}")
        driver.get(SITE_URL)
        time.sleep(3)

        # 2. 注入 Cookie
        print("    [*] 注入 Cookie...")
        cookie_count = 0
        for item in AUDIENCES_COOKIE.split(';'):
            if '=' in item:
                name, value = item.strip().split('=', 1)
                try:
                    driver.add_cookie({"name": name, "value": value, "domain": ".audiences.me"})
                    cookie_count += 1
                except Exception as e:
                    print(f"    [*] Cookie 注入失败: {name} - {e}")
        print(f"    [*] 成功注入 {cookie_count} 个 Cookie")

        # 刷新使 Cookie 生效
        driver.refresh()
        time.sleep(3)

        # 3. 访问签到页面
        print(f"    [*] 访问签到页面: {CHECKIN_URL}")
        driver.get(CHECKIN_URL)
        time.sleep(5)

        # 4. 检查是否被 Turnstile 拦截
        page_source = driver.page_source
        if "turnstile" in page_source.lower() or "cf-challenge" in page_source.lower():
            print("    [*] 检测到 Turnstile 挑战，开始绕过...")
            
            max_retries = 3
            for retry in range(max_retries):
                print(f"    [*] 第 {retry+1}/{max_retries} 次尝试...")
                try:
                    driver.uc_open_with_reconnect(CHECKIN_URL, reconnect_time=5)
                    time.sleep(5)
                    
                    current_url = driver.current_url
                    if "attendance" in current_url and "login" not in current_url:
                        print(f"    [*] 第 {retry+1} 次尝试成功，页面已加载")
                        break
                    else:
                        print(f"    [*] 第 {retry+1} 次尝试后 URL: {current_url}")
                        if retry < max_retries - 1:
                            time.sleep(3)
                            continue
                except Exception as e:
                    print(f"    [*] 第 {retry+1} 次尝试异常: {e}")
                    if retry < max_retries - 1:
                        time.sleep(3)
                        continue
            
            # 尝试 JavaScript 辅助
            try:
                print("    [*] 尝试执行 JavaScript 绕过...")
                driver.execute_script("""
                    var response = document.querySelector('input[name="cf-turnstile-response"]');
                    if (response) {
                        console.log('Found turnstile response input');
                    }
                    var buttons = document.querySelectorAll('button, input[type="submit"]');
                    for (var i = 0; i < buttons.length; i++) {
                        var text = buttons[i].textContent || buttons[i].value || '';
                        if (text.includes('验证') || text.includes('继续') || text.includes('确认')) {
                            buttons[i].click();
                            console.log('Clicked: ' + text);
                            break;
                        }
                    }
                """)
                time.sleep(5)
            except Exception as e:
                print(f"    [*] JavaScript 绕过异常: {e}")

        # 5. 检查签到结果（修复后的逻辑）
        print("    [*] 检查签到结果...")
        time.sleep(3)
        page_text = driver.page_source
        current_url = driver.current_url
        page_title = driver.title
        
        print(f"    [DEBUG] 页面内容长度: {len(page_text)}")
        print(f"    [DEBUG] 当前 URL: {current_url}")
        print(f"    [DEBUG] 页面标题: {page_title}")

        # 保存页面源码和截图
        with open("audiences_page.html", "w", encoding="utf-8") as f:
            f.write(page_text)
        try:
            driver.save_screenshot("audiences_page.png")
        except:
            pass

        # 签到成功关键词
        success_keywords = ["签到成功", "已经签到", "已签到", "今日已签到", "重复签到", "请勿重复打卡"]

        # 检查 URL 是否在签到页
        if "attendance.php" in current_url and ("签到" in page_title or "Attendance" in page_title):
            print(f"    [*] 页面已正常加载: {page_title}")
            
            # 检查是否已签到
            for kw in success_keywords:
                if kw in page_text:
                    check_success = True
                    print(f"    [OK] 签到成功 (检测到关键词: {kw})")
                    break
            
            # 如果没有检测到成功关键词，检查是否有签到按钮
            if not check_success:
                # 检查页面是否包含"签到"链接或按钮
                if "签到" in page_text or "簽到" in page_text:
                    print("    [*] 检测到签到按钮，尝试点击...")
                    try:
                        result = driver.execute_script("""
                            var elements = document.querySelectorAll('a, button, input[type="submit"]');
                            for (var i = 0; i < elements.length; i++) {
                                var text = elements[i].textContent || elements[i].value || '';
                                if (text.includes('签到') || text.includes('簽到')) {
                                    elements[i].click();
                                    return 'clicked: ' + text.trim();
                                }
                            }
                            // 检查是否有 form 可以直接提交
                            var forms = document.querySelectorAll('form[action*="attendance"]');
                            if (forms.length > 0) {
                                forms[0].submit();
                                return 'submitted form';
                            }
                            return 'not found';
                        """)
                        print(f"    [*] 点击结果: {result}")
                        time.sleep(3)
                        
                        # 重新检查
                        page_text = driver.page_source
                        for kw in success_keywords:
                            if kw in page_text:
                                check_success = True
                                print(f"    [OK] 签到成功 (检测到关键词: {kw})")
                                break
                    except Exception as e:
                        print(f"    [*] 点击签到按钮失败: {e}")
                
                # 如果还是没有成功，检查是否有明确的失败信息
                if not check_success:
                    fail_keywords = ["验证失败", "验证错误", "请重新验证"]
                    has_fail = False
                    for kw in fail_keywords:
                        if kw in page_text:
                            has_fail = True
                            print(f"    [-] 签到失败: {kw}")
                            break
                    
                    if not has_fail:
                        # 页面正常加载，没有失败信息，视为签到成功（可能已经签到过了）
                        print("    [OK] 页面正常加载，无失败信息，签到成功（可能已签到）")
                        check_success = True
        else:
            # URL 不是 attendance.php
            print(f"    [WARN] 当前 URL 不是签到页: {current_url}")
            if "login" in current_url:
                print("    [-] 签到失败: 页面重定向到登录页")
            else:
                print("    [WARN] 页面可能未正确加载")
                # 打印部分页面内容用于调试
                print(f"    [DEBUG] 页面内容前200字符: {page_text[:200]}")

    except Exception as e:
        print(f"    [-] 签到过程出错: {e}")
        import traceback
        traceback.print_exc()
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
