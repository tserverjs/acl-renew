#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACL Cloud 自动续期脚本（Playwright 完整版 v7.5 - 代理与 Renew 弹窗增强版）
更新：
  1. 【新增】支持 PROXY_SERVER 环境变量配置代理服务器
  2. 【优化】针对 Renew 按钮弹窗（Anti-bot confirmation）优化图片降噪与 OCR 识别点击逻辑
  3. 【保持】其余登录、语言切换、服务器控制、视频录制等步骤完全保持原样不变
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
SERVER_ID = os.getenv("ACL_SERVER_ID", "3727")   # 服务器 ID，Manage 按钮找不到时直接访问详情页
PROXY_SERVER = os.getenv("PROXY_SERVER", "")     # 代理服务器地址（例如 "http://127.0.0.1:7890" 或 "http://user:pass@ip:port"）
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
    统一图片下载入口（v7.4 修复版）。
    - 相对路径使用 urljoin(page.url, src) 拼接（不再手动裁剪 base_url）
    - 使用 page.context.request.get() 共享浏览器 Cookie/Session
    - 支持 data:image Base64 内联图片
    返回: bytes 或 None
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

        # 2) 相对/绝对路径统一拼接（关键修复：不再依赖 rstrip("/auth/login")）
        full_url = urljoin(page.url, src)

        # 3) 使用浏览器上下文请求，自动携带 Cookie
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
    """v7.5 新增：缓慢滚动到页面底部，触发懒加载，确保首页下方 Renew 区域渲染出来"""
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
    """【修改】处理人机验证：针对 Renew 后的 Anti-bot 确认弹窗增强 OCR 识别与准确点击"""
    print(f"\n🔄 开始处理{flow_name}人机验证...")

    # 如果有勾选框优先点击
    try:
        checkbox = page.locator(
            "div.auth-captcha-checkbox, input[type='checkbox'] + label, .captcha-checkbox"
        ).first
        if checkbox.is_visible(timeout=2000):
            checkbox.hover()
            time.sleep(0.3)
            checkbox.click()
            time.sleep(1.5)
            print("  ✅ 复选框已点击")
    except Exception as e:
        print(f"  ℹ️ 无可用复选框或点击跳过: {e}")

    # 获取提示文本（支持 Click on XXX 或 strong 标签文本）
    strong_text = ""
    try:
        prompt = page.locator("div.auth-captcha-prompt, .captcha-prompt, p:has-text('Click on')").first
        prompt.wait_for(state="visible", timeout=5000)
        
        strong_loc = prompt.locator("strong")
        if strong_loc.count() > 0:
            strong_text = strong_loc.first.inner_text().strip()
        else:
            full_txt = prompt.inner_text().strip()
            if "Click on" in full_txt:
                strong_text = full_txt.split("Click on")[-1].strip()
            else:
                strong_text = full_txt

        print(f"  📝 验证码目标提示文字: '{strong_text}'")
    except Exception as e:
        print(f"  ⚠️ 获取提示文字失败: {e}")
        return False

    # 寻找选项按钮及图片
    try:
        options = page.locator(
            "div.auth-captcha-options button, .captcha-options .captcha-option, "
            "button.auth-captcha-option, .captcha-option, div:has(> img[src*='data:image'])"
        ).all()
        options = [b for b in options if b.is_visible()]
        if not options:
            print("  ⚠️ 未找到可点击选项")
            return False
        print(f"  📍 共 {len(options)} 个待识别图片选项")
    except Exception as e:
        print(f"  ⚠️ 获取选项失败: {e}")
        return False

    target = strong_text.lower().replace(" ", "").replace("-", "")
    clicked = False

    for idx, btn in enumerate(options):
        try:
            # 获取选项内的图片
            img = btn.locator("img").first if btn.evaluate("e => e.tagName") != "IMG" else btn
            src = img.get_attribute("src") if img.is_visible() else ""

            img_bytes = download_image_bytes(page, src, label=f"选项 {idx + 1}")
            if img_bytes is None:
                continue

            # 增强型图像预处理（放大 + 增对比度 + 降噪二值化，有效克服线段干扰）
            img_obj = Image.open(BytesIO(img_bytes)).convert("RGB")
            w, h = img_obj.size
            img_obj = img_obj.resize((w * 3, h * 3), Image.Resampling.LANCZOS)
            gray = img_obj.convert("L")
            gray = ImageEnhance.Contrast(gray).enhance(2.5)
            binary = gray.point(lambda x: 255 if x > 140 else 0)

            # OCR 字符提取（仅保留英文字母）
            ocr_text = pytesseract.image_to_string(
                binary, 
                lang='eng', 
                config='--psm 7 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
            ).strip()
            
            ocr_clean = ocr_text.lower().replace(" ", "").replace("-", "")
            print(f"     📍 选项 {idx + 1}: OCR 结果 '{ocr_text}' → 规范后 '{ocr_clean}'")

            if target in ocr_clean or ocr_clean in target:
                print(f"  ✅ 匹配到目标选项 {idx + 1} (识别文字: {ocr_text})")
                btn.scroll_into_view_if_needed()
                time.sleep(0.3)
                btn.click()
                clicked = True
                time.sleep(2)
                break
        except Exception as e:
            print(f"     ❌ 选项 {idx + 1} OCR 处理异常: {e}")

    if not clicked:
        print("  ❌ 未找到与目标匹配的选项")
        return False

    time.sleep(2)

    # 结果判定
    try:
        verified = page.locator("span.auth-captcha-label, .captcha-label").first
        if verified.is_visible():
            vtext = verified.inner_text()
            if "Verified" in vtext or "Vérifié" in vtext:
                print(f"  ✅ 验证通过 (Verified: {vtext})")
                return True
    except Exception:
        pass

    try:
        captcha_selectors = ["div.auth-captcha-options", ".captcha-options", "div.auth-captcha-prompt"]
        if not any(page.locator(sel).first.is_visible() for sel in captcha_selectors if page.locator(sel).count() > 0):
            print("  ✅ 验证通过（弹窗已关闭）")
            return True
    except Exception:
        pass

    print("  ⚠️ 无法百分百确认状态，默认继续进行")
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

        try:
            email_input = page.locator("input[name='email'], input[name='username'], input[type='email']").first
            pwd_input = page.locator("input[type='password']").first
            form_gone = False
            try:
                form_gone = not email_input.is_visible(timeout=500) or not pwd_input.is_visible(timeout=500)
            except Exception:
                form_gone = True

            if form_gone:
                body_text = page.locator("body").inner_text()
                dashboard_keywords = ["Bienvenue", "Dashboard", "Tableau de bord",
                                      "Mes services", "My services", "Accueil", "Commander",
                                      "Suivi des depenses", "Vos prochains renouvellements"]
                if any(kw in body_text for kw in dashboard_keywords):
                    print(f"  🎉 登录成功（表单消失 + Dashboard 内容）")
                    return True
        except Exception:
            pass

        if current_url != last_url:
            print(f"  🔄 URL 变化中: {current_url}")
            last_url = current_url
        time.sleep(1)

    final_url = page.url
    if "login" not in final_url.lower() and "/auth/" not in final_url.lower():
        print(f"  🎉 登录成功（最终 URL）: {final_url}")
        return True
    try:
        body_text = page.locator("body").inner_text()
        if any(kw in body_text for kw in ["Bienvenue", "Dashboard", "Tableau de bord", "Mes services", "My services"]):
            print(f"  🎉 登录成功（最终内容确认）")
            return True
    except Exception:
        pass

    print(f"  ⚠️ 登录检测超时，最终 URL: {final_url}")
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
            if sel.startswith("//"):
                loc = page.locator(f"xpath={sel}").first
            else:
                loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=5000)
            if loc.is_visible():
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
        try:
            flag_img = lang_button.locator("img").first
            alt_text = flag_img.get_attribute("alt") or ""
            if "english" in alt_text.lower():
                current_lang = "EN"
            elif "francais" in alt_text.lower() or "français" in alt_text.lower():
                current_lang = "FR"
        except Exception:
            pass

    if "EN" in current_lang:
        print("✅ 当前语言已是 English，无需切换")
        return True

    print(f"📝 当前语言: {current_lang or '未知'}，准备切换为 English...")

    lang_button.scroll_into_view_if_needed()
    time.sleep(0.3)
    lang_button.click()
    print("✅ 已点击语言按钮，等待弹窗出现...")
    time.sleep(1.5)

    en_option = None

    en_selectors = [
        "//button[.//span[text()='EN'] and .//span[contains(text(), 'English')]]",
        "//div[contains(@class, 'modal')]//button[.//span[text()='EN'] and .//span[contains(text(), 'English')]]",
        "//div[contains(@class, 'dialog')]//button[.//span[text()='EN'] and .//span[contains(text(), 'English')]]",
        "//div[contains(@class, 'popup')]//button[.//span[text()='EN'] and .//span[contains(text(), 'English')]]",
        "//button[.//img[contains(@alt, 'English') or contains(@src, 'en')]]",
        "//div[contains(@class, 'modal')]//button[.//img[contains(@alt, 'English') or contains(@src, 'en')]]",
        "button:has-text('English'):has-text('EN')",
        "//button[contains(text(), 'English')]",
        "div[role='dialog'] button",
        "div[role='modal'] button",
        ".language-modal button",
        ".language-dialog button",
        ".lang-modal button",
    ]

    for sel in en_selectors:
        try:
            if sel.startswith("//"):
                loc = page.locator(f"xpath={sel}").first
            else:
                loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=3000)
            if loc.is_visible():
                text = loc.inner_text().lower()
                if "en" in text and "english" in text:
                    en_option = loc
                    print(f"✅ 直接定位到 English 选项: '{sel}'")
                    break
        except Exception:
            continue

    if not en_option:
        print("⚠️ 直接定位失败，尝试遍历弹窗内选项并用 OCR 识别...")

        modal_selectors = [
            "div[role='dialog']",
            "div[role='modal']",
            ".language-modal",
            ".language-dialog",
            ".lang-modal",
            "div[class*='modal']:visible",
            "div[class*='dialog']:visible",
            "div[class*='popup']:visible",
        ]

        modal = None
        for sel in modal_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    modal = loc
                    print(f"✅ 找到弹窗容器: '{sel}'")
                    break
            except Exception:
                continue

        if modal:
            try:
                buttons = modal.locator("button").all()
                print(f"📍 弹窗内共 {len(buttons)} 个按钮选项")

                for idx, btn in enumerate(buttons):
                    try:
                        if not btn.is_visible():
                            continue

                        btn_text = btn.inner_text().strip()
                        btn_html = btn.inner_html().lower()
                        print(f"     选项 {idx + 1}: '{btn_text}'")

                        text_lower = btn_text.lower()
                        if ("en" in text_lower and "english" in text_lower) or \
                           (btn_text.strip() == "EN" and "english" in btn_html):
                            en_option = btn
                            print(f"  ✅ 文本匹配到 English 选项 {idx + 1}")
                            break

                        try:
                            img = btn.locator("img").first
                            if img.is_visible(timeout=500):
                                src = img.get_attribute("src") or ""
                                if "en" in src.lower() or "english" in src.lower():
                                    en_option = btn
                                    print(f"  ✅ 图片 src 匹配到 English 选项 {idx + 1}: {src}")
                                    break

                                img_bytes = download_image_bytes(page, src, label=f"国旗选项 {idx + 1}")
                                if img_bytes:
                                    img_obj = Image.open(BytesIO(img_bytes)).convert("L")
                                    img_obj = img_obj.point(lambda x: 255 if x > 128 else 0)
                                    ocr_text = pytesseract.image_to_string(
                                        img_obj, lang='eng', config='--psm 7'
                                    ).strip().lower()
                                    print(f"        OCR 结果: '{ocr_text}'")
                                    if "en" in ocr_text or "english" in ocr_text or "british" in ocr_text:
                                        en_option = btn
                                        print(f"  ✅ OCR 匹配到 English 选项 {idx + 1}")
                                        break
                        except Exception:
                            pass

                    except Exception as e:
                        print(f"     ❌ 选项 {idx + 1} 处理失败: {e}")
                        continue

            except Exception as e:
                print(f"❌ 遍历弹窗选项失败: {e}")

    if not en_option:
        print("⚠️ 弹窗内未找到，尝试全局搜索...")
        try:
            all_buttons = page.locator("button").all()
            for btn in all_buttons:
                try:
                    if not btn.is_visible():
                        continue
                    text = btn.inner_text().lower()
                    if "english" in text and "en" in text:
                        en_option = btn
                        print("✅ 全局搜索找到 English 选项")
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if not en_option:
        print("❌ 无法找到 English 选项，继续执行")
        diagnostic_screenshot(page, "lang_switch_en_not_found")
        return False

    en_option.scroll_into_view_if_needed()
    time.sleep(0.3)
    en_option.click()
    print("✅ 已点击 English 语言选项")
    time.sleep(3)

    try:
        for _ in range(5):
            if en_option.is_visible(timeout=500):
                time.sleep(0.5)
            else:
                break
    except Exception:
        pass

    try:
        badge = page.locator("button[class*='LanguageButton'] .lang-code-badge").first
        if badge.is_visible(timeout=3000):
            if "EN" in badge.inner_text().strip().upper():
                print("✅ 语言切换验证通过：EN")
                return True
    except Exception:
        pass

    try:
        body_text = page.locator("body").inner_text()
        if "My services" in body_text or "Dashboard" in body_text:
            print("✅ 语言切换验证通过（页面内容已英文）")
            return True
    except Exception:
        pass

    print("⚠️ 无法验证语言切换结果，继续执行")
    return True


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
    """
    v7.5 重写：
      - 先 scroll_page_to_bottom() 触发懒加载，让首页下方续期区域渲染出来
      - Renew 按钮不再依赖 CSS Module 类名，按文字定位（Renew / Renouveler）
      - 向上回溯行容器读取状态文本，仅当状态提示需要续期时才点击
    """
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
            loc = page.locator(f"xpath={sel}").first if sel.startswith("//") else page.locator(sel)
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
            row_text = ""
            try:
                row = btn.locator(
                    "xpath=ancestor::*[self::tr or contains(@class,'row') or contains(@class,'item') "
                    "or contains(@class,'card') or contains(@class,'renewal')][1]"
                ).first
                if row.is_visible(timeout=1000):
                    row_text = row.inner_text().strip()
            except Exception:
                row_text = ""

            short = row_text[:120].replace("\n", " | ") if row_text else "(无法读取行信息)"
            print(f"\n📦 项目 {idx + 1}: {short}")

            if row_text and not needs_renewal(row_text):
                lower = row_text.lower()
                if any(kw in lower for kw in ["online", "active", "en ligne", "actif", "running"]):
                    print(f"  ✅ 状态正常，无需续期")
                    continue

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
        "//span[contains(text(), 'My services')]/parent::a",
        "//span[contains(text(), 'Mes services')]/parent::a",
        "text=Mes services",
        "text=My services"
    ]

    nav_link = None
    for sel in nav_selectors:
        try:
            if sel.startswith("//"):
                loc = page.locator(f"xpath={sel}").first
            else:
                loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=5000)
            if loc.is_visible():
                nav_link = loc
                break
        except Exception:
            continue

    if not nav_link:
        print("⚠️ 未找到导航按钮，直接访问 URL")
        page.goto("https://aclclouds.com/dashboard/projects", wait_until="networkidle")
        time.sleep(3)
        return True

    nav_link.click()
    time.sleep(3)
    print("✅ 已进入 My services 页面")
    return True


def click_manage_button(page):
    print("\n🔧 点击 Manage / Gerer 按钮...")
    manage_selectors = [
        "a.client-btn--primary[href^='/server/']",
        "a[href^='/server/'].client-btn",
        "//a[contains(@href, '/server/') and contains(@class, 'client-btn--primary')]",
        "//a[contains(text(), 'Manage')]",
        "//a[contains(text(), 'Gerer')]",
        ".client-btn--primary",
        "a:has-text('Manage')",
        "a:has-text('Gerer')",
        "//a[contains(@href, '/server/')]",
    ]

    manage_btn = None
    for sel in manage_selectors:
        try:
            if sel.startswith("//"):
                loc = page.locator(f"xpath={sel}").first
            else:
                loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=5000)
            if loc.is_visible():
                manage_btn = loc
                break
        except Exception:
            continue

    if not manage_btn:
        fallback_url = f"https://aclclouds.com/server/{SERVER_ID}"
        print(f"⚠️ 未找到 Manage 按钮，直接访问服务器详情页: {fallback_url}")
        try:
            page.goto(fallback_url, wait_until="networkidle", timeout=45000)
            time.sleep(3)
            print("✅ 已进入服务器详情页（直接 URL）")
            return True
        except Exception as e:
            print(f"❌ 直接访问详情页失败: {e}")
            return False

    manage_btn.scroll_into_view_if_needed()
    time.sleep(0.5)
    manage_btn.click()
    time.sleep(3)
    print("✅ 已进入服务器详情页")
    return True


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

    uptime_value = ""
    try:
        stat_items = page.locator("div.stat-item").all()
        for item in stat_items:
            try:
                label = item.locator(".stat-label").first.inner_text(timeout=1000).strip()
                if "Status" in label or "Uptime" in label or "status" in label.lower():
                    uptime_value = item.locator(".stat-value").first.inner_text(timeout=1000).strip()
                    break
            except Exception:
                continue
    except Exception:
        pass

    if not uptime_value:
        uptime_value = find_label_value(page, ["Uptime", "Temps de fonctionnement", "Running for"])

    if uptime_value:
        info["uptime"] = uptime_value
        SERVER_UPTIME = uptime_value
        print(f"  ⏱️ Uptime: {uptime_value}")
        if any(c in uptime_value for c in ["h", "m", "s", "d", "jour", "heure", "min"]):
            info["status"] = SERVER_STATUS = "online"
            print(f"  🟢 检测到运行时间，判断为 Online")
    else:
        print("  ⚠️ 未获取到 Uptime")

    if info["status"] == "unknown":
        try:
            badge = page.locator("span.status-badge[data-status], .status-badge, [class*='status-badge']").first
            if badge.is_visible(timeout=2000):
                ds = (badge.get_attribute("data-status") or "").lower()
                txt = badge.inner_text().strip().lower()
                raw = ds or txt
                if any(w in raw for w in ["online", "en ligne", "actif", "running"]):
                    info["status"] = SERVER_STATUS = "online"
                    print(f"  🟢 Online (badge: {txt})")
                elif any(w in raw for w in ["offline", "hors ligne", "inactif", "stopped"]):
                    info["status"] = SERVER_STATUS = "offline"
                    print(f"  🔴 Offline (badge: {txt})")
        except Exception:
            pass

    found_container = False
    try:
        info_selectors = [
            "div[style*='background: rgba(49, 95, 79']",
            ".server-info-card",
            "[class*='server-info']",
        ]
        info_container = None
        for sel in info_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    info_container = loc
                    found_container = True
                    print(f"  ✅ 找到信息容器: '{sel}'")
                    break
            except Exception:
                continue

        if info_container:
            text = info_container.inner_text()
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            for line in lines:
                if "Time remaining" in line or "Temps restant" in line:
                    info["time_remaining"] = line.split(":", 1)[-1].strip() if ":" in line else line
                elif any(w in line.lower() for w in ["plan", "gratuit", "free"]):
                    info["plan"] = line
                elif any(w in line.lower() for w in ["renewal", "renouvellement", "renew"]):
                    info["renewal_note"] = line
    except Exception as e:
        print(f"  ⚠️ 容器解析失败: {e}")

    if not info["time_remaining"]:
        info["time_remaining"] = find_label_value(page, ["Time remaining", "Temps restant"])
    if not info["plan"]:
        plan_text = find_label_value(page, ["Plan", "Forfait"])
        if plan_text:
            info["plan"] = plan_text

    print(f"  ⏰ Time remaining: {info['time_remaining'] or '未获取'}")
    print(f"  📋 Plan: {info['plan'] or '未获取'}")
    print(f"  📝 Renewal: {info['renewal_note'] or '未获取'}")

    diagnostic_screenshot(page, "server_info")
    return info


def manage_server_power(page):
    global POWER_ACTION, SERVER_UPTIME
    print("\n⚡ 检测服务器电源状态并执行操作...")

    has_uptime = False
    uptime_value = SERVER_UPTIME

    if uptime_value and any(c in uptime_value for c in ["h", "m", "s", "d", "jour", "heure", "min"]):
        has_uptime = True
        print(f"  📊 使用已获取的运行时间: {uptime_value} → Online")
    else:
        uptime_value = find_label_value(page, ["Uptime", "Temps de fonctionnement", "Running for"])
        if uptime_value:
            SERVER_UPTIME = uptime_value
            if any(c in uptime_value for c in ["h", "m", "s", "d", "jour", "heure", "min"]):
                has_uptime = True
                print(f"  📊 检测到运行时间: {uptime_value} → Online")

    if not has_uptime:
        print("🔴 未检测到运行时间，服务器可能 Offline，执行 Start...")
        if wait_and_click(page, [
            "button.power-btn[data-variant='start']",
            "button[data-variant='start']",
            "//button[contains(@class, 'power-btn') and contains(., 'Start')]",
            "//button[contains(@class, 'power-btn') and contains(., 'Demarrer')]",
            "button:has-text('Start')",
            "button:has-text('Démarrer')",
            "button:has-text('Demarrer')",
            "//button[contains(translate(., 'START', 'start'), 'start')]",
        ], label="Start 按钮"):
            POWER_ACTION = "start"
            print("✅ Start 已点击")
            return True
        print("❌ Start 按钮未找到或被禁用")
        return False
    else:
        print(f"🟢 服务器 Online（运行时间: {uptime_value}），不执行重启")
        POWER_ACTION = "none"
        return True


def send_wechat_notification(info, need_renewal, renewal_success, power_action):
    if not WECHAT_WEBHOOK_KEY:
        print("⚠️ 未设置 WECHAT_WEBHOOK_KEY，跳过通知")
        return False

    webhook_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECHAT_WEBHOOK_KEY}"
    server_name = info.get("server_name", "ACL Cloud Server")
    time_remaining = info.get("time_remaining", "未知")
    plan = info.get("plan", "未知")
    renewal_note = info.get("renewal_note", "")
    server_url = info.get("server_url", "")
    status = info.get("status", "unknown")
    uptime = info.get("uptime", SERVER_UPTIME or "未知")

    if uptime and uptime != "未知" and any(c in uptime for c in ["h", "m", "s", "d", "jour", "heure", "min"]):
        status = "online"

    if need_renewal and renewal_success:
        status_emoji = "✅"
        status_text = "续期成功"
        action_text = "已执行续期"
        color = "🟢"
    elif need_renewal and not renewal_success:
        status_emoji = "❌"
        status_text = "续期失败"
        action_text = "续期验证失败，请手动处理"
        color = "🔴"
    else:
        status_emoji = "✅"
        status_text = "状态正常"
        action_text = "无需续期"
        color = "🟢"

    if power_action == "start":
        power_text = "🚀 已执行 Start（服务器离线 → 启动）"
    elif power_action == "restart":
        power_text = "🔄 已执行 Restart"
    else:
        power_text = "➖ 未执行电源操作"

    sem = "🟢 在线" if status == "online" else "🔴 离线" if status == "offline" else "⚪ 未知"

    content = f"""{color} ACL Cloud 服务器状态报告 {color}

📌 服务器: {server_name}
{sem}
⏱️ 运行时间: {uptime}
⏰ 剩余时间: {time_remaining}
📋 套餐信息: {plan}
📝 续期提示: {renewal_note or '无'}

📊 续期状态: {status_emoji} {status_text}
🔧 执行动作: {action_text}
⚡ 电源状态: {power_text}

🔗 服务器详情页: {server_url}

⏱️ 报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    payload = {
        "msgtype": "text",
        "text": {
            "content": content,
            "mentioned_list": ["@all"]
        }
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        result = response.json()
        if result.get("errcode") == 0:
            print("✅ 企业微信通知发送成功")
            return True
        else:
            print(f"❌ 企业微信通知发送失败: {result}")
            return False
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
            print("🚀 启动 Chromium（非 headless，显示真实浏览器 UI）...")
            
            # 【修改】构建浏览器启动参数，支持代理配置
            launch_options = {
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
                print(f"🌐 正在配置代理服务器: {PROXY_SERVER}")
                parsed_proxy = urlparse(PROXY_SERVER)
                proxy_cfg = {"server": f"{parsed_proxy.scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}"}
                if parsed_proxy.username and parsed_proxy.password:
                    proxy_cfg["username"] = parsed_proxy.username
                    proxy_cfg["password"] = parsed_proxy.password
                launch_options["proxy"] = proxy_cfg

            browser = p.chromium.launch(**launch_options)

            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()
            print("✅ Chromium 已启动，ffmpeg 录制中...")

            if not wait_for_login_page(page, LOGIN_URL):
                print("❌ 登录页加载失败")
                diagnostic_screenshot(page, "login_page_failed")
                send_wechat_notification({"server_name": "登录页加载失败"}, False, False, "none")
                return False

            print("\n🔑 输入凭据...")
            email_ok = wait_and_type(page, [
                "input[name='email']", "input[type='email']",
                "input[placeholder*='email' i]", "input[name='username']",
                "input[id*='email' i]", "input[id='email']",
                "input[autocomplete='username']", "input[autocomplete='email']",
            ], USERNAME, label="邮箱输入框")

            if not email_ok:
                print("❌ 无法输入邮箱")
                diagnostic_screenshot(page, "email_input_failed")
                send_wechat_notification({"server_name": "邮箱输入失败"}, False, False, "none")
                return False

            time.sleep(0.5)

            pwd_ok = wait_and_type(page, [
                "input[name='password']", "input[type='password']",
                "input[id='password']", "input[autocomplete='current-password']",
            ], PASSWORD, label="密码输入框")

            if not pwd_ok:
                print("❌ 无法输入密码")
                diagnostic_screenshot(page, "password_input_failed")
                send_wechat_notification({"server_name": "密码输入失败"}, False, False, "none")
                return False

            print("✅ 凭据已输入")
            time.sleep(1)

            login_ok = False
            for attempt in range(MAX_RETRIES):
                print(f"\n🔄 验证码尝试 {attempt + 1}/{MAX_RETRIES}")
                if process_captcha(page, flow_name="login"):
                    if wait_and_click(page, [
                        "button:has-text('Sign in')",
                        "button[type='submit']",
                        "input[type='submit']",
                        "button:has-text('Connexion')",
                    ], label="Sign in 按钮"):
                        if check_login_success(page, timeout=20):
                            login_ok = True
                            break
                        else:
                            print("  ⚠️ 仍在登录页，准备重试...")
                time.sleep(2)

            if not login_ok:
                print("❌ 登录失败")
                diagnostic_screenshot(page, "login_failed")
                send_wechat_notification({"server_name": "登录失败"}, False, False, "none")
                return False

            print("\n✅ 登录成功，开始后续操作...")
            base_url = LOGIN_URL.rstrip("/auth/login").rstrip("/")

            if "dashboard" not in page.url.lower():
                print("📂 导航到 Dashboard...")
                page.goto(base_url + "/dashboard", wait_until="networkidle")
                time.sleep(3)

            close_install_popup(page)

            switch_language_to_en(page)

            if "dashboard" not in page.url.lower():
                page.goto(base_url + "/dashboard", wait_until="networkidle")
                time.sleep(3)

            close_install_popup(page)

            print("\n🔍 检查是否需要续期...")
            has_renewal = False
            try:
                scroll_page_to_bottom(page)
                renew_btns = page.locator(
                    "button:has-text('Renew'), button:has-text('Renouveler')"
                ).all()
                visible_renew = [b for b in renew_btns if b.is_visible()]
                if len(visible_renew) > 0:
                    has_renewal = True
                    print(f"✅ 检测到 {len(visible_renew)} 个需要续期的项目")
                else:
                    print("ℹ️ 未检测到需要续期的项目")
            except Exception as e:
                print(f"ℹ️ 续期检测异常: {e}")

            if has_renewal:
                print("\n📌 执行续期流程...")
                perform_renewal(page)

                navigate_to_services(page)
                click_manage_button(page)

                server_info = get_server_info(page)

                manage_server_power(page)

            else:
                print("\n📌 无需续期，直接获取服务器信息...")
                navigate_to_services(page)
                click_manage_button(page)
                server_info = get_server_info(page)
                manage_server_power(page)

            send_wechat_notification(server_info, NEED_RENEWAL, RENEWAL_SUCCESS, POWER_ACTION)

            print("\n🎉 所有操作已完成！")
            return True

        except Exception as e:
            print(f"\n❌ 发生错误: {e}")
            if page:
                diagnostic_screenshot(page, "fatal_error")
            send_wechat_notification({"server_name": f"脚本异常: {str(e)[:50]}"}, False, False, "none")
            return False

        finally:
            print("\n🎬 保存录屏...")
            try:
                if context:
                    context.close()
                if browser:
                    browser.close()
            except Exception as e:
                print(f"⚠️ 关闭浏览器: {e}")

            stop_ffmpeg_recording()
            stop_xvfb()

            if os.path.exists(RECORDING_FILE):
                print(f"✅ 视频: {RECORDING_FILE}")


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
