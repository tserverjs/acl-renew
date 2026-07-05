import os
import sys
import time
from cloakbrowser import launch_persistent_context

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"
PROFILE_DIR = "./cloak-profile"


def inject_discord_token_via_cdp(context, token):
    """
    使用 CDP (Chrome DevTools Protocol) 在浏览器级别注入 Token。
    这比 page.evaluate 更底层，可以绕过页面上下文限制。
    """
    clean_token = token.strip().strip('"').strip("'")
    print(f"  🔑 准备注入 Token (前15位): {clean_token[:15]}...")
    
    # 方法1: 使用 add_init_script 在所有页面创建时注入
    # 这会注入到每个新页面的 main world 中
    context.add_init_script(f"""
        (function() {{
            const token = '{clean_token}';
            const storageKey = 'token';
            const tokenValue = '"' + token + '"';
            
            // 劫持 localStorage
            Object.defineProperty(window, 'localStorage', {{
                configurable: true,
                enumerable: true,
                get: function() {{
                    const store = {{}};
                    store[storageKey] = tokenValue;
                    store.getItem = function(k) {{ return k === storageKey ? tokenValue : null; }};
                    store.setItem = function(k, v) {{}};
                    store.removeItem = function(k) {{}};
                    store.clear = function() {{}};
                    return store;
                }}
            }});
            
            // 同时设置真实的 localStorage（如果可用）
            try {{
                if (typeof Storage !== 'undefined') {{
                    const origSetItem = Storage.prototype.setItem;
                    Storage.prototype.setItem = function(k, v) {{
                        if (k === storageKey) return;
                        return origSetItem.apply(this, arguments);
                    }};
                }}
                window.__proto__.localStorage.setItem(storageKey, tokenValue);
            }} catch(e) {{}}
            
            // 设置 window.token
            window.__DISCORD_TOKEN__ = token;
            window.token = token;
        }})();
    """)
    
    print("  ✅ Init script 已注入到上下文")


def inject_discord_token_direct(page, token):
    """
    直接在页面中注入，使用多种方法确保成功。
    """
    clean_token = token.strip().strip('"').strip("'")
    print(f"  🔑 直接注入 Token (前15位): {clean_token[:15]}...")
    
    # 方法1: 尝试通过 JS 直接设置
    result = page.evaluate("""(token) => {
        const results = [];
        const t = token;
        const tv = '"' + t + '"';
        
        // 方法A: 直接操作 window
        try {
            window.localStorage = window.localStorage || {};
            window.localStorage.setItem = window.localStorage.setItem || function(){};
            window.localStorage.setItem("token", tv);
            results.push("window.localStorage.setItem: OK");
        } catch(e) { results.push("window.localStorage.setItem: " + e.message); }
        
        // 方法B: 使用 Object.defineProperty 劫持
        try {
            const fakeStorage = {
                data: {token: tv},
                getItem: function(k) { return this.data[k] || null; },
                setItem: function(k, v) { this.data[k] = v; },
                removeItem: function(k) { delete this.data[k]; },
                clear: function() { this.data = {}; }
            };
            Object.defineProperty(window, 'localStorage', {
                value: fakeStorage,
                writable: false,
                configurable: true
            });
            results.push("defineProperty localStorage: OK");
        } catch(e) { results.push("defineProperty localStorage: " + e.message); }
        
        // 方法C: 通过 document.defaultView
        try {
            if (document.defaultView) {
                document.defaultView.localStorage = document.defaultView.localStorage || {};
                document.defaultView.localStorage.setItem("token", tv);
                results.push("defaultView.localStorage: OK");
            }
        } catch(e) { results.push("defaultView.localStorage: " + e.message); }
        
        // 方法D: Cookie
        try {
            document.cookie = "token=" + encodeURIComponent(tv) + "; path=/; domain=.discord.com; Secure";
            document.cookie = "token=" + encodeURIComponent(tv) + "; path=/; domain=discord.com; Secure";
            results.push("Cookie: OK");
        } catch(e) { results.push("Cookie: " + e.message); }
        
        // 方法E: 直接设置全局变量
        try {
            window.token = t;
            window.__DISCORD_TOKEN__ = t;
            results.push("window.token: OK");
        } catch(e) { results.push("window.token: " + e.message); }
        
        return results;
    }""", clean_token)
    
    for r in result:
        print(f"    {r}")
    
    # 方法2: 使用 page.route 拦截请求并注入
    # 拦截 Discord 的 API 请求，在请求头中注入 Token
    print("  🌐 设置请求拦截注入...")
    page.route("https://discord.com/api/**", lambda route, request: route.continue_(
        headers={
            **request.headers,
            "Authorization": clean_token
        }
    ))
    
    print("  ✅ 直接注入完成")


def wait_for_cloudflare(page, timeout=90):
    print("⏳ 等待 Cloudflare 验证完成...")
    start = time.time()
    
    while time.time() - start < timeout:
        url = page.url
        title = page.title()
        content = ""
        try:
            content = page.content()
        except:
            pass
        
        if any(x in title.lower() for x in ["just a moment", "security verification", "verifying"]):
            print(f"  🔄 仍在验证页... ({int(time.time()-start)}s)")
            time.sleep(2)
            continue
        
        if "billing.kerit.cloud" in url:
            has_discord = "continue with discord" in content.lower()
            has_email = "continue with email" in content.lower() or "email address" in content.lower()
            has_cf_success = "成功" in content or "success" in content.lower()
            
            if has_discord or has_email or has_cf_success:
                print(f"  ✅ 登录界面已加载！")
                return True
        
        time.sleep(2)
    
    print(f"  ⚠️ 等待超时，当前 URL: {page.url}")
    return False


def main():
    if not DISCORD_TOKEN:
        print("❌ 错误: 未配置 DISCORD_TOKEN 环境变量！")
        sys.exit(1)

    print("=" * 60)
    print("🚀 启动 CloakBrowser + Kerit 自动登录")
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
        ],
    )
    
    # 预先注入 Token 到上下文（对所有新页面生效）
    inject_discord_token_via_cdp(context, DISCORD_TOKEN)
    
    page = context.new_page()

    try:
        # ==================== Step 1: 打开 Kerit 首页 ====================
        print("\n📌 Step 1: 打开 Kerit 首页...")
        page.goto("https://billing.kerit.cloud/", wait_until="domcontentloaded")
        time.sleep(3)
        
        # ==================== Step 2: 等待 Cloudflare 验证 ====================
        print("\n📌 Step 2: 等待 Cloudflare 验证...")
        if not wait_for_cloudflare(page, timeout=90):
            print("⚠️ 验证超时，尝试刷新...")
            page.reload(wait_until="domcontentloaded")
            time.sleep(5)
            if not wait_for_cloudflare(page, timeout=30):
                print("❌ 无法通过 Cloudflare 验证")
                sys.exit(1)
        
        page.screenshot(path="debug_login_page.png")
        print("  📸 已保存调试截图: debug_login_page.png")
        
        # ==================== Step 3: 点击 Discord 登录 ====================
        print("\n📌 Step 3: 点击 Discord 登录...")
        
        discord_selectors = [
            'button:has-text("Continue with Discord")',
            'a:has-text("Continue with Discord")',
            '[class*="discord" i]',
            'button:has-text("Discord")',
            'a:has-text("Discord")',
        ]
        
        clicked = False
        for selector in discord_selectors:
            try:
                if page.locator(selector).count() > 0:
                    print(f"  ✅ 找到 Discord 按钮: {selector}")
                    page.click(selector, timeout=10000)
                    clicked = True
                    break
            except Exception as e:
                print(f"  ❌ 选择器失败 {selector}: {e}")
        
        if not clicked:
            print("  📟 执行 JS 兜底...")
            result = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a, button'));
                const discordLink = links.find(el => 
                    el.textContent.toLowerCase().includes('continue with discord') ||
                    el.textContent.toLowerCase().includes('discord')
                );
                if (discordLink) {
                    discordLink.click();
                    return {found: true, text: discordLink.textContent.trim()};
                }
                return {found: false};
            }""")
            print(f"  JS 结果: {result}")
            clicked = result.get("found", False)
            time.sleep(3)
        
        if not clicked:
            print("❌ 无法找到 Discord 登录入口！")
            page.screenshot(path="error_no_discord.png")
            sys.exit(1)
        
        # ==================== Step 4: 等待跳转到 Discord ====================
        print("\n📌 Step 4: 等待 Discord 页面...")
        try:
            page.wait_for_url(lambda url: "discord.com" in url, timeout=40000)
            print(f"  ✅ 已进入 Discord: {page.url[:80]}...")
        except Exception as e:
            print(f"  ❌ 未跳转到 Discord: {e}")
            page.screenshot(path="error_discord_redirect.png")
            sys.exit(1)
        
        time.sleep(3)
        
        # ==================== Step 5: 在 Discord 页面注入 Token ====================
        print("\n📌 Step 5: 在 Discord 页面注入 Token...")
        
        # 先访问 Discord 登录页，确保在正确的域下
        page.goto("https://discord.com/login", wait_until="domcontentloaded")
        time.sleep(5)
        
        # 使用直接注入方法
        inject_discord_token_direct(page, DISCORD_TOKEN)
        
        # 刷新页面，让 Token 生效
        print("  🔄 刷新页面验证登录状态...")
        page.reload(wait_until="networkidle")
        time.sleep(10)
        
        current_url = page.url
        print(f"  当前 URL: {current_url}")
        
        # 检查是否已登录
        if "discord.com/login" in current_url:
            # 再试一次：可能是刷新后又被重定向到登录页
            print("  ⚠️ 仍在登录页，再次注入并尝试...")
            inject_discord_token_direct(page, DISCORD_TOKEN)
            
            # 尝试直接访问 app 页面
            page.goto("https://discord.com/app", wait_until="domcontentloaded")
            time.sleep(8)
            current_url = page.url
            
            if "discord.com/login" in current_url:
                print("❌ Token 无效或已过期！")
                page.screenshot(path="error_discord_login.png")
                sys.exit(1)
        
        if "/app" in current_url or "/channels" in current_url:
            print("  ✅ Discord 登录成功！")
        else:
            print(f"  ⚠️ 当前在: {current_url}，继续尝试...")
        
        # ==================== Step 6: 前往 OAuth2 授权页 ====================
        print("\n📌 Step 6: 前往 Kerit OAuth2 授权页...")
        
        oauth_url = ("https://discord.com/oauth2/authorize"
                     "?client_id=1432019029245038835"
                     "&redirect_uri=https%3A%2F%2Fbilling.kerit.cloud%2Fauth%2Fdiscord%2Fcallback"
                     "&response_type=code"
                     "&scope=identify+email"
                     "&prompt=consent"
                     "&state=login")
        
        page.goto(oauth_url, wait_until="domcontentloaded")
        time.sleep(5)
        
        # ==================== Step 7: 处理 OAuth2 授权 ====================
        print("\n📌 Step 7: 处理 Discord OAuth2 授权...")
        
        page.screenshot(path="debug_oauth2_page.png")
        print("  📸 已保存 OAuth2 页面截图")
        
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        
        # 再次注入 Token（OAuth2 页面可能需要）
        inject_discord_token_direct(page, DISCORD_TOKEN)
        
        auth_result = page.evaluate("""() => {
            const buttons = document.querySelectorAll("button, a[role='button']");
            const candidates = [];
            
            for (const btn of buttons) {
                const txt = btn.textContent.toLowerCase().trim();
                candidates.push(txt);
                
                if (txt.includes("authorize") || 
                    txt.includes("授权") || 
                    txt.includes("allow") ||
                    txt.includes("continue") ||
                    txt.includes("log in") ||
                    txt.includes("login") ||
                    txt === "yes") {
                    btn.click();
                    return {clicked: true, text: btn.textContent.trim(), matched: txt};
                }
            }
            
            const submit = document.querySelector('button[type="submit"]');
            if (submit) {
                submit.click();
                return {clicked: true, text: submit.textContent.trim(), matched: "submit"};
            }
            
            return {clicked: false, candidates: candidates.slice(0, 10)};
        }""")
        print(f"  授权结果: {auth_result}")
        time.sleep(10)
        
        # ==================== Step 8: 等待回到 Kerit ====================
        print("\n📌 Step 8: 等待重定向回 Kerit...")
        
        for i in range(30):
            current_url = page.url
            print(f"  [{i}] 当前 URL: {current_url[:100]}")
            
            if "billing.kerit.cloud" in current_url and "discord" not in current_url:
                if "clientarea" in current_url or "dashboard" in current_url or "/auth/discord/callback" in current_url:
                    print(f"  ✅ 成功回到 Kerit！")
                    break
            
            if "discord.com/oauth2" in current_url:
                print("  🔄 仍在 OAuth2 页面...")
            
            time.sleep(2)
        else:
            print("  ⚠️ 跳转超时")
        
        final_url = page.url
        print(f"  最终 URL: {final_url}")
        
        if "kerit" in final_url and "login" not in final_url and "oauth2" not in final_url:
            print("  ✅ 登录成功！")
        elif "billing.kerit.cloud/auth/discord/callback" in final_url:
            print("  ✅ 已收到 Discord 回调，等待加载...")
            time.sleep(5)
            # 检查是否自动跳转到了 dashboard
            if "kerit" in page.url and "login" not in page.url:
                print("  ✅ 自动跳转成功！")
            else:
                # 手动跳转到 dashboard
                page.goto("https://billing.kerit.cloud/clientarea.php", wait_until="domcontentloaded")
                time.sleep(5)
        else:
            print("❌ 登录流程未完成")
            page.screenshot(path="error_final.png")
            sys.exit(1)
        
        # 保存会话
        context.storage_state(path="kerit_auth.json")
        print("\n" + "=" * 60)
        print("🎉 完美通关！会话已保存至 kerit_auth.json")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 自动化链条断裂: {str(e)}")
        try:
            page.screenshot(path="error_screenshot.png")
            print("📸 错误截图已保存")
        except:
            pass
        sys.exit(1)
    finally:
        context.close()


if __name__ == "__main__":
    main()
