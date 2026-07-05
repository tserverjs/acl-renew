import os
import sys
import time
from cloakbrowser import launch_persistent_context

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"
PROFILE_DIR = "./cloak-profile"

def wait_for_cloudflare(page, timeout=90):
    """
    等待 Cloudflare 验证完成。
    验证通过后页面会在根路径 / 上显示登录界面（URL 仍带 __cf_chl_f_tk 参数）。
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
        
        # 检查是否已显示登录界面（无论 URL 是否带参数）
        if "billing.kerit.cloud" in url:
            # 检查关键元素：Discord 按钮或 Email 输入框
            has_discord = "continue with discord" in content.lower() or "discord" in content.lower()
            has_email = "continue with email" in content.lower() or "email address" in content.lower()
            has_cf_success = "成功" in content or "success" in content.lower()
            
            if has_discord or has_email or has_cf_success:
                print(f"  ✅ 登录界面已加载！URL: {url[:80]}...")
                return True
        
        time.sleep(2)
    
    print(f"  ⚠️ 等待超时，当前状态: {page.url}")
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
        geoip=True,
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
        # Step 1: 访问根路径（Cloudflare 会自动拦截并验证）
        print("\n📌 Step 1: 打开 Kerit 首页...")
        page.goto("https://billing.kerit.cloud/", wait_until="domcontentloaded")
        time.sleep(3)
        
        # Step 2: 等待 CF 验证完成
        print("\n📌 Step 2: 等待 Cloudflare 验证...")
        if not wait_for_cloudflare(page, timeout=90):
            print("⚠️ 验证超时，尝试刷新...")
            page.reload(wait_until="domcontentloaded")
            time.sleep(5)
            if not wait_for_cloudflare(page, timeout=30):
                print("❌ 无法通过 Cloudflare 验证")
                sys.exit(1)
        
        # 调试截图
        page.screenshot(path="debug_login_page.png")
        print("  📸 已保存调试截图: debug_login_page.png")
        
        # Step 3: 点击 Discord 登录按钮
        print("\n📌 Step 3: 点击 Discord 登录...")
        
        # 精确匹配按钮文本
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
                return {found: false, html: document.body.innerText.substring(0, 500)};
            }""")
            print(f"  JS 结果: {result}")
            clicked = result.get("found", False)
            time.sleep(3)
        
        if not clicked:
            print("❌ 无法找到 Discord 登录入口！")
            page.screenshot(path="error_no_discord.png")
            sys.exit(1)
        
        # Step 4: 等待跳转到 Discord
        print("\n📌 Step 4: 等待 Discord 授权页...")
        try:
            page.wait_for_url(lambda url: "discord.com" in url, timeout=40000)
            print(f"  ✅ 已进入 Discord: {page.url}")
        except Exception as e:
            print(f"  ❌ 未跳转到 Discord: {e}")
            print(f"  当前 URL: {page.url}")
            page.screenshot(path="error_discord_redirect.png")
            sys.exit(1)
        
        time.sleep(5)
        
        # Step 5: 注入 Discord Token
        print("\n📌 Step 5: 注入 Discord Token...")
        page.evaluate("""(token_val) => {
            const token = token_val.trim().replace(/^"|"$/g, '');
            try { localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            try { sessionStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            try { document.cookie = "token=" + encodeURIComponent(token) + "; path=/; domain=.discord.com"; } catch(e) {}
            return "Token injected";
        }""", DISCORD_TOKEN)
        print("  ✅ Token 注入完成")
        
        # Step 6: 刷新页面
        print("\n📌 Step 6: 刷新页面以应用 Token...")
        page.reload(wait_until="domcontentloaded")
        time.sleep(10)
        
        if "discord.com/login" in page.url:
            print("❌ Token 无效或账号被风控！")
            page.screenshot(path="error_discord_login.png")
            sys.exit(1)
        
        # Step 7: 处理 OAuth2 授权
        if "discord.com/oauth2" in page.url:
            print("\n📌 Step 7: 处理 Discord OAuth2 授权...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
            auth_result = page.evaluate("""() => {
                const buttons = document.querySelectorAll("button");
                for (const btn of buttons) {
                    const txt = btn.textContent.toLowerCase();
                    if (txt.includes("authorize") || txt.includes("授权") || txt.includes("allow")) {
                        btn.click();
                        return {clicked: true, text: btn.textContent.trim()};
                    }
                }
                const submit = document.querySelector('button[type="submit"]');
                if (submit) {
                    submit.click();
                    return {clicked: true, text: submit.textContent.trim()};
                }
                return {clicked: false};
            }""")
            print(f"  授权结果: {auth_result}")
            time.sleep(8)
        
        # Step 8: 等待回到 Kerit
        print("\n📌 Step 8: 等待重定向回 Kerit...")
        try:
            page.wait_for_url("**/clientarea.php*", timeout=40000)
            print(f"  ✅ 成功登录！URL: {page.url}")
        except Exception as e:
            print(f"  ⚠️ 未匹配到 clientarea.php...")
            print(f"  当前 URL: {page.url}")
            if "kerit" in page.url and "login" not in page.url and "oauth2" not in page.url:
                print("  ✅ 判断为登录成功")
            else:
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
