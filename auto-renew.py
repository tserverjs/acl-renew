import asyncio
import os
import sys
from cloakbrowser import launch 

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
LOCAL_SOCKS5 = "socks5://127.0.0.1:40000"

async def main():
    if not DISCORD_TOKEN:
        print("[错误] 未配置 DISCORD_TOKEN 环境变量！")
        sys.exit(1)

    print("🚀 正在启动 CloakBrowser (挂载本地 GOST 住宅/节点隧道并强制窗口最大化)...")
    
    # 🌟 结合内核层参数设置，双重保障最大化
    browser = await launch(
        headless=True,
        humanize=True,
        proxy={"server": LOCAL_SOCKS5},
        args=[
            "--start-maximized",        # 让 Chromium 启动时直接尝试外壳窗口最大化
            "--window-size=1920,1080"   # 初始化外壳分辨率对齐工作流
        ]
    )
    
    # 维持网页内部渲染的视口大小
    context = await browser.new_context(viewport={"width": 1920, "height": 1080})
    page = await context.new_page()

    try:
        print("正在打开 Kerit 登录页...")
        await page.goto("https://billing.kerit.cloud/login")
        await page.wait_for_timeout(4000)

        print("尝试寻找并点击 Discord 登录链接...")
        try:
            await page.click('a:has-text("Discord"), button:has-text("Discord")')
        except:
            await page.evaluate("document.querySelector('a[href*=\"discord\"]')?.click();")
        
        print("正在等待跳转至 Discord 授权域...")
        await page.wait_for_url("**/discord.com/**", timeout=25000)
        await page.wait_for_timeout(5000)

        print("已成功切入 Discord 域，开始强行注入 Token 凭据...")
        await page.evaluate("""(token_val) => {
            var token = token_val.trim().replace(/^"|"$/g, '');
            var f = document.createElement("iframe");
            f.style.display = "none";
            document.body.appendChild(f);
            try { f.contentWindow.localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            try { localStorage.setItem("token", '"'+token+'"'); } catch(e) {}
            document.body.removeChild(f);
        }""", DISCORD_TOKEN)

        print("Token 注入完毕，正在刷新 Discord 页面以唤醒状态...")
        await page.reload()
        await page.wait_for_timeout(10000)

        if "discord.com/login" in page.url:
            print("❌ 严重错误：Discord Token 无效或遭到风控拦截！")
            sys.exit(1)

        if "discord.com/oauth2" in page.url:
            print("捕捉到 Discord 授权挂载页，启动多重滚动突破...")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await page.wait_for_timeout(2000)

            print("正在自动寻找并模拟点击 'Authorize' 按钮...")
            await page.evaluate("""() => {
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
            await page.wait_for_timeout(8000)

        print("正在等待重定向回 Kerit 客户中心...")
        await page.wait_for_url("**/clientarea.php*", timeout=40000)
        print("🎉 [完美通关] 成功通过 CloakBrowser + GOST 住宅代理登录进后台！")
        
        # 成功登录后备份状态以便后续使用
        await context.storage_state(path="kerit_auth.json")
        print("已同步将会话状态保存至 kerit_auth.json")

    except Exception as e:
        print(f"❌ 自动化链条断裂: {str(e)}")
        sys.exit(1)
    finally:
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
