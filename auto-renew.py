import os
import sys
import time
from cloakbrowser import launch_persistent_context

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"
PROFILE_DIR = "./cloak-profile"


def inject_discord_token(page, token):
    """
    全面注入 Discord Token 到所有可能的位置。
    Discord 2024+ 的安全策略要求多位置注入。
    """
    # 清理 Token（去掉两端引号）
    clean_token = token.strip().strip('"').strip("'")
    
    if len(clean_token) < 50:
        print("  ⚠️ Token 长度异常，可能无效！")
    
    print(f"  🔑 注入 Token (前15位): {clean_token[:15]}...")
    
    result = page.evaluate("""(token) => {
        const t = token;
        const results = [];
        
        // 1. localStorage - 标准位置
        try { 
            localStorage.setItem("token", '"' + t + '"'); 
            results.push("localStorage: OK");
        } catch(e) { results.push("localStorage: FAIL - " + e.message); }
        
        // 2. sessionStorage
        try { 
            sessionStorage.setItem("token", '"' + t + '"'); 
            results.push("sessionStorage: OK");
        } catch(e) { results.push("sessionStorage: FAIL - " + e.message); }
        
        // 3. Cookie（多域名覆盖）
        try { 
            document.cookie = "token=" + encodeURIComponent('"' + t + '"') + "; path=/; domain=.discord.com";
            document.cookie = "token=" + encodeURIComponent('"' + t + '"') + "; path=/; domain=discord.com";
            results.push("Cookie: OK");
        } catch(e) { results.push("Cookie: FAIL - " + e.message); }
        
        // 4. 覆盖 Discord 内部 webpack 模块的 getToken
        try {
            if (window.webpackChunkdiscord_app) {
                window.webpackChunkdiscord_app.push([
                    [Math.random()], {}, 
                    req => {
                        for (const m of Object.keys(req.c || {})) {
                            const mod = req.c[m].exports;
                            if (mod && mod.default && typeof mod.default.getToken === 'function') {
                                mod.default.getToken = () => t;
                            }
                            if (mod && typeof mod.getToken === 'function') {
                                mod.getToken = () => t;
                            }
                        }
                    }
                ]);
                results.push("webpack: OK");
            } else {
                results.push("webpack: SKIP (not loaded)");
            }
        } catch(e) { results.push("webpack: FAIL - " + e.message); }
        
        // 5. 直接设置 window 对象
        try { 
            window.token = t; 
            window.__DISCORD_TOKEN__ = t;
            results.push("window: OK");
        } catch(e) { results.push("window: FAIL - " + e.message); }
        
        // 6. 尝试设置 document.defaultView
        try {
            if (document.defaultView) {
                document.defaultView.localStorage.setItem("token", '"' + t + '"');
                results.push("defaultView: OK");
            }
        } catch(e) { results.push("defaultView: FAIL - " + e.message); }
        
        return results;
    }""", clean_token)
    
    for r in result:
        print(f"    {r}")
    print("  ✅ Token 注入完成")


def wait_for_cloudflare(page, timeout=90):
    """
    等待 Cloudflare 验证完成。
    验证通过后页面会在根路径 / 上显示登录界面。
    """
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
        
        # 检查是否还在验证页
        if any(x in title.lower() for x in ["just a moment", "security verification", "verifying"]):
            print(f"  🔄 仍在验证页... ({int(time.time()-start)}s)")
            time.sleep(2)
            continue
        
        # 检查是否已显示登录界面
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
        # geoip=True,  # 禁用，避免 Failed to discover exit IP 错误
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    
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
                    return {found: true, text: discordLink.textContent.trim(), tag: discordLink.tagName};
                }
                return {found: false, html: document.body.innerText.substring(0, 300)};
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
            print(f"  当前 URL: {page.url}")
            page.screenshot(path="error_discord_redirect.png")
            sys.exit(1)
        
        time.sleep(3)
        
        # ==================== Step 5: 访问 Discord 主站注入 Token ====================
        print("\n📌 Step 5: 访问 Discord 主站并注入 Token...")
        
        # 先访问 Discord app 页面，确保在正确的域下
        page.goto("https://discord.com/app", wait_until="domcontentloaded")
        time.sleep(5)
        
        # 注入 Token
        inject_discord_token(page, DISCORD_TOKEN)
        
        # 刷新确认登录状态
        print("  🔄 刷新页面验证登录状态...")
        page.reload(wait_until="domcontentloaded")
        time.sleep(8)
        
        # 检查是否已登录（URL 应该变成 /channels/@me 或 /app，而不是 /login）
        current_url = page.url
        print(f"  当前 URL: {current_url}")
        
        if "discord.com/login" in current_url:
            print("❌ Token 无效或已过期，仍然需要登录！")
            page.screenshot(path="error_discord_login.png")
            sys.exit(1)
        
        if "/app" in current_url or "/channels" in current_url or "/library" in current_url:
            print("  ✅ Discord 登录成功！")
        else:
            print("  ⚠️ Discord 状态不确定，继续尝试...")
        
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
        
        # 先截图看当前状态
        page.screenshot(path="debug_oauth2_page.png")
        print("  📸 已保存 OAuth2 页面截图: debug_oauth2_page.png")
        
        # 滚动到底部确保按钮可见
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        
        # 尝试点击授权按钮（支持多种文本）
        auth_result = page.evaluate("""() => {
            const buttons = document.querySelectorAll("button, a[role='button']");
            const candidates = [];
            
            for (const btn of buttons) {
                const txt = btn.textContent.toLowerCase().trim();
                candidates.push(txt);
                
                // 匹配多种可能的按钮文本
                if (txt.includes("authorize") || 
                    txt.includes("授权") || 
                    txt.includes("allow") ||
                    txt.includes("continue") ||
                    txt.includes("log in") ||
                    txt.includes("login") ||
                    txt === "yes" ||
                    txt === "确认") {
                    btn.click();
                    return {clicked: true, text: btn.textContent.trim(), matched: txt};
                }
            }
            
            // 兜底：找 submit 按钮
            const submit = document.querySelector('button[type="submit"]');
            if (submit) {
                submit.click();
                return {clicked: true, text: submit.textContent.trim(), matched: "submit fallback"};
            }
            
            return {clicked: false, candidates: candidates.slice(0, 10)};
        }""")
        print(f"  授权结果: {auth_result}")
        time.sleep(10)
        
        # ==================== Step 8: 等待回到 Kerit ====================
        print("\n📌 Step 8: 等待重定向回 Kerit...")
        
        # 给页面一些时间跳转
        for _ in range(20):
            current_url = page.url
            print(f"  当前 URL: {current_url[:100]}")
            
            if "billing.kerit.cloud" in current_url and "discord" not in current_url:
                if "clientarea" in current_url or "dashboard" in current_url or "/auth/discord/callback" in current_url:
                    print(f"  ✅ 成功回到 Kerit！")
                    break
            
            if "discord.com/oauth2" in current_url:
                print("  🔄 仍在 OAuth2 页面，等待中...")
            
            time.sleep(2)
        else:
            print("  ⚠️ 跳转超时，检查最终状态...")
        
        # 最终检查
        final_url = page.url
        print(f"  最终 URL: {final_url}")
        
        if "kerit" in final_url and "login" not in final_url and "oauth2" not in final_url:
            print("  ✅ 判断为登录成功！")
        elif "billing.kerit.cloud/auth/discord/callback" in final_url:
            print("  ✅ 已收到 Discord 回调，等待页面加载...")
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
            print("📸 错误截图已保存: error_screenshot.png")
        except:
            pass
        sys.exit(1)
    finally:
        context.close()


if __name__ == "__main__":
    main()
