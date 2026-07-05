#!/usr/bin/env python3
"""
Kerit Cloud Billing 自动续期 - CloakBrowser + Email OTP 版
"""

import os
import sys
import time
import json
import imaplib
import email
import re
import urllib.request
import urllib.parse
import traceback
from datetime import datetime
from pathlib import Path

from cloakbrowser import launch


# ============================================================
# 配置
# ============================================================

# Kerit 账号 (环境变量: KERIT_ACCOUNT=email@gmail.com,app_password)
_account = os.environ.get("KERIT_ACCOUNT", "").split(",")
if len(_account) < 2:
    print("❌ KERIT_ACCOUNT 格式错误: email@gmail.com,app_password")
    sys.exit(1)

KERIT_EMAIL = _account[0].strip()
GMAIL_PASSWORD = _account[1].strip()
MASKED_EMAIL = "******@" + KERIT_EMAIL.split("@")[1]

# 代理 (Gost SOCKS5)
PROXY_URL = "socks5://127.0.0.1:40000"

# URL
LOGIN_URL = "https://billing.kerit.cloud/"
FREE_PANEL_URL = "https://billing.kerit.cloud/free_panel"

# Telegram
_tg_raw = os.environ.get("TG_BOT", "")
TG_CHAT_ID, TG_TOKEN = "", ""
if _tg_raw and "," in _tg_raw:
    _tg = _tg_raw.split(",")
    TG_CHAT_ID = _tg[0].strip()
    TG_TOKEN = _tg[1].strip()

# 截图目录
SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)


# ============================================================
# 工具函数
# ============================================================

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def save_screenshot(page, name):
    try:
        path = SCREENSHOT_DIR / f"{name}_{datetime.now().strftime('%H%M%S')}.png"
        page.screenshot(path=str(path))
        log(f"📸 {path}")
        return str(path)
    except Exception as e:
        log(f"⚠️ 截图失败: {e}")
        return None

def send_tg(result, server_id=None, remaining=None):
    lines = [
        f"🎮 Kerit 续期通知",
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if server_id is not None:
        lines.append(f"🖥 服务器ID: {server_id}")
    lines.append(f"📊 {result}")
    if remaining is not None:
        lines.append(f"⏱️ 剩余: {remaining}天")
    
    msg = "\n".join(lines)
    log(msg)
    
    if not TG_TOKEN or not TG_CHAT_ID:
        log("⚠️ TG未配置")
        return
    
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT_ID,
            "text": msg,
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            log("📨 TG推送成功")
    except Exception as e:
        log(f"⚠️ TG推送失败: {e}")


# ============================================================
# Gmail OTP
# ============================================================

def extract_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    body = re.sub(r'<[^>]+>', ' ', html)
                    break
    else:
        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
    return body


def fetch_otp_from_gmail(wait_seconds=90) -> str:
    log(f"📬 连接 Gmail，等待 OTP ({wait_seconds}s)...")
    deadline = time.time() + wait_seconds

    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
    mail.login(KERIT_EMAIL, GMAIL_PASSWORD)
    log("✅ Gmail 登录成功")

    # 查找垃圾邮件文件夹
    spam_folder = None
    _, folder_list = mail.list()
    for f in folder_list:
        decoded = f.decode("utf-8", errors="ignore")
        if any(k in decoded.lower() for k in ["spam", "junk", "垃圾"]):
            match = re.search(r'"([^"]+)"\s*$', decoded)
            if not match:
                match = re.search(r'(\S+)\s*$', decoded)
            if match:
                spam_folder = match.group(1).strip('"')
                log(f"🗑️ 垃圾邮件文件夹: {spam_folder}")
                break

    folders_to_check = ["INBOX"]
    if spam_folder:
        folders_to_check.append(spam_folder)

    seen_uids = {}
    for folder in folders_to_check:
        try:
            status, _ = mail.select(folder)
            if status == "OK":
                _, data = mail.uid("search", None, "ALL")
                seen_uids[folder] = set(data[0].split())
        except Exception:
            seen_uids[folder] = set()

    # 轮询新邮件
    while time.time() < deadline:
        time.sleep(5)

        for folder in folders_to_check:
            try:
                status, _ = mail.select(folder)
                if status != "OK":
                    continue
                
                _, data = mail.uid("search", None, 'FROM "kerit"')
                all_uids = set(data[0].split())
                new_uids = all_uids - seen_uids[folder]

                for uid in new_uids:
                    seen_uids[folder].add(uid)
                    _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    body = extract_email_body(msg)
                    
                    otp_match = re.search(r'\b(\d{4})\b', body)
                    if otp_match:
                        code = otp_match.group(1)
                        log(f"✅ OTP: {code}")
                        mail.logout()
                        return code

            except Exception:
                continue

    mail.logout()
    raise TimeoutError("❌ Gmail OTP 超时")


# ============================================================
# 续期核心
# ============================================================

def extract_remaining_days(page) -> int:
    try:
        return page.evaluate("""() => {
            const el = document.getElementById('expiry-display');
            return el ? parseInt(el.innerText || "0") : 0;
        }""") or 0
    except Exception:
        return 0


def do_renew(page):
    log("🔄 进入续期页面...")
    page.goto(FREE_PANEL_URL, wait_until="networkidle")
    time.sleep(3)
    save_screenshot(page, "free_panel")

    # 获取服务器ID
    server_id = page.evaluate("""() => {
        return typeof serverData !== 'undefined' ? serverData.id : null;
    }""")

    if not server_id:
        log("❌ 无法获取 serverData.id")
        save_screenshot(page, "no_server_id")
        send_tg("❌ serverData.id 缺失")
        return

    log(f"🆔 服务器ID: {server_id}")

    initial_count = page.evaluate("""() => {
        const el = document.getElementById('renewal-count');
        return el ? parseInt(el.innerText || "0") : 0;
    }""")

    initial_remaining = extract_remaining_days(page)
    need = 7 - initial_count

    log(f"📊 进度: {initial_count}/7，剩余: {initial_remaining}天，需续期: {need}次")

    if initial_remaining >= 7:
        log("✅ 剩余天数已满，无需续期")
        save_screenshot(page, "renew_skip")
        send_tg("✅ 无需续期", server_id, initial_remaining)
        return

    if need <= 0:
        log("🎉 已达上限")
        save_screenshot(page, "renew_full")
        send_tg("✅ 已达上限", server_id, extract_remaining_days(page))
        return

    # 循环续期
    for attempt in range(need):
        count = page.evaluate("""() => {
            const el = document.getElementById('renewal-count');
            return el ? parseInt(el.innerText || "0") : 0;
        }""")

        log(f"📊 当前: {count}/7")

        if count >= 7:
            log("🎉 已达上限")
            send_tg("✅ 续期完成", server_id, extract_remaining_days(page))
            return

        log(f"🔁 第 {attempt + 1}/{need} 次续期...")

        # 点击 Renew Server
        renew_clicked = False
        for sel in [
            'button:has-text("Renew Server")',
            'a:has-text("Renew Server")',
            'button:has-text("Renew")',
        ]:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.click()
                renew_clicked = True
                log("✅ 点击 Renew Server")
                break
        
        if not renew_clicked:
            result = page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, a'));
                const btn = btns.find(b => b.textContent.includes('Renew'));
                if (btn) { btn.click(); return true; }
                return false;
            }""")
            if result:
                renew_clicked = True
                log("✅ JS 点击 Renew")

        if not renew_clicked:
            log("❌ 找不到 Renew 按钮")
            save_screenshot(page, f"no_renew_btn_{attempt}")
            send_tg(f"❌ 无Renew按钮 #{attempt+1}", server_id)
            return

        time.sleep(3)

        # CloakBrowser 自动处理 Turnstile
        log("⏳ 等待 Turnstile...")
        time.sleep(5)

        # 获取 Token
        token = page.evaluate("""() => {
            const input = document.querySelector('input[name="cf-turnstile-response"]');
            return input ? input.value : '';
        }""")

        if not token or len(token) < 20:
            log("⚠️ Token 未就绪，等待...")
            time.sleep(5)
            token = page.evaluate("""() => {
                const input = document.querySelector('input[name="cf-turnstile-response"]');
                return input ? input.value : '';
            }""")

        if not token or len(token) < 20:
            log("❌ Token 获取失败")
            save_screenshot(page, f"token_fail_{attempt}")
            send_tg(f"❌ Token失败 #{attempt+1}", server_id)
            continue

        log(f"✅ Token: {token[:20]}...")

        # 提交续期 API
        log("🎯 提交续期...")
        result = page.evaluate(f"""async () => {{
            try {{
                const res = await fetch('/api/renew', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    credentials: 'include',
                    body: JSON.stringify({{ id: '{server_id}', captcha: '{token}' }})
                }});
                return await res.json();
            }} catch(e) {{
                return {{error: e.message}};
            }}
        }}""")

        log(f"📨 API: {json.dumps(result)[:200]}")

        # 关闭弹窗
        page.evaluate("""() => {
            document.querySelector('[data-bs-dismiss="modal"]')?.click();
        }""")

        # 刷新
        time.sleep(2)
        page.reload(wait_until="networkidle")
        time.sleep(3)
        save_screenshot(page, f"after_renew_{attempt}")

    # 最终结果
    save_screenshot(page, "renew_done")
    final_count = page.evaluate("""() => {
        const el = document.getElementById('renewal-count');
        return el ? parseInt(el.innerText || "0") : 0;
    }""")
    final_remaining = extract_remaining_days(page)

    log(f"📊 最终: {final_count}/7，剩余: {final_remaining}天")

    if final_count >= 7:
        send_tg("✅ 续期完成", server_id, final_remaining)
    else:
        send_tg(f"⚠️ 未完成 ({final_count}/7)", server_id, final_remaining)


# ============================================================
# 主流程
# ============================================================

def run():
    log("=" * 60)
    log("🚀 Kerit Cloud 自动续期 (CloakBrowser + Email OTP)")
    log(f"📧 {MASKED_EMAIL}")
    log(f"🌐 {PROXY_URL}")
    log("=" * 60)

    log("🔧 启动 CloakBrowser...")
    
    browser = launch(
        headless=False,      # Xvfb 提供虚拟显示
        humanize=True,       # 人类化行为
        proxy=PROXY_URL,     # Gost SOCKS5
        geoip=True,
    )

    try:
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        
        page = context.new_page()

        # ── IP 验证 ──
        log("🌐 验证出口IP...")
        try:
            page.goto("https://api.ipify.org?format=json", wait_until="networkidle")
            ip_text = page.text_content("body")
            ip_text = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1xx', ip_text)
            log(f"✅ 出口IP: {ip_text}")
        except Exception:
            log("⚠️ IP验证失败")

        # ── 打开登录页 ──
        log("🔑 打开 billing.kerit.cloud...")
        page.goto(LOGIN_URL, wait_until="networkidle")
        time.sleep(3)
        save_screenshot(page, "login_page")

        # 等待 Cloudflare
        log("⏳ 等待页面稳定...")
        time.sleep(3)

        # ── 输入邮箱 ──
        log("📭 查找邮箱输入框...")
        
        for _ in range(20):
            if page.locator("#email-input").count() > 0:
                break
            time.sleep(0.5)
        else:
            log("❌ 邮箱框未找到")
            save_screenshot(page, "no_email")
            send_tg("❌ 邮箱框未找到")
            return

        # CloakBrowser type 模拟人类打字
        page.locator("#email-input").type(KERIT_EMAIL, delay=50)
        log(f"✅ 邮箱: {MASKED_EMAIL}")

        # ── 点击 Continue ──
        log("🖱️ 点击 Continue...")
        
        for sel in ['button:has-text("Continue with Email")', 'button[type="submit"]']:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.click()
                log("✅ Continue 已点击")
                break
        else:
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const btn = btns.find(b => b.textContent.includes('Continue'));
                btn?.click();
            }""")

        # ── 等待 OTP 框 ──
        log("📨 等待 OTP 输入框...")
        for _ in range(30):
            if page.locator(".otp-input").count() >= 4:
                break
            time.sleep(1)
        else:
            log("❌ OTP框未出现")
            save_screenshot(page, "no_otp")
            send_tg("❌ OTP框未出现")
            return

        # ── 获取 OTP ──
        try:
            code = fetch_otp_from_gmail(wait_seconds=90)
        except TimeoutError as e:
            log(str(e))
            save_screenshot(page, "otp_timeout")
            send_tg("❌ OTP超时")
            return

        # ── 填入 OTP ──
        log(f"⌨️ 填入 OTP: {code}")
        
        otp_inputs = page.locator(".otp-input").all()
        if len(otp_inputs) < 4:
            log(f"❌ OTP框不足: {len(otp_inputs)}")
            send_tg(f"❌ OTP框不足")
            return

        for i, char in enumerate(code):
            otp_inputs[i].type(char, delay=100)
            time.sleep(0.1)

        log("✅ OTP 已填入")

        # ── 点击 Verify ──
        log("🚀 点击 Verify...")
        
        for sel in ['button:has-text("Verify")', 'button[type="submit"]']:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.click()
                log("✅ Verify 已点击")
                break
        else:
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const btn = btns.find(b => b.textContent.includes('Verify'));
                btn?.click();
            }""")

        # ── 等待登录成功 ──
        log("⏳ 等待登录跳转...")
        for _ in range(60):
            url = page.url
            if "/session" in url or "/dashboard" in url or "/free" in url:
                log(f"✅ 登录成功: {url}")
                break
            time.sleep(1)
        else:
            log("❌ 登录超时")
            save_screenshot(page, "login_timeout")
            send_tg("❌ 登录超时")
            return

        save_screenshot(page, "logged_in")

        # ── 保存认证状态 ──
        log("💾 保存认证状态...")
        try:
            storage = context.storage_state()
            with open("kerit_auth.json", "w") as f:
                json.dump(storage, f, indent=2)
            log("✅ 认证状态已保存")
        except Exception as e:
            log(f"⚠️ 保存失败: {e}")

        # ── 执行续期 ──
        do_renew(page)

    except Exception as e:
        log(f"💥 错误: {e}")
        log(traceback.format_exc())
        save_screenshot(page, "fatal_error")
        send_tg(f"💥 错误: {str(e)[:100]}")
        sys.exit(1)
        
    finally:
        browser.close()
        log("🔒 浏览器已关闭")


if __name__ == "__main__":
    run()
