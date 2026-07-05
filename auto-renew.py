import os
import sys
import time
import imaplib
import email
import re
import subprocess
import json
import urllib.request
import urllib.parse
from cloakbrowser import launch_persistent_context

# ============================================================
# 配置（从环境变量读取）
# ============================================================

LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"
PROFILE_DIR = "./cloak-profile"

_account = os.environ["KERIT_ACCOUNT"].split(",")
KERIT_EMAIL    = _account[0].strip()
GMAIL_PASSWORD = _account[1].strip()

MASKED_EMAIL   = "******@" + KERIT_EMAIL.split("@")[1]

LOGIN_URL      = "https://billing.kerit.cloud/"
FREE_PANEL_URL = "https://billing.kerit.cloud/free_panel"

# 企业微信机器人配置
WECHAT_WEBHOOK = os.environ.get("WECHAT_WEBHOOK_KEY", "")


# ============================================================
# 企业微信机器人推送
# ============================================================

def now_str():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def send_wechat(result, server_id=None, remaining=None):
    if not WECHAT_WEBHOOK:
        print("⚠️ 企业微信未配置，跳过推送")
        return
    
    lines = [
        f"🎮 <font color='info'>Kerit 服务器续期通知</font>",
        f"🕐 运行时间: {now_str()}",
    ]
    if server_id is not None:
        lines.append(f"🖥 服务器ID: {server_id}")
    
    # 根据结果设置颜色
    if "成功" in result or "✅" in result:
        color = "info"
    elif "失败" in result or "❌" in result:
        color = "warning"
    else:
        color = "comment"
    
    lines.append(f"📊 续期结果: <font color='{color}'>{result}</font>")
    if remaining is not None:
        lines.append(f"⏱️ 剩余天数: {remaining}天")
    
    # ✅ 修正点：确保此行不换行，直接拼接
    msg = "\n".join(lines)
    
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": msg
        }
    }
    
    try:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            WECHAT_WEBHOOK,
            data=data,
            headers={'Content-Type': 'application/json; charset=utf-8'},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode('utf-8'))
            if resp_data.get("errcode") == 0:
                print("📨 企业微信推送成功")
            else:
                print(f"⚠️ 企业微信推送失败: {resp_data}")
    except Exception as e:
        print(f"⚠️ 企业微信推送失败：{e}")


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
    else:
        print("⚠️ 未找到垃圾邮箱")

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
# Turnstile 工具函数 (适配 CloakBrowser/Playwright API)
# ============================================================

EXPAND_POPUP_JS = """
(function() {
    var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (!turnstileInput) return;
    var el = turnstileInput;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflowY === 'hidden') {
            el.style.overflow = 'visible';
        }
        el.style.minWidth = 'max-content';
    }
    var iframes = document.querySelectorAll('iframe');
    iframes.forEach(function(iframe) {
        if (iframe.src && iframe.src.includes('challenges.cloudflare.com')) {
            iframe.style.width = '300px';
            iframe.style.height = '65px';
            iframe.style.minWidth = '300px';
            iframe.style.visibility = 'visible';
            iframe.style.opacity = '1';
        }
    });
})();
"""

def xdotool_click(x, y):
    x, y = int(x), int(y)
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
            capture_output=True, text=True, timeout=3
        )
        wids = [w for w in result.stdout.strip().split('\n') if w]
        if wids:
            subprocess.run(["xdotool", "windowactivate", wids[-1]],
                           timeout=2, stderr=subprocess.DEVNULL)
            time.sleep(0.2)
        subprocess.run(["xdotool", "mousemove", str(x), str(y)], timeout=2, check=True)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, check=True)
        print(f"📐 坐标点击成功")
        return True
    except Exception as e:
        print(f"⚠️ xdotool点击失败：{e}")
        return False


def get_turnstile_coords(page):
    try:
        return page.evaluate("""() => {
            (function(){
                var iframes = document.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    var src = iframes[i].src || '';
                    if (src.includes('cloudflare') || src.includes('turnstile')) {
                        var rect = iframes[i].getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {
                                click_x: Math.round(rect.x + 30),
                                click_y: Math.round(rect.y + rect.height / 2)
                            };
                        }
                    }
                }
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                if (input) {
                    var container = input.parentElement;
                    for (var j = 0; j < 5; j++) {
                        if (!container) break;
                        var rect = container.getBoundingClientRect();
                        if (rect.width > 100 && rect.height > 30) {
                            return {
                                click_x: Math.round(rect.x + 30),
                                click_y: Math.round(rect.y + rect.height / 2)
                            };
                        }
                        container = container.parentElement;
                    }
                }
                return null;
            })()
        }""")
    except Exception:
        return None


def get_window_offset(page):
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
            capture_output=True, text=True, timeout=3
        )
        wids = [w for w in result.stdout.strip().split('\n') if w]
        if wids:
            geo = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", wids[-1]],
                capture_output=True, text=True, timeout=3
            ).stdout
            geo_dict = {}
            for line in geo.strip().split('\n'):
                if '=' in line:
                    k, v = line.split('=', 1)
                    geo_dict[k.strip()] = int(v.strip())
            win_x = geo_dict.get('X', 0)
            win_y = geo_dict.get('Y', 0)
            info = page.evaluate("""() => {
                (function(){ return { outer: window.outerHeight, inner: window.innerHeight }; })()
            }""")
            toolbar = info['outer'] - info['inner']
            if not (30 <= toolbar <= 200):
                toolbar = 87
            return win_x, win_y, toolbar
    except Exception:
        pass
    try:
        info = page.evaluate("""() => {
            (function(){
                return {
                    screenX: window.screenX || 0,
                    screenY: window.screenY || 0,
                    outer: window.outerHeight,
                    inner: window.innerHeight
                };
            })()
        }""")
        toolbar = info['outer'] - info['inner']
        if not (30 <= toolbar <= 200):
            toolbar = 87
        return info['screenX'], info['screenY'], toolbar
    except Exception:
        return 0, 0, 87


def check_token(page) -> bool:
    try:
        return page.evaluate("""() => {
            (function(){
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                return input && input.value && input.value.length > 20;
            })()
        }""")
    except Exception:
        return False


def get_token_value(page) -> str:
    try:
        token = page.evaluate("""() => {
            (function(){
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                return (input && input.value) ? input.value : '';
            })()
        }""")
        if token and len(token) > 20:
            return token
    except Exception:
        pass
    return ''


def turnstile_exists(page) -> bool:
    try:
        return page.evaluate("""() => {
            (function(){ return document.querySelector('input[name="cf-turnstile-response"]') !== null; })()
        }""")
    except Exception:
        return False


def solve_turnstile(page) -> bool:
    for _ in range(3):
        page.evaluate(EXPAND_POPUP_JS)
        time.sleep(0.5)

    if check_token(page):
        print("✅ Token已存在")
        return True

    coords = get_turnstile_coords(page)
    if not coords:
        print("❌ 无法获取坐标")
        return False

    win_x, win_y, toolbar = get_window_offset(page)
    abs_x = coords['click_x'] + win_x
    abs_y = coords['click_y'] + win_y + toolbar
    print(f"🖱️ 点击Token: ({abs_x}, {abs_y})")
    xdotool_click(abs_x, abs_y)

    for _ in range(30):
        time.sleep(0.5)
        if check_token(page):
            print("✅ Cloudflare Token通过")
            return True

    print("❌ Cloudflare Token超时")
    page.screenshot(path="turnstile_fail.png")
    return False


def extract_remaining_days(page) -> int:
    """从 expiry-display 元素读取剩余天数"""
    try:
        return page.evaluate("""() => {
            (function(){
                var el = document.getElementById('expiry-display');
                return el ? parseInt(el.innerText || "0") : 0;
            })()
        }""") or 0
    except Exception:
        return 0


# ============================================================
# 续期流程
# ============================================================

def do_renew(page):
    print("🔄 跳转续期页...")
    page.goto(FREE_PANEL_URL, wait_until="domcontentloaded")
    time.sleep(4)
    page.screenshot(path="free_panel.png")

    server_id = page.evaluate("""() => {
        return (function(){ return typeof serverData !== 'undefined' ? serverData.id : null; })()
    }""")
    if not server_id:
        print("❌ serverData.id缺失")
        page.screenshot(path="no_server_id.png")
        send_wechat("❌ serverData.id缺失，续期失败")
        return
    print(f"🆔 服务器ID: {server_id}")

    initial_count = page.evaluate("""() => {
        return (function(){
            var el = document.getElementById('renewal-count');
            return el ? parseInt(el.innerText || "0") : 0;
        })()
    }""")
    initial_remaining = extract_remaining_days(page)
    need = 7 - initial_count
    print(f"📊 当前进度: {initial_count}/7，剩余天数: {initial_remaining}天，本次需续期: {need}次")

    if initial_remaining >= 7:
        print("✅ 剩余天数已满7天，无需续期")
        page.screenshot(path="renew_skip.png")
        send_wechat("✅ 无需续期（剩余天数已满）", server_id, initial_remaining)
        return

    if need <= 0:
        print("🎉 已达上限7/7，无需续期")
        page.screenshot(path="renew_full.png")
        remaining = extract_remaining_days(page)
        send_wechat("✅ 无需续期（已达上限 7/7）", server_id, remaining)
        return

    for attempt in range(need):
        count = page.evaluate("""() => {
            return (function(){
                var el = document.getElementById('renewal-count');
                return el ? parseInt(el.innerText || "0") : 0;
            })()
        }""")
        print(f"📊 续期进度: {count}/7")

        if count >= 7:
            print("🎉 已达上限7/7，提前结束")
            page.screenshot(path="renew_full.png")
            remaining = extract_remaining_days(page)
            send_wechat("✅ 续期完成", server_id, remaining)
            return

        print(f"🔁 第{attempt + 1}/{need}次续期...")

        # 点击 Renew Server 按钮
        renew_clicked = False
        for _ in range(10):
            try:
                buttons = page.locator('a, button').all()
                for btn in buttons:
                    text = btn.text_content() or ""
                    if "Renew Server" in text:
                        btn.click()
                        renew_clicked = True
                        print("✅ 已点击「Renew Server」")
                        break
                if renew_clicked:
                    break
            except Exception:
                pass
            time.sleep(1)

        if not renew_clicked:
            print("❌ 续期按钮缺失")
            page.screenshot(path="no_renew_btn.png")
            send_wechat(f"❌ 续期按钮缺失，第{attempt + 1}次失败", server_id)
            return

        time.sleep(2)

        print("⏳ 等待Turnstile...")
        for _ in range(20):
            if turnstile_exists(page):
                print("🛡️ 检测到Turnstile")
                break
            time.sleep(1)
        else:
            print("❌ Turnstile未出现")
            page.screenshot(path=f"no_turnstile_{attempt}.png")
            send_wechat(f"❌ Turnstile未出现，第{attempt + 1}次失败", server_id)
            return

        if not solve_turnstile(page):
            page.screenshot(path=f"turnstile_fail_{attempt}.png")
            send_wechat(f"❌ Turnstile验证失败，第{attempt + 1}次", server_id)
            return

        token = get_token_value(page)
        if not token:
            print("❌ Token获取失败")
            send_wechat(f"❌ Token获取失败，第{attempt + 1}次", server_id)
            return

        print("🎯 提交续期...")
        result = page.evaluate(f"""() => {{
            return (async function() {{
                const res = await fetch('/api/renew', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    credentials: 'include',
                    body: JSON.stringify({{ id: '{server_id}', captcha: '{token}' }})
                }});
                const data = await res.json();
                return JSON.stringify(data);
            }})()
        }}""")
        try:
            res_obj = json.loads(result)
            if res_obj.get('success') or res_obj == {}:
                print("✅ 续期成功")
            else:
                print(f"❌ 续期失败: {result}")
        except Exception:
            print(f"✅ 续期成功")

        try:
            page.evaluate("""() => {
                document.querySelector('[data-bs-dismiss="modal"]')?.click();
            }""")
        except Exception:
            pass

        time.sleep(3)
        page.reload(wait_until="domcontentloaded")
        time.sleep(3)

    page.screenshot(path="renew_done.png")
    final_count = page.evaluate("""() => {
        return (function(){
            var el = document.getElementById('renewal-count');
            return el ? parseInt(el.innerText || "0") : 0;
        })()
    }""")
    final_remaining = extract_remaining_days(page)
    print(f"📊 最终进度: {final_count}/7")
    if final_count >= 7:
        print("🎉 已达上限7/7")
        send_wechat("✅ 续期完成", server_id, final_remaining)
    else:
        print(f"⚠️ 续期未达上限，当前{final_count}/7")
        send_wechat(f"⚠️ 续期未达上限（{final_count}/7）", server_id, final_remaining)


# ============================================================
# 主流程：邮箱OTP登录 + 续期
# ============================================================

def main():
    if not KERIT_EMAIL or not GMAIL_PASSWORD:
        print("❌ 错误: 未配置 KERIT_ACCOUNT 环境变量！格式: email,password")
        sys.exit(1)

    print("=" * 60)
    print("🚀 启动 CloakBrowser + Kerit 邮箱OTP自动登录续期")
    print("=" * 60)
    
    os.environ["CLOAKBROWSER_SUPPRESS_FONT_WARNING"] = "1"
    
    context = launch_persistent_context(
        PROFILE_DIR,
        headless=False,
        humanize=True,
        human_preset="careful",
        proxy={"server": LOCAL_SOCKS5},
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",  # ✅ 增加最大化参数，保证 xdotool 坐标映射更稳定
        ],
    )
    
    page = context.new_page()

    try:
        # ── IP 验证 ──────────────────────────────────────────
        print("🌐 验证出口IP...")
        try:
            page.goto("https://api.ipify.org/?format=json", wait_until="domcontentloaded")
            ip_text = page.locator('body').text_content()
            ip_text = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1xx', ip_text)
            print(f"✅ 出口IP确认：{ip_text}")
        except Exception:
            print("⚠️ IP验证超时，跳过")

        # ── 登录 ─────────────────────────────────────────────
        print("🔑 打开登录页面...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        time.sleep(3)

        print("🛡️ 检查Cloudflare...")
        for _ in range(20):
            time.sleep(0.5)
            if turnstile_exists(page):
                print("🛡️ 检测到Turnstile...")
                if not solve_turnstile(page):
                    page.screenshot(path="kerit_cf_fail.png")
                    send_wechat("❌ 登录页Turnstile验证失败")
                    return
                time.sleep(2)
                break
        else:
            print("✅ 无Turnstile，继续")

        print("📭 等待邮箱框...")
        try:
            page.wait_for_selector('#email-input', state='visible', timeout=20000)
        except Exception:
            print("❌ 邮箱框加载失败")
            page.screenshot(path="kerit_no_email_input.png")
            send_wechat("❌ 邮箱框加载失败")
            return

        page.locator('#email-input').fill(KERIT_EMAIL)
        print(f"✅ 邮箱：{MASKED_EMAIL}")

        print("🖱️ 点击继续...")
        clicked = False

        # 先等待按钮可点击
        page.wait_for_load_state('networkidle', timeout=15000)
        time.sleep(1)

        for selector in [
            'button:has-text("Continue with Email")',
            'a:has-text("Continue with Email")',
            'button[type="submit"]',
        ]:
            try:
                if page.locator(selector).count() > 0:
                    page.locator(selector).first.click(timeout=10000)
                    clicked = True
                    print(f"✅ 通过选择器点击成功: {selector}")
                    break
            except Exception as e:
                print(f"  选择器 {selector} 失败: {e}")
                continue

        if not clicked:
            # 使用 JavaScript 点击
            result = page.evaluate("""() => {
                const buttons = Array.from(document.querySelectorAll('button, a'));
                for (const btn of buttons) {
                    const text = btn.textContent.trim().toLowerCase();
                    if (text.includes('continue with email') || text === 'continue') {
                        btn.scrollIntoView({ behavior: 'instant', block: 'center' });
                        btn.click();
                        return {found: true, text: btn.textContent.trim()};
                    }
                }
                return {found: false};
            }""")
            clicked = result.get("found", False)
            if clicked:
                print(f"✅ 通过JS点击成功: {result.get('text')}")

        if not clicked:
            print("❌ 继续按钮缺失")
            page.screenshot(path="kerit_no_continue_btn.png")
            send_wechat("❌ 继续按钮缺失")
            return

        print("⏳ 等待页面跳转/OTP框出现...")
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        time.sleep(2)

        # 截图查看当前状态
        page.screenshot(path="kerit_after_click.png")

        print("📨 等待OTP框...")
        try:
            page.wait_for_selector('.otp-input', state='visible', timeout=30000)
        except Exception:
            print("❌ OTP框加载失败")
            page.screenshot(path="kerit_no_otp.png")
            send_wechat("❌ OTP框加载失败")
            return

        try:
            code = fetch_otp_from_gmail(wait_seconds=60)
        except TimeoutError as e:
            print(e)
            page.screenshot(path="kerit_otp_timeout.png")
            send_wechat("❌ Gmail OTP获取超时")
            return

        otp_inputs = page.locator('.otp-input').all()
        if len(otp_inputs) < 4:
            print(f"❌ OTP框不足: {len(otp_inputs)}")
            send_wechat(f"❌ OTP框数量不足（{len(otp_inputs)}）")
            return

        print(f"⌨️ 填入OTP: {code}")
        for i, char in enumerate(code):
            js = f"""
                (function() {{
                    var inputs = document.querySelectorAll('.otp-input');
                    var inp = inputs[{i}];
                    if (!inp) return;
                    var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    nativeInputValueSetter.call(inp, '{char}');
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }})();
            """
            page.evaluate(js)
            time.sleep(0.1)

        print("✅ OTP已填入")
        time.sleep(0.5)

        print("🚀 点击验证...")
        verify_clicked = False
        for selector in [
            'button:has-text("Verify Code")',
            'a:has-text("Verify Code")',
            'button[type="submit"]',
        ]:
            try:
                if page.locator(selector).count() > 0:
                    page.click(selector, timeout=10000)
                    verify_clicked = True
                    break
            except Exception:
                continue

        if not verify_clicked:
            result = page.evaluate("""() => {
                const buttons = Array.from(document.querySelectorAll('button, a'));
                for (const btn of buttons) {
                    if (btn.textContent.toLowerCase().includes('verify') || 
                        btn.textContent.toLowerCase().includes('verify code')) {
                        btn.click();
                        return {found: true, text: btn.textContent.trim()};
                    }
                }
                return {found: false};
            }""")
            verify_clicked = result.get("found", False)

        if not verify_clicked:
            print("❌ 验证按钮缺失")
            page.screenshot(path="kerit_no_verify_btn.png")
            send_wechat("❌ 验证按钮缺失")
            return

        print("⏳ 等待登录跳转...")
        for _ in range(80):
            try:
                url = page.url
                if "/session" in url:
                    print("✅ 登录成功！")
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            print("❌ 登录等待超时")
            page.screenshot(path="kerit_login_timeout.png")
            send_wechat("❌ 登录等待超时")
            return

        # ── 执行续期 ─────────────────────────────────────────
        do_renew(page)

        # 保存会话
        context.storage_state(path="kerit_auth.json")
        print("\n" + "=" * 60)
        print("🎉 任务完成！会话已保存至 kerit_auth.json")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 自动化链条断裂: {str(e)}")
        try:
            page.screenshot(path="error_screenshot.png")
            print("📸 错误截图已保存: error_screenshot.png")
        except:
            pass
        sys.exit(1)
    finally:
        context.close()


if __name__ == "__main__":
    main()
