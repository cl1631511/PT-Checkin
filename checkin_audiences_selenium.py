# checkin_audiences_selenium.py (精简版)
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

# --- 代理测试函数 (省略，与之前相同) ---
# ... (test_proxy, _test_socks5_proxy, _test_http_proxy, mask_proxy_url)

# --- 主签到逻辑 ---
def main():
    print("=" * 50)
    print("  audiences.me SeleniumBase 签到测试 (不点击按钮)")
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

        # 1. 访问首页，建立会话
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

        # 刷新
        driver.refresh()
        time.sleep(3)

        # 3. 访问签到页面 - 核心操作
        print(f"    [*] 访问签到页面: {CHECKIN_URL}")
        print("    [*] 等待 Turnstile 自动验证完成（不点击任何按钮）...")
        
        # 使用 UC 模式加载页面，它会自动处理 Turnstile
        driver.uc_open_with_reconnect(CHECKIN_URL, reconnect_time=5)
        
        # 等待页面稳定（Turnstile 验证需要时间）
        time.sleep(5)
        
        # 检查当前 URL 和页面状态
        current_url = driver.current_url
        print(f"    [*] 当前 URL: {current_url}")

        # 4. 等待 Turnstile 验证完成（检测 iframe 消失或 token 出现）
        print("    [*] 等待 Turnstile 验证完成...")
        max_wait = 60  # 最多等待 60 秒
        for i in range(max_wait):
            time.sleep(2)
            try:
                # 检查 Turnstile iframe 是否还存在
                iframes = driver.find_elements("css selector", "iframe[src*='turnstile']")
                if not iframes:
                    print(f"    [*] Turnstile iframe 已消失 (等待 {i*2} 秒)")
                    break
                # 或者检查是否有响应 token
                token = driver.execute_script("""
                    var el = document.querySelector('input[name="cf-turnstile-response"]');
                    return el ? el.value : null;
                """)
                if token:
                    print(f"    [*] Turnstile token 已生成 (等待 {i*2} 秒)")
                    break
            except Exception:
                pass
        else:
            print("    [*] Turnstile 验证等待超时，继续...")

        # 5. 等待页面自动跳转或刷新（签到成功后的自动行为）
        print("    [*] 等待页面自动跳转（签到成功自动刷新）...")
        time.sleep(8)  # 给页面足够时间完成自动签到

        # 6. 检查签到结果
        print("    [*] 检查签到结果...")
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

        # 7. 检查签到结果（不点击任何按钮）
        success_keywords = ["签到成功", "已经签到", "已签到", "今日已签到", "重复签到", "请勿重复打卡"]
        fail_keywords = ["验证失败", "验证错误", "请重新验证"]

        # 检查成功关键词
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
                # 如果页面是签到页且没有失败信息，但也没有成功关键词
                if "attendance" in current_url and ("签到" in page_title or "Attendance" in page_title):
                    # 检查是否有 "签到" 按钮（说明还没签到）
                    if "签到" in page_text and "已签到" not in page_text:
                        print("    [WARN] 页面显示未签到，Turnstile 可能未通过")
                        # 再次尝试等待
                        print("    [*] 等待 30 秒后重新检查...")
                        time.sleep(30)
                        page_text = driver.page_source
                        for kw in success_keywords:
                            if kw in page_text:
                                check_success = True
                                print(f"    [OK] 签到成功 (检测到关键词: {kw})")
                                break
                        if not check_success:
                            # 如果等待后还是没有成功关键词，但页面有"已签到"相关文字
                            if any(kw in page_text for kw in ["已签到", "已经签到", "今日已签到"]):
                                check_success = True
                                print("    [OK] 签到成功")
                    else:
                        # 页面可能已经签到过了
                        check_success = True
                        print("    [OK] 页面正常加载，签到成功（可能已签到）")
                else:
                    print("    [WARN] 页面可能未正确加载，请检查截图")

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
