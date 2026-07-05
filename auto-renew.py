import os
import time
import re
import imaplib
import email
import subprocess
import urllib.request
import urllib.parse
from playwright.sync_api import sync_playwright

# ============================================================
# 配置（从环境变量读取）
# ============================================================

_account = os.environ.get("KERIT_ACCOUNT", "").split(",")
if len(_account) >= 2:
    KERIT_EMAIL    = _account[0].strip()
    GMAIL_PASSWORD = _account[1].strip()
else:
    KERIT_EMAIL    = ""
    GMAIL_PASSWORD = ""

MASKED_EMAIL   = "******@" + KERIT_EMAIL.split("@")[1] if "@" in KERIT_EMAIL else "******"

LOGIN_URL      = "https://billing.kerit.cloud/"
FREE_PANEL_URL = "https://billing.kerit.cloud/free_panel"

WECHAT_KEY = os.environ.get("WECHAT_KEY", "")

# ============================================================
# 微信推送
# ============================================================

def now_str():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def send_wechat(result, server_id=None, remaining=None):
    lines = [
        f"🎮 Kerit 服务器续期通知",
        f"🕐 运行时间: {now_str()}",
    ]
    if server_id is not None:
        lines.append(f"🖥 服务器ID: {server_id}")
    lines.append(f"📊 续期结果: {result}")
    if remaining is not None:
        lines.append(f"⏱️ 剩余天数: {remaining}天")
    msg = "\n".join(lines)
    
    if not WECHAT_KEY:
        print("⚠️ WECHAT_KEY未配置，跳过推送")
        return
        
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECHAT_KEY}"
    data = {
        "msgtype": "text",
        "text": {
            "content": msg
        }
    }
    
    import json
    try:
        req = urllib.request.Request(
            url, 
            data=json.dumps(data).encode('utf-8'), 
            headers={'Content-Type': 'application/json'},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"📨 微信推送成功")
    except Exception as e:
        print(f"⚠️ 微信推送失败：{e}")

# ============================================================
# IMAP 读取 Gmail OTP
# ============================================================

def fetch_otp_from_gmail(wait_seconds=60) -> str:
    print(f"📬 连接Gmail，等待{wait_seconds}s...")
    deadline = time.time() + wait_seconds

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(KERIT_EMAIL, GMAIL_PASSWORD)

    spam_folder = None
    _, folder_list = mail.list()
    for f in folder_list:
        decoded = f.decode("utf-8", errors="ignore")
        if any(k in decoded for k in ["Spam", "Junk", "垃圾", "spam", "junk"]):
            match = re.search(r'"([^"]+)"\s*$', decoded)
            if not match:
                match = re.search(r'(\S+)\s*$', decoded)
            if match:
                spam_folder = match.group(1).strip('"')
                print(f"🗑️ 检查Gmail垃圾邮箱")
                break

    folders_to_check = ["INBOX"]
    if spam_folder:
        folders_to_check.append(spam_folder)

    seen_uids = {}
    for folder in folders_to_check:
        try:
            status, _ = mail.select(folder)
            if status != "OK":
                raise Exception(f"select失败: {status}")
            _, data = mail.uid("search", None, "ALL")
            seen_uids[folder] = set(data[0].split())
        except Exception as e:
            print(f"⚠️ 文件夹异常 {folder}: {e}")
            seen_uids[folder] = set()

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

                    otp = re.search(r'\b(\d{4})\b', body)
                    if otp:
                        code = otp.group(1)
                        print(f"✅ Gmail OTP: {code}")
                        mail.logout()
                        return code

            except Exception as e:
                print(f"⚠️ 检查{folder}出错: {e}")
                continue

    mail.logout()
    raise TimeoutError("❌ Gmail超时")

# ============================================================
# 辅助解析函数
# ============================================================

def extract_remaining_days(page) -> int:
    try:
        return page.evaluate("""
            (function(){
                var el = document.getElementById('expiry-display');
                return el ? parseInt(el.innerText || "0") : 0;
            })()
        """) or 0
    except Exception:
        return 0

# ============================================================
# 后续续期流程
# ============================================================

def do_renew(page):
    print("🔄 跳转续期页...")
    page.goto(FREE_PANEL_URL)
    page.wait_for_timeout(4000)
    page.screenshot(path="free_panel.png")

    server_id = page.evaluate("(function(){ return typeof serverData !== 'undefined' ? serverData.id : null; })()")
    if not server_id:
        print("❌ serverData.id缺失")
        page.screenshot(path="no_server_id.png")
        send_wechat("❌ serverData.id缺失，续期失败")
        return
    print(f"🆔 服务器ID: {server_id}")

    initial_count = page.evaluate("""
        (function(){
            var el = document.getElementById('renewal-count');
            return el ? parseInt(el.innerText || "0") : 0;
        })()
    """)
    initial_remaining = extract_remaining_days(page)
    need = 7 - initial_count
    print(f"📊 当前进度: {initial_count}/7，剩余天数: {initial_remaining}天，本次需续期: {need}次")

    if initial_remaining >= 7:
        print("✅ 剩余天数已满7天，无需续期")
        send_wechat("✅ 无需续期（剩余天数已满）", server_id, initial_remaining)
        return

    if need <= 0:
        print("🎉 已达上限7/7，无需续期")
        send_wechat("✅ 无需续期（已达上限 7/7）", server_id, initial_remaining)
        return

    for attempt in range(need):
        count = page.evaluate("""
            (function(){
                var el = document.getElementById('renewal-count');
                return el ? parseInt(el.innerText || "0") : 0;
            })()
        """)
        print(f"📊 续期进度: {count}/7")

        if count >= 7:
            print("🎉 已达上限7/7，提前结束")
            remaining = extract_remaining_days(page)
            send_wechat("✅ 续期完成", server_id, remaining)
            return

        print(f"🔁 第{attempt + 1}/{need}次续期...")

        # 点击 Renew Server 按钮
        try:
            page.locator('button:has-text("Renew Server"), a:has-text("Renew Server")').first.click(timeout=10000)
            print("✅ 已点击「Renew Server」")
        except Exception:
            print("❌ 续期按钮缺失")
            page.screenshot(path="no_renew_btn.png")
            send_wechat(f"❌ 续期按钮缺失，第{attempt + 1}次失败", server_id)
            return

        page.wait_for_timeout(2000)

        # 续期内部如果有 Turnstile 拦截，在此通过 API 异步提交
        print("🎯 获取Token并提交续期...")
        # 等待网页内 Turnstile 渲染出 response（CloakBrowser 通常会自动过）
        token = ""
        for _ in range(20):
            token = page.evaluate("document.querySelector('input[name=\"cf-turnstile-response\"]')?.value || ''")
            if len(token) > 20:
                break
            page.wait_for_timeout(1000)
            
        if not token:
            print("❌ 续期页面 Turnstile Token 获取失败")
            send_wechat(f"❌ Token获取失败，第{attempt + 1}次", server_id)
            return

        result = page.evaluate(f"""
            (async function() {{
                const res = await fetch('/api/renew', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    credentials: 'include',
                    body: JSON.stringify({{ id: '{server_id}', captcha: '{token}' }})
                }});
                const data = await res.json();
                return JSON.stringify(data);
            }})()
        """)
        print(f"🎯 接口返回结果: {result}")

        try:
            page.evaluate("document.querySelector('[data-bs-dismiss=\"modal\"]')?.click();")
        except Exception:
            pass

        page.wait_for_timeout(3000)
        page.reload()
        page.wait_for_timeout(3000)

    page.screenshot(path="renew_done.png")
    final_count = page.evaluate("(function(){ var el = document.getElementById('renewal-count'); return el ? parseInt(el.innerText || '0') : 0; })()")
    final_remaining = extract_remaining_days(page)
    print(f"📊 最终进度: {final_count}/7")
    send_wechat(f"✅ 续期运行完毕（当前进度 {final_count}/7）", server_id, final_remaining)

# ============================================================
# 主流程
# ============================================================

def main():
    print("🚀 启动 CloakBrowser + Kerit 邮箱OTP自动登录续期")
    print("============================================================")
    
    with sync_playwright() as p:
        # 连接到已启动的 CloakBrowser 独立浏览器实例
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        # ── IP 验证 ──────────────────────────────────────────
        print("🌐 验证出口IP...")
        try:
            page.goto("https://api.ipify.org/?format=json", timeout=15000)
            ip_text = page.locator('body').text_content()
            ip_text = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1xx', ip_text)
            print(f"✅ 出口IP确认：{ip_text}")
        except Exception:
            print("⚠️ IP验证超时，跳过")

        # ── 登录 ─────────────────────────────────────────────
        print("🔑 打开登录页面...")
        page.goto(LOGIN_URL)
        page.wait_for_timeout(3000)

        print("🛡️ 检查Cloudflare...")
        # CloakBrowser 会自动处理滑块或挑战，这里做探测等待
        for _ in range(15):
            is_cf = page.evaluate("document.querySelector('input[name=\"cf-turnstile-response\"]') !== null")
            if is_cf:
                print("🛡️ 检测到Turnstile挑战框，等待CloakBrowser自动通过...")
                page.wait_for_timeout(2000)
            else:
                break
        print("✅ 无Turnstile，继续")

        print("📭 等待邮箱框...")
        try:
            page.wait_for_selector('#email-input', state='visible', timeout=20000)
        except Exception:
            print("❌ 邮箱框加载失败")
            page.screenshot(path="kerit_no_email_input.png")
            send_wechat("❌ 邮箱框加载失败")
            return

        page.fill('#email-input', KERIT_EMAIL)
        print(f"✅ 邮箱：{MASKED_EMAIL}")

        print("🖱️ 点击继续...")
        clicked = False
        for selector in ['button:has-text("Continue with Email")', 'button[type="submit"]', 'form button']:
            try:
                if page.locator(selector).is_visible():
                    page.locator(selector).click(timeout=5000)
                    clicked = True
                    print(f"✅ 通过选择器强行点击成功: {selector}")
                    break
            except Exception:
                continue

        if not clicked:
            print("❌ 继续按钮缺失或点击失败")
            page.screenshot(path="kerit_no_continue_btn.png")
            send_wechat("❌ 继续按钮缺失")
            return

        print("⏳ 等待页面跳转/OTP框出现...")
        page.wait_for_timeout(4000)  # 稳固停留，等待目标 DOM 树重新分发

        print("📨 等待OTP框...")
        # 兼容性多特征组合探测器，避开脆弱的纯 Class 定位
        otp_selector = 'input[class*="otp"], input[id*="otp"], input[maxlength="1"], input[type="text"]'
        try:
            page.wait_for_selector(otp_selector, state='visible', timeout=25000)
            print("✅ 成功发现并锁定验证码输入区")
        except Exception:
            # 保底方案：即使输入框被魔改没有属性，只要页面还有任何 input 就强行推进
            try:
                if page.locator('input').first.is_visible():
                    print("⚠️ 未发现标准特征的 OTP 输入框，锁定普通 Input 输入框作为备用")
                else:
                    raise Exception()
            except Exception:
                print("❌ OTP框加载失败")
                page.screenshot(path="kerit_no_otp.png")
                send_wechat("❌ OTP框加载失败")
                return

        # 获取邮件 OTP 码
        try:
            code = fetch_otp_from_gmail(wait_seconds=60)
        except TimeoutError as e:
            print(e)
            page.screenshot(path="kerit_otp_timeout.png")
            send_wechat("❌ Gmail OTP获取超时")
            return

        print(f"⌨️ 填入OTP: {code}")
        
        # 核心修复：强力免依赖 JS 序列填入法
        # 获取全部可见框，不论类名是否变动，按物理序列强制注入
        js_fill_otp = f"""
            (function() {{
                var inputs = Array.from(document.querySelectorAll('input')).filter(el => {{
                    var style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.type !== 'hidden';
                }});
                
                if (inputs.length < 4) {{
                    inputs = Array.from(document.querySelectorAll('input'));
                }}

                var codeStr = '{code}';
                for (var i = 0; i < Math.min(codeStr.length, inputs.length); i++) {{
                    var inp = inputs[i];
                    var char = codeStr[i];
                    
                    inp.focus();
                    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    nativeInputValueSetter.call(inp, char);
                    
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    inp.dispatchEvent(new KeyboardEvent('keydown', {{ key: char, bubbles: true }}));
                    inp.dispatchEvent(new KeyboardEvent('keyup', {{ key: char, bubbles: true }}));
                }}
            }})();
        """
        try:
            page.evaluate(js_fill_otp)
            print("✅ OTP已填入")
        except Exception as e:
            print(f"⚠️ JS 填写验证码异常: {e}")
            
        page.wait_for_timeout(500)

        print("🚀 点击验证...")
        verify_clicked = False
        for selector in ['button:has-text("Verify Code")', 'button[type="submit"]', 'form button.btn-primary']:
            try:
                if page.locator(selector).is_visible():
                    page.locator(selector).click(timeout=5000)
                    verify_clicked = True
                    break
            except Exception:
                continue

        if not verify_clicked:
            print("❌ 验证按钮缺失")
            page.screenshot(path="kerit_no_verify_btn.png")
            send_wechat("❌ 验证按钮缺失")
            return

        print("⏳ 等待登录跳转...")
        for _ in range(60):
            if "/session" in page.url or "/free_panel" in page.url:
                print("✅ 登录成功！")
                break
            page.wait_for_timeout(500)
        else:
            print("❌ 登录等待超时")
            page.screenshot(path="kerit_login_timeout.png")
            send_wechat("❌ 登录等待超时")
            return

        # 执行续期循环
        do_renew(page)

if __name__ == "__main__":
    main()
