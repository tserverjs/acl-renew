import os
import sys
import time
# 注意：同步模式下，虽然 CloakBrowser 替换了 Playwright，但我们依然可以直接使用它的同步 API
from cloakbrowser import launch 

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"

def main():
    if not DISCORD_TOKEN:
        print("[错误] 未配置 DISCORD_TOKEN 环境变量！")
        sys.exit(1)

    print("🚀 正在启动 CloakBrowser (纯同步模式 + 住宅代理 + 内核最大化)...")
    
    # 移除 await，直接同步 launch
    browser = launch(
        headless=True,
        humanize=True,
        proxy={"server": LOCAL_SOCKS5},
        args=[
            "--start-maximized",        
            "--window-size=1920,1080"   
        ]
    )
    
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()

    try:
        print("正在打开 Kerit 登录页...")
        page.goto("https://billing.kerit.cloud/login")
        time.sleep(4)

        print("尝试寻找并点击 Discord 登录链接...")
        try:
            page.click('a:has-text("Discord"), button:has-text("Discord")')
        except:
            page.evaluate("document.querySelector('a[href*=\"discord\"]')?.click();")
        
        print("正在等待跳转至 Discord 授权域...")
        page.wait_for_url("**/discord.com/**", timeout=25000)
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
        
        # 成功登录后备份状态
        context.storage_state(path="kerit_auth.json")
        print("已同步将会话状态保存至 kerit_auth.json")

    except Exception as e:
        print(f"❌ 自动化链条断裂: {str(e)}")
        sys.exit(1)
    finally:
        browser.close()

if __name__ == "__main__":
    main()
