import os
import time
import re
import imaplib
import email
import urllib.request
import urllib.parse
import json
from playwright.sync_api import sync_playwright

# ============================================================
# 配置与解析环境
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
WECHAT_KEY     = os.environ.get("WECHAT_KEY", "")

# ============================================================
# 微信推送函数
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
    data = {"msgtype": "text", "text": {"content": msg}}
    
    try:
        req = urllib.request.Request(
            url, data=json.dumps(data).encode('utf-8'), 
            headers={'Content-Type': 'application/json'}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print("📨 微信推送成功")
    except Exception as e:
        print(f"⚠️ 微信推送失败：{e}")

# ============================================================
# Gmail IMAP 读取 OTP
# ============================================================
def fetch_otp_from_gmail(wait_seconds=60) -> str:
    print(f"📬 连接Gmail，等待发送验证码 (最长 {wait_seconds}s)...")
    deadline = time.time() + wait_seconds

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(KERIT_EMAIL, GMAIL_PASSWORD)

    spam_folder = None
    _, folder_list = mail.list()
    for f in folder_list:
        decoded = f.decode("utf-8", errors="ignore")
        if any(k in decoded for k in ["Spam", "Junk", "垃圾", "spam", "junk"]):
            match = re.search(r'"([^"]+)"\s*$', decoded) or re.search(r'(\S+)\s*$', decoded)
            if match:
                spam_folder = match.group(1).strip('"')
                print(f"🗑️ 探测到垃圾文件夹: {spam_folder}")
                break

    folders_to_check = ["INBOX"]
    if spam_folder:
        folders_to_check.append(spam_folder)

    seen_uids = {}
    for folder in folders_to_check:
        try:
            mail.select(folder)
            _, data = mail.uid("search", None, "ALL")
            seen_uids[folder] = set(data[0].split())
        except Exception:
            seen_uids[folder] = set()

    while time.time() < deadline:
        time.sleep(5)
        for folder in folders_to_check:
            try:
                mail.select(folder)
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
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                    otp = re.search(r'\b(\d{4})\b', body)
                    if otp:
                        code = otp.group(1)
                        print(f"✅ 成功截获 OTP: {code}")
                        mail.logout()
                        return code
            except Exception as e:
                print(f"⚠️ 检查邮箱 [{folder}] 异常: {e}")
                continue

    mail.logout()
    raise TimeoutError("❌ 获取 Gmail OTP 验证码超时")

# ============================================================
# 续期核心模块
# ============================================================
def extract_remaining_days(page) -> int:
    try:
        return page.evaluate("() => parseInt(document.getElementById('expiry-display')?.innerText || '0')") or 0
    except Exception:
        return 0

def do_renew(page):
    print("🔄 正在跳转至续期控制台...")
    page.goto(FREE_PANEL_URL)
    page.wait_for_timeout(4000)
    page.screenshot(path="kerit_free_panel.png")

    server_id = page.evaluate("() => typeof serverData !== 'undefined' ? serverData.id : null")
    if not server_id:
        print("❌ 无法获取 serverData.id")
        page.screenshot(path="kerit_no_server_id.png")
        send_wechat("❌ serverData.id 缺失，终止续期")
        return
    print(f"🆔 服务器唯一ID: {server_id}")

    initial_count = page.evaluate("() => parseInt(document.getElementById('renewal-count')?.innerText || '0')")
    initial_remaining = extract_remaining_days(page)
    need = 7 - initial_count
    print(f"📊 当前进度: {initial_count}/7，剩余天数: {initial_remaining}天，本次需刷续期: {need}次")

    if initial_remaining >= 7 or need <= 0:
        print("🎉 续期天数或次数已达上限，无需继续操作。")
        send_wechat("✅ 无需续期（额度已满）", server_id, initial_remaining)
        return

    for attempt in range(need):
        count = page.evaluate("() => parseInt(document.getElementById('renewal-count')?.innerText || '0')")
        if count >= 7:
            print("🎉 已刷满 7/7 次，提前收工！")
            break

        print(f"🔁 正在发起第 {attempt + 1}/{need} 次续期请求...")
        
        try:
            page.locator('button:has-text("Renew Server"), a:has-text("Renew Server")').first.click(timeout=10000)
            print("  -> 已点击「Renew Server」触发弹窗")
        except Exception:
            print("❌ 续期按钮点击失败")
            page.screenshot(path="kerit_no_renew_btn.png")
            return

        page.wait_for_timeout(2000)

        # 异步捕获及执行 Turnstile 绕过
        token = ""
        for _ in range(20):
            token = page.evaluate("document.querySelector('input[name=\"cf-turnstile-response\"]')?.value || ''")
            if len(token) > 20:
                break
            page.wait_for_timeout(1000)

        if not token:
            print("❌ 拦截错误：未能在页面上捕获到有效的 Turnstile Token")
            continue

        # 模拟前端原生 API 进行异步 Fetch 提交
        result = page.evaluate(f"""
            (async function() {{
                const res = await fetch('/api/renew', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    credentials: 'include',
                    body: JSON.stringify({{ id: '{server_id}', captcha: '{token}' }})
                }});
                return JSON.stringify(await res.json());
            }})()
        """)
        print(f"  -> 接口响应: {result}")

        try:
            page.evaluate("document.querySelector('[data-bs-dismiss=\"modal\"]')?.click();")
        except Exception:
            pass

        page.wait_for_timeout(2000)
        page.reload()
        page.wait_for_timeout(3000)

    page.screenshot(path="kerit_renew_final.png")
    final_count = page.evaluate("() => parseInt(document.getElementById('renewal-count')?.innerText || '0')")
    final_remaining = extract_remaining_days(page)
    send_wechat(f"✅ 续期链条运行结束（当前进度 {final_count}/7）", server_id, final_remaining)

# ============================================================
# 主控入口
# ============================================================
def main():
    print("🚀 启动自动化流...")

    with sync_playwright() as p:
        print("🔗 正在尝试连接 CloakBrowser 调试端口 (127.0.0.1:9222)...")
        browser = None
        
        # 增加主动重试探针，完美避开 ECONNREFUSED
        for attempt in range(6):
            try:
                browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                print("✅ 成功连接至 CloakBrowser！")
                break
            except Exception as e:
                if attempt == 5:
                    print("❌ 重试结束，无法连接到浏览器调试端口。")
                    raise e
                print(f"⚠️ 端口尚未完全释放或处于就绪中，5秒后重试... ({attempt + 1}/6)")
                time.sleep(5)

        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        # IP 验证
        try:
            page.goto("https://api.ipify.org/?format=json", timeout=15000)
            print(f"🌐 节点真实出口 IP: {page.locator('body').text_content()}")
        except Exception:
            print("⚠️ IP 基础检测请求超时，跳过")

        # 登录业务流
        print("🔑 正在定位至登录页...")
        page.goto(LOGIN_URL)
        page.wait_for_timeout(3000)

        # Turnstile 自动化阻尼器
        for _ in range(15):
            if page.evaluate("document.querySelector('input[name=\"cf-turnstile-response\"]') !== null"):
                print("🛡️ 捕捉到 Cloudflare Turnstile 人机挑战，交由 Cloak 后台静默通关...")
                page.wait_for_timeout(2000)
            else:
                break

        try:
            page.wait_for_selector('#email-input', state='visible', timeout=20000)
        except Exception:
            print("❌ 目标页面加载异常，未找到账户输入框")
            page.screenshot(path="kerit_err_no_email.png")
            return

        page.fill('#email-input', KERIT_EMAIL)
        print(f"📝 已注入登录邮箱: {MASKED_EMAIL}")

        # 强点击逻辑
        clicked = False
        for selector in ['button:has-text("Continue with Email")', 'button[type="submit"]', 'form button']:
            try:
                if page.locator(selector).is_visible():
                    page.locator(selector).click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            print("❌ 点击提交失败，按钮未能识别")
            page.screenshot(path="kerit_err_no_submit.png")
            return

        print("⏳ 强制硬停滞 4秒，等待 DOM 重塑...")
        page.wait_for_timeout(4000)

        # 多维特征兼容型 OTP 选择器探针
        otp_selector = 'input[class*="otp"], input[id*="otp"], input[maxlength="1"], input[type="text"]'
        try:
            page.wait_for_selector(otp_selector, state='visible', timeout=25000)
            print("🎯 OTP 输入载体捕获成功")
        except Exception:
            if page.locator('input').first.is_visible():
                print("⚠️ 属性被混淆，启用页面盲盒输入模式")
            else:
                print("❌ 未捕获到任何输入控件")
                page.screenshot(path="kerit_err_no_otp_box.png")
                return

        # 获取并填入 OTP
        code = fetch_otp_from_gmail(wait_seconds=60)
        print(f"⌨️ 正在灌注验证码: {code}")

        # 序列无依赖底层 JavaScript 直接填入法
        js_fill_otp = f"""
            (function() {{
                var inputs = Array.from(document.querySelectorAll('input')).filter(el => {{
                    var style = window.getComputedStyle(el);
                    return style.display !== 'none' && style.visibility !== 'hidden' && el.type !== 'hidden';
                }});
                if (inputs.length < 4) inputs = Array.from(document.querySelectorAll('input'));
                var codeStr = '{code}';
                for (var i = 0; i < Math.min(codeStr.length, inputs.length); i++) {{
                    var inp = inputs[i];
                    var char = codeStr[i];
                    inp.focus();
                    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    nativeInputValueSetter.call(inp, char);
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }})();
        """
        page.evaluate(js_fill_otp)
        page.wait_for_timeout(500)

        # 确认验证
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
            print("❌ 无法提交验证，缺失确认按钮")
            page.screenshot(path="kerit_err_no_verify.png")
            return

        print("⏳ 正在等待重定向会话授权...")
        for _ in range(60):
            if "/session" in page.url or "/free_panel" in page.url:
                print("🎉 突破防线，成功鉴权登录！")
                break
            page.wait_for_timeout(500)
        else:
            print("❌ 登录跳转超时")
            page.screenshot(path="kerit_err_login_timeout.png")
            return

        # 续期执行体
        do_renew(page)

if __name__ == "__main__":
    main()
