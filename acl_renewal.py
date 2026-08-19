#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACL Cloud 自动续期脚本（Playwright 版 v1.0）
特性：headless 原生录视频、无需 Xvfb、智能电源管理、企业微信通知
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
# ==========================

NEED_RENEWAL = False
RENEWAL_SUCCESS = False
SERVER_STATUS = "unknown"
POWER_ACTION = "none"


def ensure_video_dir():
    if not os.path.exists(VIDEO_DIR):
        os.makedirs(VIDEO_DIR)
        print(f"📁 视频目录: {VIDEO_DIR}")


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
        pem, pt = "🚀", "已执行 Start（服务器离线，执行启动）"
    elif power_action == "restart":
        pem, pt = "🔄", "已执行 Restart（服务器在线，执行重启）"
    else:
        pem, pt = "➖", "未执行电源操作"

    sem = "🟢" if status == "online" else "🔴" if status == "offline" else "⚪"
    video_info = f"\n🎬 <b>录屏:</b> 已生成，见 Artifact" if video_path else ""

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


def process_captcha(page, flow_name=""):
    """处理人机验证"""
    print(f"🔄 {flow_name} 人机验证...")

    # 1. 点击复选框
    try:
        checkbox = page.locator("div.auth-captcha-checkbox, input[type='checkbox'] + label, .captcha-checkbox").first
        checkbox.wait_for(state="visible", timeout=10000)
        checkbox.click()
        time.sleep(1.5)
        print("✅ 复选框已点击")
    except Exception as e:
        print(f"⚠️ 复选框点击失败: {e}")
        return False

    # 2. 获取提示文字
    try:
        prompt = page.locator("div.auth-captcha-prompt, .captcha-prompt").first
        prompt.wait_for(state="visible", timeout=5000)
        strong_text = prompt.locator("strong").inner_text()
        print(f"📝 提示: {strong_text}")
    except Exception as e:
        print(f"⚠️ 获取提示失败: {e}")
        return False

    # 3. 获取选项
    try:
        options = page.locator("div.auth-captcha-options button, .captcha-options .captcha-option").all()
        if not options:
            print("⚠️ 未找到选项")
            return False
        print(f"📍 找到 {len(options)} 个选项")
    except Exception as e:
        print(f"⚠️ 获取选项失败: {e}")
        return False

    # 4. OCR 匹配
    target = strong_text.lower().replace(" ", "").replace("-", "")
    base_url = page.url.rstrip("/auth/login").rstrip("/")
    clicked = False

    for idx, btn in enumerate(options):
        try:
            img = btn.locator("img").first
            src = img.get_attribute("src")
            full_url = base_url + src if src.startswith("/") else src

            resp = requests.get(full_url, timeout=10)
            img_obj = Image.open(BytesIO(resp.content)).convert("L")
            img_obj = img_obj.point(lambda x: 255 if x > 128 else 0)
            ocr = pytesseract.image_to_string(img_obj, lang='eng', config='--psm 7').strip()
            ocr_clean = ocr.lower().replace(" ", "").replace("-", "")

            if target in ocr_clean or ocr_clean in target:
                print(f"✅ 匹配选项 {idx+1} (OCR: {ocr})")
                btn.scroll_into_view_if_needed()
                time.sleep(0.3)
                btn.click()
                clicked = True
                time.sleep(2)
                break
        except Exception as e:
            print(f"  ❌ 选项 {idx+1} 失败: {e}")

    if not clicked:
        print("❌ 未匹配任何选项")
        return False

    # 5. 验证结果
    time.sleep(2)
    try:
        verified = page.locator("span.auth-captcha-label, .captcha-label").first
        if verified.is_visible() and ("Verified" in verified.inner_text() or "Vérifié" in verified.inner_text()):
            print("✅ 验证通过 (Verified)")
            return True
    except:
        pass

    try:
        if not page.locator("div.auth-captcha-options, .captcha-options, div.auth-captcha-prompt").first.is_visible():
            print("✅ 验证通过（弹窗关闭）")
            return True
    except:
        pass

    print("⚠️ 无法确认验证状态，假设通过")
    return True


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

    # 状态检测
    try:
        badge = page.locator("span.status-badge[data-status], .status-badge, [class*='status-badge']").first
        if badge.is_visible():
            ds = (badge.get_attribute("data-status") or "").lower()
            txt = badge.inner_text().strip().lower()
            raw = ds or txt
            if any(w in raw for w in ["online", "en ligne", "actif"]):
                info["status"] = SERVER_STATUS = "online"
                print(f"  🟢 Online")
            elif any(w in raw for w in ["offline", "hors ligne", "inactif"]):
                info["status"] = SERVER_STATUS = "offline"
                print(f"  🔴 Offline")
            else:
                print(f"  ⚪ 未知: {txt}")
    except Exception as e:
        print(f"  ⚠️ 状态检测失败: {e}")

    # 其他信息
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
        try:
            btn = page.locator("button.power-btn[data-variant='start'], button[data-variant='start']").first
            if btn.is_visible() and not btn.is_disabled():
                btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                btn.click()
                POWER_ACTION = "start"
                print("✅ Start 已点击")
                return True
        except:
            pass
        print("❌ Start 按钮未找到或被禁用")
        return False
    else:
        print("🟢 Online → Restart")
        try:
            btn = page.locator("button.power-btn[data-variant='restart'], button[data-variant='restart']").first
            if btn.is_visible() and not btn.is_disabled():
                btn.scroll_into_view_if_needed()
                time.sleep(0.5)
                btn.click()
                POWER_ACTION = "restart"
                print("✅ Restart 已点击")
                return True
        except:
            pass
        print("❌ Restart 按钮未找到或被禁用")
        return False


def main():
    global NEED_RENEWAL, RENEWAL_SUCCESS, SERVER_STATUS, POWER_ACTION

    if not USERNAME or not PASSWORD:
        print("❌ 未设置 ACL_USERNAME 或 ACL_PASSWORD")
        sys.exit(1)

    ensure_video_dir()
    server_info = {}
    video_path = ""

    with sync_playwright() as p:
        # 启动 Chromium（headless=True，无需 Xvfb）
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

        # 创建上下文并开启视频录制
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=VIDEO_DIR,
            record_video_size={"width": 1280, "height": 720},  # 视频尺寸，可调整
        )

        page = context.new_page()
        print("🚀 Playwright 启动完成，视频录制中...")

        try:
            # ========== 登录 ==========
            print(f"\n🌐 打开登录页: {LOGIN_URL}")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            time.sleep(2)

            print("🔑 输入凭据...")
            page.fill("input[name='email'], input[type='email']", USERNAME)
            page.fill("input[name='password'], input[type='password']", PASSWORD)
            print("✅ 凭据已输入")

            # 验证码 + 登录
            login_ok = False
            for attempt in range(MAX_RETRIES):
                print(f"\n🔄 验证码尝试 {attempt+1}/{MAX_RETRIES}")
                if process_captcha(page, flow_name="login"):
                    page.click("button:has-text('Sign in')")
                    time.sleep(3)
                    if "login" not in page.url.lower():
                        print(f"🎉 登录成功: {page.url}")
                        login_ok = True
                        break
                time.sleep(2)

            if not login_ok:
                print("❌ 登录失败")
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
                        print(f"  ❌ 项目 {idx+1} 错误: {e}")

                # 导航到服务列表
                print("\n📂 导航到 My services...")
                try:
                    page.click("a[aria-label='My services'], a[href='/dashboard/projects']")
                except:
                    page.goto("https://aclclouds.com/dashboard/projects", wait_until="networkidle")
                time.sleep(2)

                # 点击 Manage
                print("\n🔧 点击 Manage...")
                try:
                    page.click("a.client-btn--primary[href^='/server/'], .client-btn--primary")
                except:
                    pass
                time.sleep(3)

                server_info = get_server_info(page)
                manage_server_power(page)

            else:
                print("\n📌 无需续期，直接获取信息...")
                try:
                    page.click("a[aria-label='My services'], a[href='/dashboard/projects']")
                except:
                    page.goto("https://aclclouds.com/dashboard/projects", wait_until="networkidle")
                time.sleep(2)

                try:
                    page.click("a.client-btn--primary[href^='/server/'], .client-btn--primary")
                except:
                    pass
                time.sleep(3)

                server_info = get_server_info(page)
                manage_server_power(page)

            # ========== 通知 ==========
            # 关闭 context 保存视频
            context.close()
            browser.close()

            # 获取生成的视频路径
            video_files = glob.glob(os.path.join(VIDEO_DIR, "*.webm"))
            if video_files:
                video_path = video_files[0]
                # 可选：转换为 mp4
                print(f"🎬 视频已保存: {video_path}")

            send_wechat_notification(server_info, NEED_RENEWAL, RENEWAL_SUCCESS, POWER_ACTION, video_path)
            print("\n🎉 完成！")
            return True

        except Exception as e:
            print(f"\n❌ 错误: {e}")
            try:
                context.close()
                browser.close()
            except:
                pass
            send_wechat_notification({"server_name": f"异常: {str(e)[:50]}"}, False, False, "none")
            return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
