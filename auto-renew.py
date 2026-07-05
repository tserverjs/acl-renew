import os
import sys
import time
from cloakbrowser import launch_persistent_context

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"
PROFILE_DIR = "./cloak-profile"  # 持久化用户配置，避免无痕检测

def solve_turnstile_managed_challenge(page, max_retries=3):
    """
    处理 Cloudflare Managed Turnstile（显式点击挑战）。
    CloakBrowser 的 C++ 补丁能自动通过 non-interactive 模式，
    但 managed 模式需要 humanize 的拟人化点击。
    """
    print("🔍 扫描 Cloudflare Turnstile 挑战...")
    
    for attempt in range(max_retries):
        # 多种可能的 Turnstile iframe 选择器
        turnstile_selectors = [
            "iframe[src*='challenges.cloudflare.com']",
            "iframe[src*='turnstile']",
            "iframe[src*='cloudflare']",
            ".cf-turnstile",
            "#turnstile-widget",
            "iframe[title*='challenge']",
        ]
        
        found = False
        for selector in turnstile_selectors:
            try:
                count = page.locator(selector).count()
                if count > 0:
                    found = True
                    print(f"  🔒 发现挑战元素: {selector} (尝试 {attempt+1}/{max_retries})")
                    
                    # 方法1: 在 iframe 内部点击
                    try:
                        frame = page.frame_locator(selector)
                        # Turnstile 内部可能的点击目标
                        targets = [
                            "input[type='checkbox']",
                            ".mark",
                            "#challenge-stage",
                            "body",
                        ]
                        for target in targets:
                            if frame.locator(target).count() > 0:
                                print(f"  🎯 点击 iframe 内目标: {target}")
                                frame.locator(target).first.click(timeout=10000)
                                print("  ⏳ 等待验证响应...")
                                time.sleep(12)
                                return True
                    except Exception as e:
                        print(f"  ⚠️ iframe 内点击失败: {e}")
                    
                    # 方法2: 直接点击 iframe 元素本身（兜底）
                    try:
                        print("  🎯 兜底：直接点击 iframe 元素")
                        page.locator(selector).first.click(timeout=10000)
                        time.sleep(12)
                        return True
                    except Exception as e:
                        print(f"  ⚠️ iframe 点击失败: {e}")
                        
            except Exception:
                continue
        
        if not found:
            print("  ✅ 未发现显式挑战框，可能已通过 non-interactive 模式")
            return True
            
        time.sleep(3)
    
    print("  ⚠️ Turnstile 处理完成（可能仍需等待）")
    return False

def wait_for_page_ready(page, timeout=60):
    """
    智能等待页面真正加载完成（而非卡在 CF 验证页）。
    """
    print("⏳ 等待页面就绪...")
    start = time.time()
    
    while time.time() - start < timeout:
        url = page.url
        title = page.title()
        content = ""
        try:
            content = page.content()[:500]
        except:
            pass
        
        # 检查是否还在 CF 验证页
        if any(x in title.lower() for x in ["just a moment", "security verification", "verifying"]):
            print(f"  🔄 仍在验证页... ({int(time.time()-start)}s)")
            time.sleep(2)
            continue
            
        # 检查 URL 是否已跳转
        if "billing.kerit.cloud/login" in url and "cloudflare" not in url.lower():
            # 检查是否有实际内容
            if "discord" in content.lower() or "login" in content.lower() or "kerit" in content.lower():
                print(f"  ✅ 页面已就绪: {title}")
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
    
    # 核心配置：持久化上下文 + 反检测最优参数
    # 参考官方推荐配置: https://github.com/CloakHQ/CloakBrowser
    context = launch_persistent_context(
        PROFILE_DIR,
        headless=False,
        humanize=True,
        human_preset="careful",  # 更谨慎的移动模式，适合敏感站点
        proxy={"server": LOCAL_SOCKS5},
        geoip=True,              # 自动从代理IP推断时区和语言
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        args=[
            "--no-sandbox",           # GitHub Actions 容器必需
            "--disable-dev-shm-usage", # 避免 /dev/shm 空间不足
            "--disable-blink-features=AutomationControlled",
        ],
    )
    
    page = context.new_page()

    try:
        # Step 1: 打开登录页
        print("\n📌 Step 1: 打开 Kerit 登录页...")
        page.goto("https://billing.kerit.cloud/login", wait_until="domcontentloaded")
        time.sleep(3)
        
        # Step 2: 处理 Turnstile（如果需要）
        print("\n📌 Step 2: 处理 Cloudflare 验证...")
        solve_turnstile_managed_challenge(page)
        
        # Step 3: 等待页面真正就绪
        print("\n📌 Step 3: 等待页面加载完成...")
        if not wait_for_page_ready(page, timeout=60):
            print("⚠️ 页面可能未完全加载，尝试继续...")
        
        # 调试截图
        page.screenshot(path="debug_step3_page_ready.png")
        print("  📸 已保存调试截图: debug_step3_page_ready.png")
        
        # Step 4: 查找并点击 Discord 登录
        print("\n📌 Step 4: 查找 Discord 登录按钮...")
        
        discord_selectors = [
            'a:has-text("Discord")',
            'button:has-text("Discord")',
            'a[href*="discord"]',
            'button:has-text("Login with Discord")',
            'a:has-text("Login with Discord")',
            '[class*="discord" i]',
            '[id*="discord" i]',
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
            print("  📟 未找到标准按钮，执行 JS 兜底...")
            result = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a, button'));
                const discordLink = links.find(el => 
                    el.textContent.toLowerCase().includes('discord') ||
                    (el.href && el.href.includes('discord'))
                );
                if (discordLink) {
                    discordLink.click();
                    return {found: true, text: discordLink.textContent.trim()};
                }
                return {found: false, html: document.body.innerHTML.substring(0, 1000)};
            }""")
            print(f"  JS 结果: {result}")
            time.sleep(3)
            clicked = result.get("found", False)
        
        if not clicked:
            print("❌ 无法找到 Discord 登录入口！")
            page.screenshot(path="error_no_discord.png")
            sys.exit(1)
        
        # Step 5: 等待跳转到 Discord
        print("\n📌 Step 5: 等待 Discord 授权页...")
        try:
            page.wait_for_url(lambda url: "discord.com" in url or "discordapp.com" in url, timeout=40000)
            print(f"  ✅ 已进入 Discord 域: {page.url}")
        except Exception as e:
            print(f"  ❌ 未跳转到 Discord: {e}")
            print(f"  当前 URL: {page.url}")
            page.screenshot(path="error_no_discord_redirect.png")
            sys.exit(1)
        
        time.sleep(5)
        
        # Step 6: 注入 Discord Token
        print("\n📌 Step 6: 注入 Discord Token...")
        page.evaluate("""(token_val) => {
            const token = token_val.trim().replace(/^"|"$/g, '');
            // 注入 localStorage
            try { localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            // 注入 sessionStorage
            try { sessionStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            // 设置 document.cookie 中的 token（某些场景需要）
            try { document.cookie = "token=" + encodeURIComponent(token) + "; path=/; domain=.discord.com"; } catch(e) {}
            return "Token injected";
        }""", DISCORD_TOKEN)
        print("  ✅ Token 注入完成")
        
        # Step 7: 刷新页面唤醒登录状态
        print("\n📌 Step 7: 刷新页面以应用 Token...")
        page.reload(wait_until="domcontentloaded")
        time.sleep(10)
        
        # 检查是否还在登录页
        if "discord.com/login" in page.url:
            print("❌ Token 无效或账号被风控！")
            page.screenshot(path="error_discord_login.png")
            sys.exit(1)
        
        # Step 8: 处理 OAuth2 授权页
        if "discord.com/oauth2" in page.url:
            print("\n📌 Step 8: 处理 Discord OAuth2 授权...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
            # 点击 Authorize 按钮
            auth_result = page.evaluate("""() => {
                const buttons = document.querySelectorAll("button");
                for (const btn of buttons) {
                    const txt = btn.textContent.toLowerCase();
                    if (txt.includes("authorize") || txt.includes("授权") || txt.includes("allow")) {
                        btn.click();
                        return {clicked: true, text: btn.textContent.trim()};
                    }
                }
                // 兜底：找 submit 按钮
                const submit = document.querySelector('button[type="submit"]');
                if (submit) {
                    submit.click();
                    return {clicked: true, text: submit.textContent.trim()};
                }
                return {clicked: false};
            }""")
            print(f"  授权结果: {auth_result}")
            time.sleep(8)
        
        # Step 9: 等待回到 Kerit
        print("\n📌 Step 9: 等待重定向回 Kerit...")
        try:
            page.wait_for_url("**/clientarea.php*", timeout=40000)
            print(f"  ✅ 成功登录！当前 URL: {page.url}")
        except Exception as e:
            print(f"  ⚠️ 未匹配到 clientarea.php，检查当前状态...")
            print(f"  当前 URL: {page.url}")
            # 如果 URL 包含 kerit 且不是登录页，也算成功
            if "kerit" in page.url and "login" not in page.url:
                print("  ✅ 判断为登录成功（URL 验证通过）")
            else:
                page.screenshot(path="error_final_redirect.png")
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
