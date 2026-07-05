import os
import sys
import time
from cloakbrowser import launch 

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"

def solve_cloudflare_turnstile(page):
    """深入 Iframe 内部精准定位并点击 Turnstile 复选框"""
    print("⏳ 正在扫描页面底层 DOM 结构...")
    time.sleep(5) # 留出缓冲时间让 Cloudflare 完成初始化
    
    # 核心定位器：锁定 Cloudflare 验证码的特征 Iframe
    iframe_selector = "iframe[src*='challenges.cloudflare.com']"
    
    if page.locator(iframe_selector).count() > 0:
        print("🔒 确凿检测到 Cloudflare Turnstile 显式挑战框！准备执行穿透点击...")
        try:
            # 1. 定位到该 iframe 上下文
            cf_frame = page.frame_locator(iframe_selector)
            
            # 2. 在 iframe 内部寻找复选框容器或点击区域
            # Turnstile 内部的点击目标通常是 #challenge-stage 或里面的 checkbox 元素
            checkbox = cf_frame.locator("#challenge-stage, input[type='checkbox'], .mark")
            
            if checkbox.count() > 0:
                print("🎯 已成功锁定 Iframe 内部的复选框目标，正在模拟人类点击...")
                # 使用 click 会自动触发 CloakBrowser 的拟人化贝塞尔曲线轨迹和时序
                checkbox.first.click(timeout=10000)
                print("⏳ 点击指令已发送，给予 15 秒等待放行期...")
                time.sleep(15)
            else:
                print("⚠️ 找到了 Iframe，但未探测到内部的有效点击元素，尝试直接对其中心施加模拟点击...")
                # 兜底：对整个 iframe 容器进行点击
                page.locator(iframe_selector).first.click()
                time.sleep(15)
                
        except Exception as e:
            print(f"⚠️ 穿透点击过程中发生异常: {str(e)}")
    else:
        print("❓ 未在当前层级发现显式挑战框，可能处于静默流或已被指纹混淆通过。")

def main():
    if not DISCORD_TOKEN:
        print("[错误] 未配置 DISCORD_TOKEN 环境变量！")
        sys.exit(1)

    print("🚀 正在启动 CloakBrowser (Headed 真实桌面渲染 + 拟人化引擎)...")
    
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
        
        # 🌟 触发精准穿透点击
        solve_cloudflare_turnstile(page)

        print("正在检查页面是否已解密成功...")
        time.sleep(5)

        print("尝试寻找并点击 Discord 登录链接...")
        try:
            # 显式等待按钮出现，防止超前点击
            page.wait_for_selector('a:has-text("Discord"), button:has-text("Discord"), a[href*="discord"]', timeout=15000)
            page.click('a:has-text("Discord"), button:has-text("Discord")')
        except Exception as e:
            print(f"📟 标准按钮未就绪 ({str(e)})，执行强行破门机制...")
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
