#!/usr/bin/env python3
"""签到判定逻辑的回归测试（防 piggo "没签到却报 OK" 复发）。

运行: python tests/test_checkin_verdict.py
只需 import checkin.py；顶层的 cloakbrowser 用空桩替掉，不需要真浏览器。
"""
import importlib.util
import sys
import types
from pathlib import Path

# Windows 控制台默认 gbk，checkin.py 里那些中文/emoji print 会炸掉裸跑。
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


# ---- 让测试不等真实时间：sleep 只推进一个假时钟 ----
_clock = {"t": 0.0}


def _sleep(seconds):
    _clock["t"] += seconds


checkin.time = type("_FakeTime", (), {"sleep": staticmethod(_sleep),
                                      "time": staticmethod(lambda: _clock["t"])})()

# 真·签到成功页正文（2026-10-29 卡神提供 piggo 页面，只留判定用到的片段，账号信息不带）
REAL_SIGNED_PAGE = ("猪猪 :: 签到 - Powered by NexusPHP\n"
                    "[签到已得15, 补签卡: 0]\n"
                    "签到成功 这是您的第 242 次签到，已连续签到 2 天，本次签到获得 15 个魔力值。"
                    "点击白色背景的圆点进行补签。你目前拥有补签卡 0 张。今日签到排名： 478 / 820")
REAL_SIGNED_TITLE = "猪猪 :: 签到 - Powered by NexusPHP"
# CI 日志里 piggo 那次唯一读到的东西：395 字符，跟真签到页差一个数量级
Piggo_PAGE = ("Attention Required! | Cloudflare\n"
              "Sorry, you have been blocked\n"
              "You are unable to access piggo.me\n"
              "Ray ID: 8f0b... · Performance & Security by Cloudflare")
NOT_SIGNED_PAGE = ("签到\n"
                   "单击下方按钮完成今日签到\n"
                   "签到规则：每日签到可获得魔力值")
LOGIN_PAGE = ("用户登录\n"
              "请输入用户名和密码\n"
              "记住密码")
LEICHI_PAGE = ("系统正在对当前访问进行安全检测\n"
               "安全检测能力由雷池WAF驱动")


# ===================== 1. classify_page：只认正面证据 =====================
print("\n[1] classify_page 判定")
ok(checkin.classify_page(REAL_SIGNED_PAGE, "/attendance.php", REAL_SIGNED_TITLE)[0] == "success",
   "真·piggo 签到成功页 → success")
ok(checkin.classify_page(NOT_SIGNED_PAGE, "/attendance.php")[0] == "unknown",
   "只有签到按钮、没有任何结果证据 → unknown（不是 success）")
ok(checkin.classify_page(Piggo_PAGE, "/attendance.php")[0] == "blocked",
   "Cloudflare 拦截页 → blocked（就是 piggo 那天 395 字符的页）")
ok(checkin.classify_page(LOGIN_PAGE, "/login.php")[0] == "login",
   "被送回 login.php → login")
ok(checkin.classify_page(LEICHI_PAGE, "/attendance.php")[0] == "waf",
   "仍停在雷池WAF页 → waf")
ok(checkin.classify_page("验证码错误，请重新验证", "/attendance.php")[0] == "fail",
   "站点报错文字 → fail")
ok(checkin.classify_page("", "")[0] == "unknown",
   "空正文 → unknown（读不到东西绝不算成功）")
ok(checkin.classify_page("", "/attendance.php", "Just a moment...")[0] == "blocked",
   "只有标题是 Just a moment… 也判成 blocked")
_v, _r = checkin.classify_page(Piggo_PAGE, "/attendance.php")
ok("Attention Required" not in _r and "piggo.me" not in _r,
   "判据里不带页面原文")
ok(checkin.classify_page("", "/attendance.php", "", 403)[0] == "blocked",
   "HTTP 403 + 空正文 → blocked（不看正文也能定性）")
ok(checkin.classify_page("", "/attendance.php", "", 401)[0] == "login",
   "HTTP 401 → login")
ok(checkin.classify_page(LOGIN_PAGE, "/attendance.php")[0] == "login",
   "正文是登录表单（用户名+密码）→ login")
ok(checkin.classify_page(REAL_SIGNED_PAGE, "/attendance.php", REAL_SIGNED_TITLE, 403)[0] == "success",
   "真签到成功正文优先于状态码（站点回话为准）")

# ===================== 2. do_checkin：auto 不再白送成功 =====================
print("\n[2] do_checkin 自动签到模式")


class _Resp:
    def __init__(self, status):
        self.status = status


class FakePage:
    """按 JS 片段分派返回值的最小 Playwright 页面对象。"""

    def __init__(self, text, *, has_form=False, has_link=False, after_submit=None,
                 path="/attendance.php", status=200, title=""):
        self.text = text
        self.has_form = has_form
        self.has_link = has_link
        self.after_submit = after_submit
        self.url = "https://piggo.me" + path
        self.status = status
        self.title_text = title
        self.goto_urls = []

    def goto(self, url, **kw):
        self.goto_urls.append(url)
        return _Resp(self.status)

    def wait_for_load_state(self, **kw):
        pass

    def title(self):
        return self.title_text

    def evaluate(self, script):
        if "getElementById('attendance')" in script:
            if not self.has_form:
                return False
            if self.after_submit is not None:
                self.text = self.after_submit
            return True
        if "querySelectorAll('a')" in script:
            if not self.has_link:
                return False
            if self.after_submit is not None:
                self.text = self.after_submit
            return True
        return self.text


SITE_AUTO = {"name": "piggo", "url": "https://piggo.me",
             "checkin_path": "/attendance.php", "checkin_type": "auto"}
SITE_TURNSTILE = dict(SITE_AUTO, checkin_type="turnstile")

# 真事故：CF 拦截页 —— 旧代码在这里打印「签到页面已加载，签到成功」并返回 True
ok(checkin.do_checkin(FakePage(Piggo_PAGE), SITE_AUTO) is False,
   "CF 拦截页 + auto → FAIL（旧的假绿）")
# 空页 + auto：没有任何证据
ok(checkin.do_checkin(FakePage(""), SITE_AUTO) is False,
   "读不到正文 + auto → FAIL")
# 有表单但提交后仍无成功提示
ok(checkin.do_checkin(FakePage(NOT_SIGNED_PAGE, has_form=True,
                               after_submit=NOT_SIGNED_PAGE), SITE_AUTO) is False,
   "点了签到但页面没回话 → FAIL")
# 提交后才出现成功提示 → 这才是真成功
ok(checkin.do_checkin(FakePage(NOT_SIGNED_PAGE, has_form=True,
                               after_submit=REAL_SIGNED_PAGE), SITE_AUTO) is True,
   "auto 站点真的提交并确认成功 → OK")
# 已经签过（正面证据），不需要再点
ok(checkin.do_checkin(FakePage(REAL_SIGNED_PAGE), SITE_AUTO) is True,
   "已签到正文 → OK")
# 被送回登录页：Cookie 失效要如实报红
ok(checkin.do_checkin(FakePage(LOGIN_PAGE, path="/login.php"), SITE_AUTO) is False,
   "Cookie 失效被重定向到 login.php → FAIL")
# 链接式站点：点 a 标签后确认
ok(checkin.do_checkin(FakePage(NOT_SIGNED_PAGE, has_link=True,
                               after_submit=REAL_SIGNED_PAGE), SITE_AUTO) is True,
   "链接式签到点击后确认 → OK")
# 空正文 + HTTP 403：Cloudflare/雷池 硬拦截，不看正文也要红
ok(checkin.do_checkin(FakePage("", status=403), SITE_AUTO) is False,
   "空正文 + HTTP 403 → FAIL（旧的假绿最隐蔽的一种）")
# 状态码 200 但正文是登录表单：Cookie 失效
ok(checkin.do_checkin(FakePage(LOGIN_PAGE), SITE_AUTO) is False,
   "HTTP 200 + 登录表单正文 → FAIL")
# turnstile 站点没有「签到成功」证据时，仍走原来的 turnstile 分支（不回归）
_seen = []
_orig_ts = checkin._checkin_turnstile
checkin._checkin_turnstile = lambda page: (_seen.append("turnstile"), False)[1]
checkin.do_checkin(FakePage(NOT_SIGNED_PAGE), SITE_TURNSTILE)
checkin._checkin_turnstile = _orig_ts
ok(_seen == ["turnstile"],
   "turnstile 站点无证据时仍然进入 _checkin_turnstile（未改变原行为）")

# ===================== 3. 失败要让 CI 红 =====================
print("\n[3] 汇总退出码")
_src = (ROOT / "checkin.py").read_text(encoding="utf-8")
ok("if fail_count > 0:" in _src and _src.index("if fail_count > 0:") >
   _src.index("send_bark_notification(title, body, is_critical)"),
   "有站点失败时 main() 以非 0 退出（Actions 不再全绿）")

print(f"\n{_passed}/{_passed + _failed} 通过")
if _failed:
    sys.exit(1)
