# checkin_audiences_stealth.py
import os
import sys
import time
import re
import asyncio
from urllib.parse import urlparse
from playwright.async_api import async_playwright

# playwright-stealth 的正确导入方式
try:
    from playwright_stealth import stealth_async
except ImportError:
    # 如果 playwright-stealth 版本不同，尝试备用导入
    try:
        from playwright_stealth import stealth
        stealth_async = stealth
    except ImportError:
        # 如果都没有，定义一个空函数
        async def stealth_async(page):
            return page

# --- 配置 ---
PROXY_URL = os.environ.get("PROXY_URL")
AUDIENCES_COOKIE = os.environ.get("AUDIENCES_COOKIE")
SITE_URL = "https://audiences.me"
CHECKIN_URL = f"{SITE_URL}/attendance.php"

# --- 代理解析 ---
def parse_proxy(proxy_url: str):
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return None
    proxy = {
        "server": f"{parsed.hostname}:{parsed.port}",
    }
    if parsed.scheme.startswith('socks5'):
        proxy["type"] = "socks5"
    if parsed.username and parsed.password:
        proxy["username"] = parsed.username
        proxy["password"] = parsed.password
    return proxy

def mask_proxy_url(proxy_url: str) -> str:
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
async def main():
    print("=" * 50)
    print("  audiences.me Playwright-Stealth 签到测试")
    print("=" * 50)

    if not AUDIENCES_COOKIE:
        print("[!] 未设置 AUDIENCES_COOKIE 环境变量，无法继续")
        sys.exit(1)
    print("    [*] 检测到 AUDIENCES_COOKIE")

    proxy_config = None
    if PROXY_URL:
        proxy_display = mask_proxy_url(PROXY_URL)
        print(f"    [*] 检测到代理配置: {proxy_display}")
        proxy_config = parse_proxy(PROXY_URL)
        if proxy_config:
            print(f"    [*] 使用代理进行签到")
        else:
            print(f"    [*] 代理解析失败，使用直连")
    else:
        print("    [*] 未配置代理，使用直连")

    async with async_playwright() as p:
        # 启动浏览器
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            proxy=proxy_config if proxy_config else None,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        
        # 应用 stealth 插件（如果可用）
        try:
            await stealth_async(page)
            print("    [*] Stealth 已启用")
        except Exception as e:
            print(f"    [*] Stealth 启用失败: {e}")

        # 1. 访问首页
        print(f"    [*] 访问首页: {SITE_URL}")
        await page.goto(SITE_URL, wait_until="networkidle")
        await asyncio.sleep(2)

        # 2. 注入 Cookie
        print("    [*] 注入 Cookie...")
        cookie_count = 0
        for item in AUDIENCES_COOKIE.split(';'):
            if '=' in item:
                name, value = item.strip().split('=', 1)
                try:
                    await context.add_cookies([{
                        "name": name,
                        "value": value,
                        "domain": ".audiences.me",
                        "path": "/"
                    }])
                    cookie_count += 1
                except Exception as e:
                    print(f"    [*] Cookie 注入失败: {name} - {e}")
        print(f"    [*] 成功注入 {cookie_count} 个 Cookie")

        await page.reload()
        await asyncio.sleep(2)

        # 3. 访问签到页面
        print(f"    [*] 访问签到页面: {CHECKIN_URL}")
        await page.goto(CHECKIN_URL, wait_until="networkidle")
        await asyncio.sleep(3)

        print(f"    [*] 当前 URL: {page.url}")

        # 4. 等待 Turnstile
        print("    [*] 等待 Turnstile 验证完成...")
        
        has_turnstile = await page.locator(".cf-turnstile").count() > 0
        if has_turnstile:
            print("    [*] 检测到 Turnstile 容器")
            
            # 等待 Turnstile iframe 加载
            try:
                await page.wait_for_selector("iframe[src*='turnstile']", timeout=15000)
                print("    [*] Turnstile iframe 已加载")
            except:
                print("    [*] Turnstile iframe 未检测到")
            
            # 等待验证完成
            max_wait = 120
            for i in range(max_wait):
                await asyncio.sleep(2)
                token = await page.evaluate("""
                    () => {
                        const el = document.querySelector('input[name="cf-token"]');
                        return el ? el.value : null;
                    }
                """)
                if token:
                    print(f"    [*] Turnstile token 已生成 (等待 {i*2} 秒)")
                    break
                iframe_count = await page.locator("iframe[src*='turnstile']").count()
                if iframe_count == 0 and i > 2:
                    print(f"    [*] Turnstile iframe 已消失 (等待 {i*2} 秒)")
                    break
                if i % 5 == 0:
                    print(f"    [*] 等待 Turnstile 验证... ({i*2}s)")
            else:
                print("    [*] Turnstile 等待超时")
        else:
            print("    [*] 未检测到 Turnstile 容器")

        # 5. 检查结果
        print("    [*] 检查签到状态...")
        await asyncio.sleep(3)
        
        page_text = await page.content()
        page_title = await page.title()
        current_url = page.url
        
        print(f"    [DEBUG] 当前 URL: {current_url}")
        print(f"    [DEBUG] 页面标题: {page_title}")

        with open("audiences_page.html", "w", encoding="utf-8") as f:
            f.write(page_text)
        await page.screenshot(path="audiences_page.png")
        print("    [*] 页面源码和截图已保存")

        # 检查签到结果
        success_keywords = ["签到成功", "已经签到", "已签到", "今日已签到", "重复签到", "请勿重复打卡"]
        fail_keywords = ["验证失败", "验证错误", "请重新验证"]

        check_success = False
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
                if "签到" in page_text and "已签到" not in page_text:
                    print("    [*] 页面显示未签到，尝试点击签到按钮...")
                    try:
                        clicked = await page.evaluate("""
                            () => {
                                const elements = document.querySelectorAll('a, button, input[type="submit"]');
                                for (const el of elements) {
                                    const text = el.textContent || el.value || '';
                                    if (text.includes('签到') || text.includes('簽到')) {
                                        el.click();
                                        return true;
                                    }
                                }
                                return false;
                            }
                        """)
                        if clicked:
                            print("    [*] 已点击签到按钮")
                            await asyncio.sleep(3)
                            page_text = await page.content()
                            for kw in success_keywords:
                                if kw in page_text:
                                    check_success = True
                                    print(f"    [OK] 签到成功 (检测到关键词: {kw})")
                                    break
                    except Exception as e:
                        print(f"    [*] 点击签到按钮失败: {e}")
                
                if not check_success:
                    if "attendance" in current_url:
                        print("    [WARN] 页面正常加载，但未检测到签到成功关键词")
                        print("    [WARN] 可能 Turnstile 未通过或签到已满")
                    else:
                        print("    [WARN] 页面可能未正确加载")

        await browser.close()
        print("    [*] 浏览器已关闭")

    print("=" * 50)
    print(f"  结果: {'✅ 成功' if check_success else '❌ 失败'}")
    print("=" * 50)
    return check_success

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
