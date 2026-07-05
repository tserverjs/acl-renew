import os
import sys
import time
from cloakbrowser import launch 

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"

def solve_cloudflare_turnstile(page):
    """专门穿透 iframe 并点击 Cloudflare Turnstile 复选框"""
    print("⏳ 正在检测页面中是否存在 Cloudflare Turnstile 验证码...")
    
    # 留出 8 秒时间让 Cloudflare 尝试静默过验或加载出验证框
    time.sleep(8)
    
    # 检查是否还在挑战页
    if "Performance security verification" in page.content() or "cf-challenge" in page.url or page.locator("iframe[src*='challenges']").count() > 0:
        print("🔒 触发 Cloudflare 显式挑战，尝试定位验证框...")
        try:
            # 1. 找到 Turnstile 的安全 iframe
            turnstile_iframe = page.wait_for_selector("iframe[src*='challenges.cloudflare.com']", timeout=15000)
            if turnstile_iframe:
                print("🎯 已锁定 Turnstile 核心 Iframe，正在计算复选框物理绝对坐标...")
                
                # 获取该 iframe 的相对屏幕位置和大小
                box = turnstile_iframe.bounding_box()
                if box:
                    # 复选框一般在 iframe 的偏左侧 (比如宽度的 15% 处，高度的 50% 居中处)
                    click_x = box["x"] + 30
                    click_y = box["y"] + box["height"] / 2
                    
                    print(f"🖱️ 拟人化鼠标正在精准移动至物理坐标: ({click_x}, {click_y}) 并实施点击...")
                    page.mouse.move(click_x, click_y, steps=10)
                    page.mouse.click(click_x, click_y)
                    
                    print("⏳ 点击完毕，等待 Cloudflare 验证通过 (15秒放行期)...")
                    time.sleep(15)
        except Exception as e:
            print(f"⚠️ 尝试点击 Turnstile 框时发生异常 (可能已静默过验): {str(e)}")
    else:
        print("✅ 完美！系统指纹过关，Cloudflare 未弹出显式验证码，直接进入了登录页。")

def main():
    if not DISCORD_TOKEN:
        print("[错误] 未配置 DISCORD_TOKEN 环境变量！")
        sys.exit(1)

    print("🚀 正在启动 CloakBrowser (Headed 真实桌面渲染模式 + GOST 代理)...")
    
    # 抑制没有 Windows 字体的警告（如果上面工作流安装成功，这里指纹就已经完美了）
    os.environ["CLOAKBROWSER_SUPPRESS_FONT_WARNING"] = "1"
    
    browser = launch(
        headless=False, 
        humanize=True,
        proxy={"server": LOCAL_SOCKS5}
    )
    
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()

    try:
        print("正在打开 Kerit 登录页...")
        page.goto("https://billing.kerit.cloud/login")
        
        # 🌟 核心拦截：在此处强行注入过验逻辑
        solve_cloudflare_turnstile(page)

        print("尝试寻找并点击 Discord 登录链接...")
        try:
            # 重新确认一次元素是否可点击
            page.wait_for_selector('a:has-text("Discord"), button:has-text("Discord"), a[href*="discord"]', timeout=15000)
            page.click('a:has-text("Discord"), button:has-text("Discord")')
        except:
            print("📟 按钮未就绪，尝试使用 JS 强制破门机制触发点击...")
            page.evaluate("document.querySelector('a[href*=\"discord\"]')?.click();")
        
        print("正在等待跳转至 Discord 授权域...")
        page.wait_for_url("**/discord.com/**", timeout=40000)
        time.sleep(5)

        print("已成功切入 Discord 域，开始强行注入 Token 凭据...")
        page.evaluate("""(token_val) => {
            var token = token_val.trim().replace(/^"|"$/g, '');
            var f = document.createElement("iframe");
            f.style.display = "none";
            document.body.appendChild(f);
            try { f.contentWindow.localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            try { localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            document.body.removeChild(f);
        }""", DISCORD_TOKEN)

        print("Token 注入完毕，正在刷新 Discord 页面以唤醒状态...")
        page.reload()
        time.sleep(10)

        if "discord.com/login" in page.url:
            print("❌ 严重错误：Discord Token 无效或遭到风控拦截！")
            sys.exit(1)

        if "discord.com/oauth2" in page.url:
            print("捕捉到 Discord 授权挂载页，启动多重滚动突破...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

            print("正在自动寻找并模拟点击 'Authorize' 按钮...")
            page.evaluate("""() => {
                var buttons = document.querySelectorAll("button");
                var clicked = false;
                buttons.forEach(function(btn){
                    var txt = btn.textContent.toLowerCase();
                    if(txt.includes("authorize") || txt.includes("授权")) {
                        btn.click();
                        clicked = true;
                    }
                });
                if(!clicked){
                     var authBtn = document.querySelector('button[type="submit"]');
                     if(authBtn) authBtn.click();
                }
            }""")
            time.sleep(8)

        print("正在等待重定向回 Kerit 客户中心...")
        page.wait_for_url("**/clientarea.php*", timeout=40000)
        print("🎉 [完美通关] 成功通过 CloakBrowser 登录进后台！")
        
        context.storage_state(path="kerit_auth.json")
        print("已同步将会话状态保存至 kerit_auth.json")

    except Exception as e:
        print(f"❌ 自动化链条断裂: {str(e)}")
        sys.exit(1)
    finally:
        browser.close()

if __name__ == "__main__":
    main()
