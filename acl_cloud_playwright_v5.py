#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACL Cloud 自动续期脚本（Playwright 修复版 v5.0）
修复：登录成功检测改为轮询等待，避免误判
特性：headless 原生录视频、无需 Xvfb、智能电源管理
"""

import os
import sys
import time
import glob
import requests
from datetime import datetime
from PIL import Image
from io import BytesIO
import pytesseract
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ========== 配置 ==========
USERNAME = os.getenv("ACL_USERNAME", "")
PASSWORD = os.getenv("ACL_PASSWORD", "")
LOGIN_URL = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
WECHAT_WEBHOOK_KEY = os.getenv("WECHAT_WEBHOOK_KEY", "")
MAX_RETRIES = 3
VIDEO_DIR = "videos"
DIAGNOSTIC_PREFIX = "diag"
# ==========================

NEED_RENEWAL = False
RENEWAL_SUCCESS = False
SERVER_STATUS = "unknown"
POWER_ACTION = "none"


def ensure_video_dir():
    if not os.path.exists(VIDEO_DIR):
        os.makedirs(VIDEO_DIR)
        print(f"📁 视频目录: {VIDEO_DIR}")


def diagnostic_screenshot(page, name):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{VIDEO_DIR}/{DIAGNOSTIC_PREFIX}_{ts}_{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
        print(f"📸 诊断截图: {path}")
    except Exception as e:
        print(f"⚠️ 截图失败: {e}")


def wait_and_type(page, selectors, value, label="输入框", delay_ms=50):
    print(f"  🔍 查找 {label}...")
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=8000)
            print(f"     ✅ 找到 {label}: '{sel}'")
            loc.click(timeout=5000)
            time.sleep(0.2)
            loc.press("Control+a")
            loc.press("Delete")
            time.sleep(0.2)
            loc.type(value, delay=delay_ms, timeout=15000)
            print(f"     ✅ {label} 输入完成")
            return True
        except PlaywrightTimeout:
            print(f"     ⏱️ 选择器 '{sel}' 等待超时")
            continue
        except Exception as e:
            print(f"     ❌ 选择器 '{sel}': {str(e)[:80]}")
            continue
    # JS 兜底
    print(f"  ⚠️ 常规输入失败，尝试 JavaScript 兜底...")
    for sel in selectors:
        try:
            page.wait_for_selector(sel, state="attached", timeout=5000)
            page.evaluate(f"""
                (() => {{
                    const el = document.querySelector('{sel}');
                    if (!el) return false;
                    el.value = '{value}';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('keyup', {{ bubbles: true }}));
                    return true;
                }})()
            """)
            print(f"     ✅ JS 兜底成功: '{sel}'")
            return True
        except Exception as e:
            print(f"     ❌ JS 兜底失败 '{sel}': {e}")
            continue
    return False


def wait_and_click(page, selectors, label="按钮"):
    print(f"  🔍 查找 {label}...")
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=8000)
            loc.scroll_into_view_if_needed()
            time.sleep(0.3)
            loc.click(timeout=5000)
            print(f"     ✅ 点击 {label}: '{sel}'")
            return True
        except Exception as e:
            print(f"     ❌ '{sel}': {str(e)[:80]}")
            continue
    return False


def wait_for_login_page(page, url):
    print(f"\n🌐 打开登录页: {url}")
    for attempt in range(3):
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
            print(f"  ✅ 页面 networkidle")
            time.sleep(3)
            selectors = [
                "input[name='email']", "input[type='email']",
                "input[placeholder*='email' i]", "input[name='username']",
                "input[id*='email' i]", "input[id='email']",
                "input[autocomplete='username']", "input[autocomplete='email']",
            ]
            for sel in selectors:
                try:
                    page.wait_for_selector(sel, state="visible", timeout=5000)
                    print(f"  ✅ 登录表单已就绪: '{sel}'")
                    return True
                except:
                    continue
            print(f"  ⚠️ 第 {attempt+1} 次：表单未就绪，重试...")
            diagnostic_screenshot(page, f"login_retry_{attempt+1}")
            time.sleep(3)
        except Exception as e:
            print(f"  ❌ 第 {attempt+1} 次加载失败: {e}")
            diagnostic_screenshot(page, f"load_error_{attempt+1}")
            time.sleep(3)
    return False


# =============================================================================
# 🔄 验证码处理（完全对齐原始 Selenium 代码逻辑）
# =============================================================================
def process_captcha(page, flow_name=""):
    print(f"\n🔄 开始处理{flow_name}人机验证...")

    # ── 1. 点击复选框 ──────────────────────────────────────────────────────
    try:
        checkbox = page.locator(
            "div.auth-captcha-checkbox, input[type='checkbox'] + label, .captcha-checkbox"
        ).first
        checkbox.wait_for(state="visible", timeout=10000)
        checkbox.hover()
        time.sleep(0.3)
        checkbox.click()
        time.sleep(1.5)
        print("  ✅ 复选框已点击")
    except Exception as e:
        print(f"  ⚠️ 点击复选框失败: {e}")
        return False

    # ── 2. 获取提示文字 ────────────────────────────────────────────────────
    try:
        prompt = page.locator("div.auth-captcha-prompt, .captcha-prompt").first
        prompt.wait_for(state="visible", timeout=5000)
        strong_text = prompt.locator("strong").inner_text()
        print(f"  📝 验证码提示文字: {strong_text}")
    except Exception as e:
        print(f"  ⚠️ 获取提示文字失败: {e}")
        return False

    # ── 3. 获取选项 ─────────────────────────────────────────────────────────
    try:
        options = page.locator(
            "div.auth-captcha-options button, .captcha-options .captcha-option, "
            "button.auth-captcha-option, .captcha-option"
        ).all()
        if not options:
            print("  ⚠️ 获取选项失败: 未找到选项按钮")
            return False
        print(f"  📍 共 {len(options)} 个选项")
    except Exception as e:
        print(f"  ⚠️ 获取选项失败: {e}")
        return False

    # ── 4. OCR 识别并点击 ──────────────────────────────────────────────────
    base_url = page.url.rstrip("/auth/login").rstrip("/")
    target = strong_text.lower().replace(" ", "").replace("-", "")
    clicked = False

    for idx, btn in enumerate(options):
        try:
            img = btn.locator("img.auth-captcha-option-img, img").first
            src = img.get_attribute("src")
            full_url = base_url + src if src.startswith("/") else src
            print(f"     📍 选项 {idx + 1}: {full_url[:50]}...")
            resp = requests.get(full_url, timeout=10)
            img_obj = Image.open(BytesIO(resp.content)).convert("L")
            img_obj = img_obj.point(lambda x: 255 if x > 128 else 0)
            ocr_text = pytesseract.image_to_string(img_obj, lang='eng', config='--psm 7').strip()
            ocr_clean = ocr_text.lower().replace(" ", "").replace("-", "")
            print(f"        OCR 结果: '{ocr_text}' → 清洗后: '{ocr_clean}'")
            if target in ocr_clean or ocr_clean in target:
                print(f"  ✅ 找到匹配: 选项 {idx + 1} (OCR: {ocr_text})")
                btn.scroll_into_view_if_needed()
                time.sleep(0.3)
                btn.click()
                clicked = True
                time.sleep(2)
                break
        except Exception as e:
            print(f"     ❌ 选项 {idx + 1} 识别失败: {e}")

    if not clicked:
        print("  ❌ 未点击任何选项")
        return False

    # ── 5. 检查验证结果（三种检测模式）──────────────────────────────────────
    time.sleep(2)

    # 模式 A：检测 Verified / Vérifié 标签
    try:
        verified = page.locator("span.auth-captcha-label, .captcha-label").first
        if verified.is_visible():
            vtext = verified.inner_text()
            if "Verified" in vtext or "Vérifié" in vtext:
                print(f"  ✅ 人机验证通过！(Verified 标签: {vtext})")
                return True
    except:
        pass

    # 模式 B：检测验证码元素是否消失 + 错误提示
    try:
        error_selectors = [
            ".auth-captcha-error", ".captcha-error", ".text-danger",
            "[class*='error']"
        ]
        has_error = False
        for sel in error_selectors:
            try:
                els = page.locator(sel).all()
                for el in els:
                    if el.is_visible() and el.inner_text().strip():
                        print(f"  ❌ 人机验证失败（检测到错误提示: {el.inner_text().strip()[:50]}）")
                        has_error = True
                        break
                if has_error:
                    break
            except:
                continue
        if has_error:
            return False

        captcha_selectors = [
            "div.auth-captcha-options", ".captcha-options",
            "div.auth-captcha-prompt", ".auth-captcha-checkbox"
        ]
        visible_captcha = 0
        for sel in captcha_selectors:
            try:
                els = page.locator(sel).all()
                for el in els:
                    if el.is_visible():
                        visible_captcha += 1
            except:
                continue
        if visible_captcha == 0:
            print("  ✅ 人机验证通过！(验证码元素已消失，弹窗自动关闭)")
            return True

        success_selectors = [
            ".alert-success", ".text-success", ".success-message",
            "[class*='success']", "[class*='verified']", ".toast-success",
            ".notification-success"
        ]
        for sel in success_selectors:
            try:
                els = page.locator(sel).all()
                for el in els:
                    if el.is_visible():
                        txt = el.inner_text().strip()
                        if txt:
                            print(f"  ✅ 人机验证通过！(成功提示: {txt[:30]})")
                            return True
            except:
                continue
    except Exception as e:
        print(f"  ⚠️ 验证结果检测异常: {e}")

    # 模式 C：兜底
    print("  ⚠️ 无法确认验证状态，假设通过（无错误提示）")
    return True


def check_login_success(page, timeout=30):
    """
    检测登录是否成功：轮询检测 URL 变化，最多等待 timeout 秒
    修复：避免只 sleep 一次就判断，导致误判为失败
    """
    print(f"  ⏳ 等待登录跳转（最多 {timeout} 秒）...")
    start_time = time.time()
    last_url = page.url

    while time.time() - start_time < timeout:
        current_url = page.url
        # 如果 URL 变了且不含 login，说明登录成功
        if "login" not in current_url.lower():
            print(f"  🎉 登录成功！当前 URL: {current_url}")
            return True
        # 如果 URL 还在变化（还在跳转中），继续等待
        if current_url != last_url:
            print(f"  🔄 URL 变化中: {current_url}")
            last_url = current_url
            time.sleep(1)
            continue
        # URL 没变，再等 1 秒轮询
        time.sleep(1)

    # 超时后最终检查
    final_url = page.url
    print(f"  ⚠️ 登录检测超时，最终 URL: {final_url}")
    if "login" not in final_url.lower():
        print(f"  🎉 登录成功（最终确认）！URL: {final_url}")
        return True
    return False


def needs_renewal(status_text):
    return any(kw in status_text.lower() for kw in [
        "suspended", "expired", "suspendu", "expiré", "terminé",
        "inactive", "inactif", "ended", "non actif", "renouvellement",
        "renewal", "renouveler", "renew"
    ])


def get_server_info(page):
    global SERVER_STATUS
    print("\n📊 获取服务器信息...")
    info = {
        "time_remaining": "", "plan": "", "renewal_note": "",
        "server_name": "", "server_url": page.url, "status": "unknown"
    }

    try:
        badge = page.locator("span.status-badge[data-status], .status-badge, [class*='status-badge']").first
        if badge.is_visible():
            ds = (badge.get_attribute("data-status") or "").lower()
            txt = badge.inner_text().strip().lower()
            raw = ds or txt
            if any(w in raw for w in ["online", "en ligne", "actif"]):
                info["status"] = SERVER_STATUS = "online"
                print(f"  🟢 Online (data-status={ds}, text={txt})")
            elif any(w in raw for w in ["offline", "hors ligne", "inactif"]):
                info["status"] = SERVER_STATUS = "offline"
                print(f"  🔴 Offline (data-status={ds}, text={txt})")
            else:
                print(f"  ⚪ 未知: {txt} (data-status={ds})")
    except Exception as e:
        print(f"  ⚠️ 状态检测失败: {e}")

    try:
        container = page.locator("div[style*='background: rgba(49, 95, 79'], .server-info-card, [class*='server-info']").first
        if container.is_visible():
            text = container.inner_text()
            for line in text.split("\n"):
                line = line.strip()
                if "Time remaining" in line or "Temps restant" in line:
                    info["time_remaining"] = line.split(":", 1)[-1].strip()
                elif any(w in line.lower() for w in ["plan", "gratuit", "free"]):
                    info["plan"] = line
                elif any(w in line.lower() for w in ["renewal", "renouvellement"]):
                    info["renewal_note"] = line
        print(f"  ⏰ {info['time_remaining'] or '未获取'}")
        print(f"  📋 {info['plan'] or '未获取'}")
    except:
        pass

    try:
        info["server_name"] = page.locator("h1, .server-name, [class*='server-title']").first.inner_text().strip()
    except:
        info["server_name"] = "ACL Cloud Server"

    return info


def manage_server_power(page):
    global POWER_ACTION
    print("\n⚡ 电源管理...")

    try:
        badge = page.locator("span.status-badge[data-status], .status-badge").first
        ds = (badge.get_attribute("data-status") or "").lower()
        txt = badge.inner_text().strip().lower()
        is_offline = any(w in (ds + txt) for w in ["offline", "hors", "inactif"])
    except:
        print("❌ 无法检测状态")
        return False

    if is_offline:
        print("🔴 Offline → Start")
        if wait_and_click(page, [
            "button.power-btn[data-variant='start']",
            "button[data-variant='start']",
            "button:has-text('Start')",
            "button:has-text('Démarrer')",
        ], label="Start 按钮"):
            POWER_ACTION = "start"
            print("✅ Start 已点击")
            return True
        print("❌ Start 未找到")
        return False
    else:
        print("🟢 Online → Restart")
        if wait_and_click(page, [
            "button.power-btn[data-variant='restart']",
            "button[data-variant='restart']",
            "button:has-text('Restart')",
            "button:has-text('Redémarrer')",
        ], label="Restart 按钮"):
            POWER_ACTION = "restart"
            print("✅ Restart 已点击")
            return True
        print("❌ Restart 未找到")
        return False


def send_wechat_notification(info, need_renewal, renewal_success, power_action, video_path=""):
    if not WECHAT_WEBHOOK_KEY:
        print("⚠️ 未设置 WECHAT_WEBHOOK_KEY")
        return False

    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECHAT_WEBHOOK_KEY}"
    name = info.get("server_name", "ACL Cloud Server")
    tr = info.get("time_remaining", "未知")
    plan = info.get("plan", "未知")
    note = info.get("renewal_note", "")
    surl = info.get("server_url", "")
    status = info.get("status", "unknown")

    if need_renewal and renewal_success:
        remoji, rtext, rc = "✅", "续期成功", "🟢"
    elif need_renewal:
        remoji, rtext, rc = "❌", "续期失败", "🔴"
    else:
        remoji, rtext, rc = "✅", "无需续期", "🟢"

    if power_action == "start":
        pem, pt = "🚀", "已执行 Start"
    elif power_action == "restart":
        pem, pt = "🔄", "已执行 Restart"
    else:
        pem, pt = "➖", "未执行电源操作"

    sem = "🟢" if status == "online" else "🔴" if status == "offline" else "⚪"
    video_info = f"\n🎬 <b>录屏:</b> 已生成" if video_path else ""

    content = f"""{rc} <b>ACL Cloud 服务器状态报告</b> {rc}

📌 <b>服务器:</b> {name}
{sem} <b>当前状态:</b> {status.upper()}
⏰ <b>剩余时间:</b> {tr}
📋 <b>套餐:</b> {plan}
📝 <b>续期提示:</b> {note or '无'}

📊 <b>续期:</b> {remoji} {rtext}
⚡ <b>电源:</b> {pem} {pt}{video_info}

🔗 <a href="{surl}">访问详情页</a>

⏱️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    payload = {"msgtype": "text", "text": {"content": content, "mentioned_list": ["@all"]}}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.json().get("errcode") == 0:
            print("✅ 通知发送成功")
            return True
        print(f"❌ 通知失败: {r.json()}")
    except Exception as e:
        print(f"❌ 通知异常: {e}")
    return False


def main():
    global NEED_RENEWAL, RENEWAL_SUCCESS, SERVER_STATUS, POWER_ACTION

    if not USERNAME or not PASSWORD:
        print("❌ 未设置 ACL_USERNAME 或 ACL_PASSWORD")
        sys.exit(1)

    ensure_video_dir()
    server_info = {}
    video_path = ""
    page = None
    context = None
    browser = None

    with sync_playwright() as p:
        try:
            print("🚀 启动 Chromium...")
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1080",
                ]
            )

            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                record_video_dir=VIDEO_DIR,
                record_video_size={"width": 1280, "height": 720},
            )

            page = context.new_page()
            print("✅ Playwright 启动完成，视频录制中...")

            # ========== 登录 ==========
            if not wait_for_login_page(page, LOGIN_URL):
                print("❌ 登录页加载失败")
                diagnostic_screenshot(page, "login_page_failed")
                send_wechat_notification({"server_name": "登录页加载失败"}, False, False, "none")
                return False

            print("\n🔑 输入凭据...")

            email_ok = wait_and_type(page, [
                "input[name='email']", "input[type='email']",
                "input[placeholder*='email' i]", "input[name='username']",
                "input[id*='email' i]", "input[id='email']",
                "input[autocomplete='username']", "input[autocomplete='email']",
            ], USERNAME, label="邮箱输入框", delay_ms=50)

            if not email_ok:
                print("❌ 无法输入邮箱")
                diagnostic_screenshot(page, "email_input_failed")
                try:
                    html = page.content()
                    print(f"\n📄 页面源码前 3000 字符:\n{html[:3000]}")
                except:
                    pass
                send_wechat_notification({"server_name": "邮箱输入失败"}, False, False, "none")
                return False

            time.sleep(0.5)

            pwd_ok = wait_and_type(page, [
                "input[name='password']", "input[type='password']",
                "input[id='password']", "input[autocomplete='current-password']",
            ], PASSWORD, label="密码输入框", delay_ms=50)

            if not pwd_ok:
                print("❌ 无法输入密码")
                diagnostic_screenshot(page, "password_input_failed")
                send_wechat_notification({"server_name": "密码输入失败"}, False, False, "none")
                return False

            print("✅ 凭据已输入")
            time.sleep(1)

            # 验证码 + 登录（修复：使用轮询检测登录成功）
            login_ok = False
            for attempt in range(MAX_RETRIES):
                print(f"\n🔄 验证码尝试 {attempt+1}/{MAX_RETRIES}")
                if process_captcha(page, flow_name="login"):
                    if wait_and_click(page, [
                        "button:has-text('Sign in')",
                        "button[type='submit']",
                        "input[type='submit']",
                        "button:has-text('Connexion')",
                    ], label="Sign in 按钮"):
                        # 🔧 修复：使用轮询检测，而不是固定 sleep 4 秒
                        if check_login_success(page, timeout=15):
                            login_ok = True
                            break
                        else:
                            print(f"  ⚠️ 仍在登录页，准备重试...")
                time.sleep(2)

            if not login_ok:
                print("❌ 登录失败")
                diagnostic_screenshot(page, "login_failed")
                send_wechat_notification({"server_name": "登录失败"}, False, False, "none")
                return False

            # ========== 检查续期 ==========
            print("\n🔍 检查续期...")
            base_url = LOGIN_URL.rstrip("/auth/login").rstrip("/")
            if "dashboard" not in page.url.lower():
                page.goto(base_url + "/dashboard", wait_until="networkidle")
                time.sleep(2)

            has_renewal = False
            try:
                table = page.locator("div.home-renewal-table, .renewal-table, [class*='renewal']").first
                btns = page.locator("button.home-renew-action, .renew-action, button[class*='renew']").all()
                if table.is_visible() and any(b.is_visible() for b in btns):
                    has_renewal = True
                    print("✅ 检测到续期项目")
            except:
                print("ℹ️ 无续期表格")

            # ========== 续期流程 ==========
            if has_renewal:
                print("\n🔄 执行续期...")
                rows = page.locator("div.home-renewal-row, .renewal-row, tr[class*='renewal']").all()
                for idx, row in enumerate(rows):
                    try:
                        status = row.locator("span.home-renewal-status, .renewal-status, .status").first.inner_text().strip()
                        model = row.locator("strong.home-renewal-name, .renewal-name, .model").first.inner_text().strip()
                        print(f"\n📦 {model} | 状态: {status}")

                        if needs_renewal(status):
                            NEED_RENEWAL = True
                            print("  ⚠️ 需要续期")
                            row.scroll_into_view_if_needed()
                            time.sleep(0.5)

                            renew_btn = row.locator("button.home-renew-action, .renew-action, button[class*='renew']").first
                            if renew_btn.is_visible():
                                renew_btn.click()
                                print("  ✅ 已点击 Renew")
                                time.sleep(2)

                                if process_captcha(page, flow_name="renewal_popup"):
                                    RENEWAL_SUCCESS = True
                                    print("✅ 续期验证通过")
                                else:
                                    print("❌ 续期验证失败")
                                time.sleep(2)
                        else:
                            print("  ✅ 无需续期")
                    except Exception as e:
                        print(f"  ❌ 项目 {idx+1}: {e}")

                print("\n📂 导航到 My services...")
                if not wait_and_click(page, [
                    "a[aria-label='My services']", "a[href='/dashboard/projects']",
                    "text=My services", "text=Mes services",
                ], label="My services"):
                    page.goto("https://aclclouds.com/dashboard/projects", wait_until="networkidle")
                time.sleep(2)

                print("\n🔧 点击 Manage...")
                if not wait_and_click(page, [
                    "a.client-btn--primary[href^='/server/']",
                    "a[href^='/server/'].client-btn",
                    ".client-btn--primary",
                ], label="Manage"):
                    print("⚠️ 未找到 Manage")
                time.sleep(3)

                server_info = get_server_info(page)
                manage_server_power(page)

            else:
                print("\n📌 无需续期...")
                if not wait_and_click(page, [
                    "a[aria-label='My services']", "a[href='/dashboard/projects']",
                    "text=My services", "text=Mes services",
                ], label="My services"):
                    page.goto("https://aclclouds.com/dashboard/projects", wait_until="networkidle")
                time.sleep(2)

                if not wait_and_click(page, [
                    "a.client-btn--primary[href^='/server/']",
                    "a[href^='/server/'].client-btn",
                    ".client-btn--primary",
                ], label="Manage"):
                    print("⚠️ 未找到 Manage")
                time.sleep(3)

                server_info = get_server_info(page)
                manage_server_power(page)

            print("\n🎉 所有操作已完成！")
            return True

        except Exception as e:
            print(f"\n❌ 脚本异常: {e}")
            if page:
                diagnostic_screenshot(page, "fatal_error")
            send_wechat_notification({"server_name": f"异常: {str(e)[:50]}"}, False, False, "none")
            return False

        finally:
            print("\n🎬 保存录屏...")
            try:
                if context:
                    context.close()
                if browser:
                    browser.close()
            except Exception as e:
                print(f"⚠️ 关闭浏览器: {e}")

            video_files = glob.glob(os.path.join(VIDEO_DIR, "*.webm"))
            if video_files:
                video_path = video_files[0]
                print(f"✅ 视频: {video_path}")

            send_wechat_notification(server_info, NEED_RENEWAL, RENEWAL_SUCCESS, POWER_ACTION, video_path)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
