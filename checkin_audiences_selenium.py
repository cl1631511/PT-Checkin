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

# --- 代理测试函数 (保持不变，省略) ---
# ... (test_proxy, _test_socks5_proxy, _test_http_proxy, mask_proxy_url)

# --- 主签到逻辑 ---
def main():
    print("=" * 50)
    print("  audiences.me SeleniumBase 签到测试 (改进版)")
    print("=" * 50)

    # ... (代理检测和 Cookie 检测部分保持不变) ...

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

        # ... (代理配置部分保持不变) ...

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
            
            # 4a. 尝试多次重连
            max_retries = 3
            for retry in range(max_retries):
                print(f"    [*] 第 {retry+1}/{max_retries} 次尝试...")
                try:
                    # 使用 reconnect 方法，它会尝试重新连接并处理验证
                    driver.uc_open_with_reconnect(CHECKIN_URL, reconnect_time=5)
                    time.sleep(5)
                    
                    # 检查是否成功
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
            
            # 4b. 如果仍然失败，尝试直接执行 JavaScript 绕过
            try:
                print("    [*] 尝试执行 JavaScript 绕过...")
                driver.execute_script("""
                    // 尝试提交 Turnstile 响应
                    var response = document.querySelector('input[name="cf-turnstile-response"]');
                    if (response) {
                        console.log('Found turnstile response input');
                    }
                    // 尝试点击任何验证相关的按钮
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

        # 5. 检查签到结果
        print("    [*] 检查签到结果...")
        time.sleep(3)
        page_text = driver.page_source
        print(f"    [DEBUG] 页面内容长度: {len(page_text)}")
        print(f"    [DEBUG] 当前 URL: {driver.current_url}")

        # 保存页面源码和截图
        with open("audiences_page.html", "w", encoding="utf-8") as f:
            f.write(page_text)
        try:
            driver.save_screenshot("audiences_page.png")
        except:
            pass

        # 检查关键词
        success_keywords = ["签到成功", "已经签到", "已签到", "今日已签到", "重复签到", "请勿重复打卡"]
        fail_keywords = ["登录", "login", "验证失败", "验证错误"]

        check_success = False
        for kw in success_keywords:
            if kw in page_text:
                check_success = True
                print(f"    [OK] 签到成功 (检测到关键词: {kw})")
                break

        if not check_success:
            # 检查是否在登录页
            if "登录" in page_text or "login" in page_text.lower():
                print("    [-] 签到失败: 页面重定向到登录页，Cookie 可能无效")
                # 打印部分页面内容用于调试
                print(f"    [DEBUG] 页面标题: {driver.title}")
                print(f"    [DEBUG] 页面内容前200字符: {page_text[:200]}")
            else:
                for kw in fail_keywords:
                    if kw in page_text:
                        print(f"    [-] 签到失败: {kw}")
                        break
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
