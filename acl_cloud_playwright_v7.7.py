#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACL Cloud 自动续期脚本（Playwright 完整版 v7.6）
更新（v7.6）：
  1. 【新增】支持配置代理服务器（PROXY_SERVER 环境变量）
  2. 【优化】针对 Renew 弹窗（Anti-bot confirmation）优化 OCR 识别逻辑，处理背景干涉线
  3. 【保持】其余逻辑（Xvfb、ffmpeg 录屏、网页交互、微信通知）完全一致
"""

import os
import sys
import time
import base64
import subprocess
import signal
import atexit
import requests
from datetime import datetime
from urllib.parse import urljoin, urlparse
from PIL import Image, ImageEnhance
from io import BytesIO
import pytesseract
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ========== 配置 ==========
USERNAME = os.getenv("ACL_USERNAME", "")
PASSWORD = os.getenv("ACL_PASSWORD", "")
LOGIN_URL = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
WECHAT_WEBHOOK_KEY = os.getenv("WECHAT_WEBHOOK_KEY", "")
SERVER_ID = os.getenv("ACL_SERVER_ID", "3727")   # 服务器 ID
PROXY_SERVER = os.getenv("PROXY_SERVER", "")     # 代理配置，如 "http://127.0.0.1:7890" 或 "http://user:pass@host:port"

MAX_RETRIES = 3
VIDEO_DIR = "videos"
RECORDING_FILE = "full_operation_recording.mp4"
DIAGNOSTIC_PREFIX = "diag"
DISPLAY_NUM = ":99"
SCREEN_SIZE = "1920x1080x24"
# ==========================

NEED_RENEWAL = False
RENEWAL_SUCCESS = False
SERVER_STATUS = "unknown"
POWER_ACTION = "none"
SERVER_UPTIME = ""

_xvfb_proc = None
_ffmpeg_proc = None


def download_image_bytes(page, src, label="图片"):
    """
    统一图片下载入口
    - 相对路径使用 urljoin(page.url, src) 拼接
    - 使用 page.context.request.get() 共享 Cookie/Session
    - 支持 data:image Base64 内联图片
    """
    if not src:
        print(f"     ⚠️ {label}: src 为空")
        return None

    try:
        # 1) data URI 直接解码
        if src.startswith("data:image"):
            try:
                _, b64data = src.split(",", 1)
                return base64.b64decode(b64data)
            except Exception as e:
                print(f"     ⚠️ {label}: data URI 解码失败: {e}")
                return None

        # 2) 拼接路径
        full_url = urljoin(page.url, src)

        # 3) 使用 context 请求，携带 Cookie
        resp = page.context.request.get(full_url, timeout=15000)
        if not resp.ok:
            print(f"     ❌ {label}: HTTP {resp.status} {full_url}")
            return None
        body = resp.body()
        if not body or len(body) < 100:
            print(f"     ⚠️ {label}: 响应体过小 ({len(body) if body else 0} bytes) {full_url}")
            return None
        return body
    except Exception as e:
        print(f"     ❌ {label}: 下载失败: {e}")
        return None


def preprocess_captcha_image(img_bytes):
    """优化 OCR 识别逻辑：针对带有干涉线条和噪点的验证码图片处理"""
    try:
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        # 1. 放大图片提升分辨率
        w, h = img.size
        img = img.resize((w * 3, h * 3), Image.Resampling.LANCZOS)
        # 2. 转灰度图
        gray = img.convert("L")
        # 3. 增加对比度
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(2.5)
        # 4. 二值化降噪处理
        threshold = 140
        binary = gray.point(lambda p: 255 if p > threshold else 0)
        return binary
    except Exception as e:
        print(f"        ⚠️ 图片预处理失败: {e}")
        return None


def _kill_proc(proc, name="process", timeout=5):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    print(f"⏹️  {name} 已终止")


def start_xvfb():
    global _xvfb_proc
    if os.getenv("DISPLAY"):
        print(f"ℹ️  已有 DISPLAY={os.getenv('DISPLAY')}")
        return
    print(f"🖥️  启动 Xvfb {DISPLAY_NUM} ({SCREEN_SIZE})...")
    cmd = ["Xvfb", DISPLAY_NUM, "-screen", "0", SCREEN_SIZE,
           "-ac", "+extension", "RANDR", "-noreset"]
    _xvfb_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ["DISPLAY"] = DISPLAY_NUM
    time.sleep(2)
    if _xvfb_proc.poll() is not None:
        raise RuntimeError("Xvfb 启动失败")
    print(f"✅ Xvfb 已启动")


def stop_xvfb():
    global _xvfb_proc
    _kill_proc(_xvfb_proc, "Xvfb")
    _xvfb_proc = None


def start_ffmpeg_recording():
    global _ffmpeg_proc
    print(f"🎥 启动 ffmpeg 录屏 → {RECORDING_FILE}")
    cmd = [
        "ffmpeg",
        "-f", "x11grab",
        "-video_size", "1920x1080",
        "-i", DISPLAY_NUM,
        "-r", "10",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "30",
        "-movflags", "+faststart",
        "-y",
        RECORDING_FILE
    ]
    _ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    if _ffmpeg_proc.poll() is not None:
        raise RuntimeError("ffmpeg 启动失败")
    print("✅ ffmpeg 录屏已启动")


def stop_ffmpeg_recording():
    global _ffmpeg_proc
    if _ffmpeg_proc is None:
        return
    print("⏹️  停止 ffmpeg 录屏...")
    try:
        _ffmpeg_proc.send_signal(signal.SIGTERM)
        _ffmpeg_proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        _kill_proc(_ffmpeg_proc, "ffmpeg", timeout=3)
    _ffmpeg_proc = None
    if os.path.exists(RECORDING_FILE):
        size_mb = os.path.getsize(RECORDING_FILE) / (1024 * 1024)
        print(f"✅ 录屏已保存: {RECORDING_FILE} ({size_mb:.1f} MB)")


def ensure_video_dir():
    if not os.path.exists(VIDEO_DIR):
        os.makedirs(VIDEO_DIR)
        print(f"📁 视频目录: {VIDEO_DIR}")


def diagnostic_screenshot(page, name):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{VIDEO_DIR}/{DIAGNOSTIC_PREFIX}_{ts}_{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
        print(f"📸 诊断截图: {path}")
    except Exception as e:
        print(f"⚠️ 截图失败: {e}")


def scroll_page_to_bottom(page, step=600, pause=0.6, max_steps=20):
    print("  📜 滚动页面加载全部内容...")
    try:
        for i in range(max_steps):
            page.mouse.wheel(0, step)
            time.sleep(pause)
            try:
                height = page.evaluate("document.body ? document.body.scrollHeight : 0")
                cur = page.evaluate("window.scrollY + window.innerHeight")
                if height and cur >= height - 10:
                    print(f"  ✅ 已滚动到底部 (step {i + 1})")
                    break
            except Exception:
                break
    except Exception as e:
        print(f"  ⚠️ 滚动异常（忽略）: {e}")
    time.sleep(1)


def wait_and_type(page, selectors, value, label="输入框", delay_ms=50):
    print(f"  🔍 查找 {label}...")
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=8000)
            print(f"     ✅ 找到 {label}: '{sel}'")
            loc.click(timeout=5000)
            time.sleep(0.2)
            loc.press("Control+a")
            loc.press("Delete")
            time.sleep(0.2)
            loc.type(value, delay=delay_ms, timeout=15000)
            print(f"     ✅ {label} 输入完成")
            return True
        except PlaywrightTimeout:
            print(f"     ⏱️ 选择器 '{sel}' 等待超时")
            continue
        except Exception as e:
            print(f"     ❌ 选择器 '{sel}': {str(e)[:80]}")
            continue
    print(f"  ⚠️ 常规输入失败，尝试 JavaScript 兜底...")
    for sel in selectors:
        try:
            page.wait_for_selector(sel, state="attached", timeout=5000)
            page.evaluate(f"""
                (() => {{
                    const el = document.querySelector('{sel}');
                    if (!el) return false;
                    el.value = '{value}';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('keyup', {{ bubbles: true }}));
                    return true;
                }})()
            """)
            print(f"     ✅ JS 兜底成功: '{sel}'")
            return True
        except Exception as e:
            print(f"     ❌ JS 兜底失败 '{sel}': {e}")
            continue
    return False


def wait_and_click(page, selectors, label="按钮", timeout=8000):
    print(f"  🔍 查找 {label}...")
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout)
            loc.scroll_into_view_if_needed()
            time.sleep(0.3)
            loc.click(timeout=5000)
            print(f"     ✅ 点击 {label}: '{sel}'")
            return True
        except Exception as e:
            print(f"     ❌ '{sel}': {str(e)[:80]}")
            continue
    return False


def safe_find_text(page, selectors, default=""):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=1000):
                return loc.inner_text().strip()
        except Exception:
            continue
    return default


def close_install_popup(page):
    try:
        selectors = [
            "//button[contains(text(), 'Fermer')]",
            "//button[contains(text(), 'Close')]",
            "//div[contains(@class, 'pwa-install')]//button[1]",
            "//div[contains(@class, 'install-popup')]//button[contains(@class, 'close')]"
        ]
        for sel in selectors:
            try:
                btns = page.locator(sel).all()
                for btn in btns:
                    if btn.is_visible():
                        btn.click()
                        print("✅ 已关闭安装弹窗")
                        time.sleep(0.5)
                        return True
            except Exception:
                continue
    except Exception as e:
        print(f"ℹ️ 无需关闭弹窗: {e}")
    return False


def wait_for_login_page(page, url):
    print(f"\n🌐 打开登录页: {url}")
    for attempt in range(3):
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
            print(f"  ✅ 页面 networkidle")
            time.sleep(3)
            selectors = [
                "input[name='email']", "input[type='email']",
                "input[placeholder*='email' i]", "input[name='username']",
                "input[id*='email' i]", "input[id='email']",
                "input[autocomplete='username']", "input[autocomplete='email']",
            ]
            for sel in selectors:
                try:
                    page.wait_for_selector(sel, state="visible", timeout=5000)
                    print(f"  ✅ 登录表单已就绪: '{sel}'")
                    return True
                except Exception:
                    continue
            print(f"  ⚠️ 第 {attempt + 1} 次：表单未就绪，重试...")
            diagnostic_screenshot(page, f"login_retry_{attempt + 1}")
            time.sleep(3)
        except Exception as e:
            print(f"  ❌ 第 {attempt + 1} 次加载失败: {e}")
            diagnostic_screenshot(page, f"load_error_{attempt + 1}")
            time.sleep(3)
    return False


def process_captcha(page, flow_name=""):
    """
    处理人机验证（Anti-bot confirmation 弹窗 OCR 识别并点击）
    """
    print(f"\n🔄 开始处理{flow_name}人机验证...")

    # 如果有复选框先尝试点击复选框
    try:
        checkbox = page.locator(
            "div.auth-captcha-checkbox, input[type='checkbox'] + label, .captcha-checkbox"
        ).first
        if checkbox.is_visible(timeout=3000):
            checkbox.hover()
            time.sleep(0.3)
            checkbox.click()
            time.sleep(1.5)
            print("  ✅ 复选框已点击")
    except Exception:
        pass

    # 1. 获取提示文字（如 Click on ACLClouds）
    strong_text = ""
    prompt_selectors = [
        "div.auth-captcha-prompt", ".captcha-prompt",
        "//p[contains(text(), 'Click on')]", "//div[contains(text(), 'Click on')]"
    ]
    for p_sel in prompt_selectors:
        try:
            prompt = page.locator(p_sel).first
            if prompt.is_visible(timeout=3000):
                if prompt.locator("strong").count() > 0:
                    strong_text = prompt.locator("strong").inner_text().strip()
                else:
                    text_content = prompt.inner_text().strip()
                    if "Click on" in text_content:
                        strong_text = text_content.split("Click on")[-1].strip()
                if strong_text:
                    break
        except Exception:
            continue

    if not strong_text:
        print("  ⚠️ 获取提示文字失败")
        return False

    print(f"  📝 验证码提示目标文字: {strong_text}")
    target = strong_text.lower().replace(" ", "").replace("-", "")

    # 2. 定位图片选项列表
    option_selectors = [
        "div.auth-captcha-options button", ".captcha-options .captcha-option",
        "button.auth-captcha-option", ".captcha-option",
        "//div[contains(@class, 'modal')]//img/ancestor::button",
        "//div[contains(@class, 'modal')]//img/parent::div",
        "img[src*='captcha']", "div:has(> img)"
    ]

    options = []
    for opt_sel in option_selectors:
        try:
            found = page.locator(opt_sel).all()
            visible_opts = [o for o in found if o.is_visible()]
            if len(visible_opts) >= 2:
                options = visible_opts
                break
        except Exception:
            continue

    if not options:
        print("  ⚠️ 未找到可点击的图片选项")
        return False

    print(f"  📍 共找到 {len(options)} 个候选选项")
    clicked = False

    # 3. OCR 识别选项图片
    for idx, opt in enumerate(options):
        try:
            # 尝试找 img 标签
            if opt.evaluate("node => node.tagName.toLowerCase()") == "img":
                img = opt
            else:
                img = opt.locator("img").first

            src = img.get_attribute("src") if img.is_visible() else ""
            img_bytes = download_image_bytes(page, src, label=f"选项 {idx + 1}")
            if img_bytes is None:
                continue

            # 处理图片 & OCR 识别
            processed_img = preprocess_captcha_image(img_bytes)
            if not processed_img:
                continue

            ocr_text = pytesseract.image_to_string(
                processed_img, lang='eng', config='--psm 7 --oem 3 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
            ).strip()
            
            ocr_clean = ocr_text.lower().replace(" ", "").replace("-", "")
            print(f"     📍 选项 {idx + 1} OCR: '{ocr_text}' → 清洗: '{ocr_clean}'")

            # 比对关键词
            if target in ocr_clean or ocr_clean in target:
                print(f"  ✅ 成功匹配选项 {idx + 1} (目标: {strong_text} | OCR: {ocr_text})")
                opt.scroll_into_view_if_needed()
                time.sleep(0.3)
                opt.click()
                clicked = True
                time.sleep(2)
                break
        except Exception as e:
            print(f"     ❌ 选项 {idx + 1} 识别/点击出错: {e}")

    if not clicked:
        print("  ❌ 未匹配到对应的文字选项")
        return False

    time.sleep(2)
    # 验证弹窗是否顺利关闭或成功
    try:
        verified = page.locator("span.auth-captcha-label, .captcha-label").first
        if verified.is_visible():
            vtext = verified.inner_text()
            if "Verified" in vtext or "Vérifié" in vtext:
                print(f"  ✅ 验证通过 (Verified: {vtext})")
                return True
    except Exception:
        pass

    return True


def check_login_success(page, timeout=20):
    print(f"  ⏳ 等待登录跳转/渲染（最多 {timeout} 秒）...")
    start_time = time.time()
    last_url = page.url

    while time.time() - start_time < timeout:
        current_url = page.url
        if "login" not in current_url.lower() and "/auth/" not in current_url.lower():
            print(f"  🎉 登录成功（URL）: {current_url}")
            return True

        try:
            dashboard_indicators = [
                "text=Bienvenue", "text=Dashboard", "text=Tableau de bord",
                "text=Accueil", "text=Mes services", "text=My services",
                "a[href='/dashboard']", "a[href='/logout']",
                "[class*='dashboard']", "nav"
            ]
            for sel in dashboard_indicators:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=500):
                        print(f"  🎉 登录成功（内容: '{sel}'）URL: {current_url}")
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        time.sleep(1)

    final_url = page.url
    if "login" not in final_url.lower() and "/auth/" not in final_url.lower():
        print(f"  🎉 登录成功（最终 URL）: {final_url}")
        return True

    return False


def switch_language_to_en(page):
    print("\n🌐 检查并切换语言为 English...")

    lang_button = None
    lang_selectors = [
        "button[aria-label='Langue']",
        "button[class*='LanguageButton']",
        "button[class*='language']",
        "//button[@aria-label='Langue']",
        "//button[contains(@class, 'LanguageButton')]",
        "//button[.//img[contains(@src, 'flags')]]",
        "//button[.//span[contains(@class, 'lang-code')]]",
    ]

    for sel in lang_selectors:
        try:
            loc = page.locator(f"xpath={sel}").first if sel.startswith("//") else page.locator(sel).first
            if loc.is_visible(timeout=3000):
                lang_button = loc
                break
        except Exception:
            continue

    if not lang_button:
        print("⚠️ 未找到语言切换按钮，跳过")
        return False

    current_lang = ""
    try:
        badge = lang_button.locator(".lang-code-badge").first
        current_lang = badge.inner_text().strip().upper()
    except Exception:
        pass

    if "EN" in current_lang:
        print("✅ 当前语言已是 English，无需切换")
        return True

    lang_button.scroll_into_view_if_needed()
    time.sleep(0.3)
    lang_button.click()
    time.sleep(1.5)

    en_option = None
    en_selectors = [
        "button:has-text('English'):has-text('EN')",
        "//button[contains(text(), 'English')]",
    ]

    for sel in en_selectors:
        try:
            loc = page.locator(f"xpath={sel}").first if sel.startswith("//") else page.locator(sel).first
            if loc.is_visible(timeout=3000):
                en_option = loc
                break
        except Exception:
            continue

    if en_option:
        en_option.click()
        print("✅ 已点击 English 语言选项")
        time.sleep(3)
        return True

    return False


def needs_renewal(status_text):
    if not status_text:
        return False
    status_lower = status_text.lower()
    keywords = [
        "suspended", "expired", "suspendu", "expire", "termine",
        "inactive", "inactif", "ended", "non actif", "renouvellement",
        "renewal", "renouveler", "renew"
    ]
    return any(kw in status_lower for kw in keywords)


def perform_renewal(page):
    global NEED_RENEWAL, RENEWAL_SUCCESS
    print("\n🔄 开始执行续期操作...")
    close_install_popup(page)
    scroll_page_to_bottom(page)

    renew_buttons = []
    for sel in [
        "button:has-text('Renew')",
        "button:has-text('Renouveler')",
        "//button[span[normalize-space()='Renew']]",
        "//button[normalize-space()='Renew']",
    ]:
        try:
            loc = page.locator(f"xpath={sel}") if sel.startswith("//") else page.locator(sel)
            btns = [b for b in loc.all() if b.is_visible()]
            if btns:
                renew_buttons = btns
                print(f"📋 找到 {len(btns)} 个 Renew 按钮 (selector: {sel})")
                break
        except Exception:
            continue

    if not renew_buttons:
        print("ℹ️ 页面上没有可见的 Renew 按钮，无需续期")
        return True

    for idx, btn in enumerate(renew_buttons):
        try:
            NEED_RENEWAL = True
            btn.scroll_into_view_if_needed()
            time.sleep(0.5)
            btn.click()
            print(f"  ✅ 已点击 Renew 按钮")
            time.sleep(2)

            if process_captcha(page, flow_name="renewal_popup"):
                RENEWAL_SUCCESS = True
                print("✅ 续期验证通过！")
            else:
                print("❌ 续期验证失败")
            time.sleep(2)
            close_install_popup(page)
        except Exception as e:
            print(f"  ❌ 处理 Renew 按钮 {idx + 1} 出错: {e}")
            continue

    return True


def navigate_to_services(page):
    print("\n📂 点击 My services / Mes services 导航...")
    nav_selectors = [
        "a[aria-label='My services']",
        "a[href='/dashboard/projects']",
        "//a[contains(@aria-label, 'My services')]",
        "text=Mes services",
        "text=My services"
    ]

    for sel in nav_selectors:
        try:
            loc = page.locator(f"xpath={sel}").first if sel.startswith("//") else page.locator(sel).first
            if loc.is_visible(timeout=3000):
                loc.click()
                time.sleep(3)
                print("✅ 已进入 My services 页面")
                return True
        except Exception:
            continue

    print("⚠️ 未找到导航按钮，直接访问 URL")
    page.goto("https://aclclouds.com/dashboard/projects", wait_until="networkidle")
    time.sleep(3)
    return True


def click_manage_button(page):
    print("\n🔧 点击 Manage / Gerer 按钮...")
    manage_selectors = [
        "a.client-btn--primary[href^='/server/']",
        "a[href^='/server/'].client-btn",
        "//a[contains(@href, '/server/')]",
        "a:has-text('Manage')",
        "a:has-text('Gerer')",
    ]

    for sel in manage_selectors:
        try:
            loc = page.locator(f"xpath={sel}").first if sel.startswith("//") else page.locator(sel).first
            if loc.is_visible(timeout=3000):
                loc.scroll_into_view_if_needed()
                time.sleep(0.5)
                loc.click()
                time.sleep(3)
                print("✅ 已进入服务器详情页")
                return True
        except Exception:
            continue

    fallback_url = f"https://aclclouds.com/server/{SERVER_ID}"
    print(f"⚠️ 未找到 Manage 按钮，直接访问服务器详情页: {fallback_url}")
    try:
        page.goto(fallback_url, wait_until="networkidle", timeout=45000)
        time.sleep(3)
        return True
    except Exception as e:
        print(f"❌ 直接访问详情页失败: {e}")
        return False


def find_label_value(page, labels):
    for label in labels:
        xpaths = [
            f"//*[contains(normalize-space(text()), '{label}')]/following-sibling::*[1]",
            f"//*[contains(normalize-space(text()), '{label}')]/parent::*//following-sibling::*[1]",
        ]
        for xp in xpaths:
            try:
                loc = page.locator(f"xpath={xp}").first
                if loc.is_visible(timeout=800):
                    value = loc.inner_text().strip()
                    if value and label.lower() not in value.lower():
                        return value
            except Exception:
                continue
    return ""


def get_server_info(page):
    global SERVER_STATUS, SERVER_UPTIME
    print("\n📊 获取服务器信息...")
    info = {
        "time_remaining": "",
        "plan": "",
        "renewal_note": "",
        "server_name": "",
        "server_url": page.url,
        "status": "unknown",
        "uptime": "",
    }

    try:
        info["server_name"] = page.locator("h1, .server-name, [class*='server-title']").first.inner_text(timeout=3000).strip()
    except Exception:
        info["server_name"] = "ACL Cloud Server"

    uptime_value = find_label_value(page, ["Uptime", "Temps de fonctionnement", "Running for"])
    if uptime_value:
        info["uptime"] = uptime_value
        SERVER_UPTIME = uptime_value
        info["status"] = SERVER_STATUS = "online"

    info["time_remaining"] = find_label_value(page, ["Time remaining", "Temps restant"])
    info["plan"] = find_label_value(page, ["Plan", "Forfait"])

    diagnostic_screenshot(page, "server_info")
    return info


def manage_server_power(page):
    global POWER_ACTION, SERVER_UPTIME
    print("\n⚡ 检测服务器电源状态并执行操作...")

    if SERVER_UPTIME:
        print(f"🟢 服务器 Online（运行时间: {SERVER_UPTIME}），不执行重启")
        POWER_ACTION = "none"
        return True

    print("🔴 未检测到运行时间，服务器可能 Offline，执行 Start...")
    if wait_and_click(page, [
        "button:has-text('Start')",
        "button:has-text('Démarrer')",
        "//button[contains(translate(., 'START', 'start'), 'start')]",
    ], label="Start 按钮"):
        POWER_ACTION = "start"
        print("✅ Start 已点击")
        return True

    return False


def send_wechat_notification(info, need_renewal, renewal_success, power_action):
    if not WECHAT_WEBHOOK_KEY:
        print("⚠️ 未设置 WECHAT_WEBHOOK_KEY，跳过通知")
        return False

    webhook_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECHAT_WEBHOOK_KEY}"
    server_name = info.get("server_name", "ACL Cloud Server")
    time_remaining = info.get("time_remaining", "未知")
    plan = info.get("plan", "未知")
    status = info.get("status", "unknown")
    uptime = info.get("uptime", SERVER_UPTIME or "未知")

    content = f"""ACL Cloud 服务器状态报告

📌 服务器: {server_name}
⏱️ 运行时间: {uptime}
⏰ 剩余时间: {time_remaining}
📋 套餐信息: {plan}

📊 续期状态: {'✅ 成功' if renewal_success else ('❌ 失败' if need_renewal else '✅ 无需续期')}
⚡ 电源动作: {power_action}

⏱️ 报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    try:
        requests.post(webhook_url, json={"msgtype": "text", "text": {"content": content}}, timeout=10)
        print("✅ 企业微信通知发送成功")
        return True
    except Exception as e:
        print(f"❌ 发送通知异常: {e}")
        return False


def main():
    global NEED_RENEWAL, RENEWAL_SUCCESS, SERVER_STATUS, POWER_ACTION, SERVER_UPTIME

    if not USERNAME or not PASSWORD:
        print("❌ 错误: 未设置 ACL_USERNAME 或 ACL_PASSWORD")
        sys.exit(1)

    ensure_video_dir()
    server_info = {}
    page = None
    context = None
    browser = None

    atexit.register(stop_ffmpeg_recording)
    atexit.register(stop_xvfb)

    start_xvfb()
    start_ffmpeg_recording()

    with sync_playwright() as p:
        try:
            print("🚀 启动 Chromium 浏览器...")
            
            # 配置代理（如果环境变量 PROXY_SERVER 已设置）
            launch_args = {
                "headless": False,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1080",
                    "--start-maximized",
                ]
            }

            if PROXY_SERVER:
                print(f"🌐 正在使用代理服务器: {PROXY_SERVER}")
                parsed_proxy = urlparse(PROXY_SERVER)
                proxy_config = {"server": f"{parsed_proxy.scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}"}
                if parsed_proxy.username and parsed_proxy.password:
                    proxy_config["username"] = parsed_proxy.username
                    proxy_config["password"] = parsed_proxy.password
                launch_args["proxy"] = proxy_config

            browser = p.chromium.launch(**launch_args)

            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            if not wait_for_login_page(page, LOGIN_URL):
                print("❌ 登录页加载失败")
                return False

            wait_and_type(page, ["input[name='email']", "input[type='email']"], USERNAME, label="邮箱输入框")
            wait_and_type(page, ["input[name='password']", "input[type='password']"], PASSWORD, label="密码输入框")

            login_ok = False
            for attempt in range(MAX_RETRIES):
                print(f"\n🔄 验证码尝试 {attempt + 1}/{MAX_RETRIES}")
                if process_captcha(page, flow_name="login"):
                    if wait_and_click(page, ["button:has-text('Sign in')", "button[type='submit']"], label="Sign in 按钮"):
                        if check_login_success(page, timeout=20):
                            login_ok = True
                            break
                time.sleep(2)

            if not login_ok:
                print("❌ 登录失败")
                return False

            base_url = LOGIN_URL.rstrip("/auth/login").rstrip("/")
            if "dashboard" not in page.url.lower():
                page.goto(base_url + "/dashboard", wait_until="networkidle")

            close_install_popup(page)
            switch_language_to_en(page)

            # 检测与执行续期
            scroll_page_to_bottom(page)
            renew_btns = page.locator("button:has-text('Renew'), button:has-text('Renouveler')").all()
            visible_renew = [b for b in renew_btns if b.is_visible()]

            if visible_renew:
                print("\n📌 执行续期流程...")
                perform_renewal(page)

            navigate_to_services(page)
            click_manage_button(page)
            server_info = get_server_info(page)
            manage_server_power(page)

            send_wechat_notification(server_info, NEED_RENEWAL, RENEWAL_SUCCESS, POWER_ACTION)

            print("\n🎉 所有操作已完成！")
            return True

        except Exception as e:
            print(f"\n❌ 发生错误: {e}")
            return False

        finally:
            try:
                if context:
                    context.close()
                if browser:
                    browser.close()
            except Exception:
                pass

            stop_ffmpeg_recording()
            stop_xvfb()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
