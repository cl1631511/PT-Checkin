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

# --- 代理测试函数 (与之前相同，省略) ---
# ... (保留之前的 test_proxy, _test_socks5_proxy, _test_http_proxy, mask_proxy_url 函数)

# --- 主签到逻辑 ---
def main():
    print("=" * 50)
    print("  audiences.me SeleniumBase 签到测试")
    print("=" * 50)

    # 1. 代理测试 (与之前相同)
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

        # 4. 先访问首页，再访问签到页（更自然的流程）
        print(f"    [*] 访问首页: {SITE_URL}")
        driver.get(SITE_URL)
        time.sleep(3)

        # 5. 注入 Cookie
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

        # 6. 访问签到页面
        print(f"    [*] 访问签到页面: {CHECKIN_URL}")
        driver.get(CHECKIN_URL)
        time.sleep(3)

        # 7. 使用 UC 模式绕过 Turnstile
        print("    [*] 尝试绕过 Turnstile (最多等待 120 秒)...")
        
        try:
            driver.uc_open_with_reconnect(CHECKIN_URL, reconnect_time=5)
            print("    [*] uc_open_with_reconnect 执行完成")
        except Exception as e:
            print(f"    [!] uc_open_with_reconnect 失败: {e}")
            print("    [*] 尝试备用策略...")
            try:
                driver.wait_for_element_not_visible("iframe[src*='turnstile']", timeout=90)
            except:
                pass
            try:
                driver.wait_for_element_present("input[name='cf-turnstile-response'][value!='']", timeout=30)
            except:
                pass

        # 8. 等待页面稳定
        time.sleep(5)

        # 9. 尝试点击签到链接或按钮
        print("    [*] 查找并点击签到元素...")
        
        # 尝试多种签到元素选择器
        click_selectors = [
            'a:contains("签到")',
            'a:contains("簽到")',
            'input[type="submit"][value*="签到"]',
            'input[type="submit"][value*="簽到"]',
            'button:contains("签到")',
            'button:contains("簽到")',
            'form[action*="attendance"] input[type="submit"]',
        ]
        
        clicked = False
        for selector in click_selectors:
            try:
                elements = driver.find_elements("css selector", selector)
                for el in elements:
                    if el.is_displayed() and el.is_enabled():
                        el.click()
                        clicked = True
                        print(f"    [*] 点击了签到元素: {selector}")
                        time.sleep(3)
                        break
                if clicked:
                    break
            except Exception as e:
                pass
        
        # 如果找不到签到元素，尝试用 JavaScript 查找
        if not clicked:
            print("    [*] 未找到签到按钮，尝试 JavaScript 查找...")
            js_result = driver.execute_script("""
                // 查找包含"签到"或"簽到"的链接、按钮或提交按钮
                var elements = document.querySelectorAll('a, button, input[type="submit"]');
                for (var i = 0; i < elements.length; i++) {
                    var text = elements[i].textContent || elements[i].value || '';
                    if (text.includes('签到') || text.includes('簽到')) {
                        elements[i].click();
                        return 'clicked: ' + text.trim();
                    }
                }
                // 查找 action 包含 attendance 的表单并提交
                var forms = document.querySelectorAll('form[action*="attendance"]');
                if (forms.length > 0) {
                    forms[0].submit();
                    return 'submitted_form';
                }
                return 'no_action';
            """)
            print(f"    [*] JavaScript 执行结果: {js_result}")
            time.sleep(5)

        # 10. 检查签到结果
        print("    [*] 检查签到结果...")
        page_text = driver.page_source
        print(f"    [DEBUG] 页面内容长度: {len(page_text)}")

        # 保存页面源码到文件（用于调试）
        with open("audiences_page.html", "w", encoding="utf-8") as f:
            f.write(page_text)
        print("    [*] 页面源码已保存到 audiences_page.html")

        # 保存截图
        try:
            driver.save_screenshot("audiences_page.png")
            print("    [*] 截图已保存到 audiences_page.png")
        except:
            pass

        # 检查更多关键词
        success_keywords = [
            "签到成功", "已经签到", "已签到", "今日已签到", 
            "重复签到", "请勿重复打卡", "签到已成功",
            "签到完成", "已打卡", "打卡成功", "成功签到",
            "your check-in was successful", "already checked in"
        ]
        fail_keywords = ["验证失败", "验证错误", "请重新验证", "登录"]

        # 在页面源码中搜索关键词
        page_lower = page_text.lower()
        for kw in success_keywords:
            if kw in page_text:
                check_success = True
                print(f"    [OK] 签到成功 (检测到关键词: {kw})")
                break

        if not check_success:
            # 检查失败关键词
            for kw in fail_keywords:
                if kw in page_text:
                    print(f"    [-] 签到失败: {kw}")
                    break
            else:
                # 如果页面包含用户信息，但没检测到签到关键词，可能已经签到过了
                if "用户" in page_text or "user" in page_lower or "控制面板" in page_text:
                    print("    [WARN] 页面包含用户信息，但未检测到明确签到结果")
                    print("    [WARN] 可能已签到但关键词不同，或需要手动签到")
                else:
                    print("    [WARN] 未检测到明确签到结果")

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
