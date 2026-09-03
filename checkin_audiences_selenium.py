# checkin_audiences_selenium.py
import os
from seleniumbase import Driver

# 1. 从环境变量获取 Cookie (您已经设置好了)
cookie_string = os.environ.get("AUDIENCES_COOKIE")

# 2. 启动 SeleniumBase Driver (使用 UC 模式)
driver = Driver(uc=True, headless=False) # 建议先在本地 headless=False 测试

try:
    # 3. 访问签到页面
    driver.get("https://audiences.me/attendance.php")

    # 4. 注入 Cookie (SeleniumBase 需要先访问页面才能设置 cookie)
    if cookie_string:
        for item in cookie_string.split(';'):
            if '=' in item:
                name, value = item.strip().split('=', 1)
                driver.add_cookie({"name": name, "value": value, "domain": ".audiences.me"})
        driver.refresh() # 刷新使 Cookie 生效

    # 5. 使用 UC 模式绕过 Turnstile
    # 这是 SeleniumBase 的核心方法，它会尝试自动处理验证
    driver.uc_open_with_reconnect("https://audiences.me/attendance.php", reconnect_time=3)

    # 6. 等待页面加载完成，检查是否签到成功
    driver.implicitly_wait(10)
    page_text = driver.page_source

    # 7. 检查签到结果 (与您之前的逻辑一致)
    if "签到成功" in page_text or "已签到" in page_text:
        print("✅ audiences.me 签到成功!")
    else:
        print("❌ 签到可能失败，请检查页面。")

    # 如果需要，可以在这里保存页面截图用于调试
    # driver.save_screenshot("audiences_checkin_result.png")

finally:
    driver.quit()
