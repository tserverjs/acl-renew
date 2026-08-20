#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACL Cloud 自动续期脚本（Playwright 完整版 v7.2-fix）
"""

import os
import sys
import time
import subprocess
import signal
import atexit
import requests
from datetime import datetime
from PIL import Image
from io import BytesIO
import pytesseract
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ========== 配置 ==========
USERNAME = os.getenv("ACL_USERNAME", "")
PASSWORD = os.getenv("ACL_PASSWORD", "")
LOGIN_URL = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
WECHAT_WEBHOOK_KEY = os.getenv("WECHAT_WEBHOOK_KEY", "")
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
        except:
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
            except:
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
                except:
                    continue
            print(f"  ⚠️ 第 {attempt+1} 次：表单未就绪，重试...")
            diagnostic_screenshot(page, f"login_retry_{attempt+1}")
            time.sleep(3)
        except Exception as e:
            print(f"  ❌ 第 {attempt+1} 次加载失败: {e}")
            diagnostic_screenshot(page, f"load_error_{attempt+1}")
            time.sleep(3)
    return False


def process_captcha(page, flow_name=""):
    print(f"\n🔄 开始处理{flow_name}人机验证...")

    # 1. 点击复选框
    try:
        checkbox = page.locator(
            "div.auth-captcha-checkbox, input[type='checkbox'] + label, .captcha-checkbox"
        ).first
        checkbox.wait_for(state="visible", timeout=10000)
        checkbox.hover()
        time.sleep(0.3)
        checkbox.click()
        time.sleep(1.5)
        print("  ✅ 复选框已点击")
    except Exception as e:
        print(f"  ⚠️ 点击复选框失败: {e}")
        return False

    # 2. 获取提示文字
    try:
        prompt = page.locator("div.auth-captcha-prompt, .captcha-prompt").first
        prompt.wait_for(state="visible", timeout=5000)
        strong_text = prompt.locator("strong").inner_text()
        print(f"  📝 验证码提示文字: {strong_text}")
    except Exception as e:
        print(f"  ⚠️ 获取提示文字失败: {e}")
        return False

    # 3. 获取选项
    try:
        options = page.locator(
            "div.auth-captcha-options button, .captcha-options .captcha-option, "
            "button.auth-captcha-option, .captcha-option"
        ).all()
        if not options:
            print("  ⚠️ 未找到选项")
            return False
        print(f"  📍 共 {len(options)} 个选项")
    except Exception as e:
        print(f"  ⚠️ 获取选项失败: {e}")
        return False

    # 4. OCR 识别并点击
    base_url = page.url.rstrip("/auth/login").rstrip("/")
    target = strong_text.lower().replace(" ", "").replace("-", "")
    clicked = False

    for idx, btn in enumerate(options):
        try:
            img = btn.locator("img.auth-captcha-option-img, img").first
            src = img.get_attribute("src")
            full_url = base_url + src if src.startswith("/") else src
            print(f"     📍 选项 {idx + 1}: {full_url[:50]}...")
            resp = requests.get(full_url, timeout=10)
            img_obj = Image.open(BytesIO(resp.content)).convert("L")
            img_obj = img_obj.point(lambda x: 255 if x > 128 else 0)
            ocr_text = pytesseract.image_to_string(img_obj, lang='eng', config='--psm 7').strip()
            ocr_clean = ocr_text.lower().replace(" ", "").replace("-", "")
            print(f"        OCR: '{ocr_text}' → 清洗: '{ocr_clean}'")
            if target in ocr_clean or ocr_clean in target:
                print(f"  ✅ 匹配选项 {idx + 1} (OCR: {ocr_text})")
                btn.scroll_into_view_if_needed()
                time.sleep(0.3)
                btn.click()
                clicked = True
                time.sleep(2)
                break
        except Exception as e:
            print(f"     ❌ 选项 {idx + 1}: {e}")

    if not clicked:
        print("  ❌ 未点击任何选项")
        return False

    # 5. 检查验证结果
    time.sleep(2)
    try:
        verified = page.locator("span.auth-captcha-label, .captcha-label").first
        if verified.is_visible():
            vtext = verified.inner_text()
            if "Verified" in vtext or "Vérifié" in vtext:
                print(f"  ✅ 验证通过 (Verified: {vtext})")
                return True
    except:
        pass

    try:
        error_selectors = [".auth-captcha-error", ".captcha-error", ".text-danger", "[class*='error']"]
        for sel in error_selectors:
            try:
                els = page.locator(sel).all()
                for el in els:
                    if el.is_visible() and el.inner_text().strip():
                        print(f"  ❌ 验证失败（错误: {el.inner_text().strip()[:50]}）")
                        return False
            except:
                continue

        captcha_selectors = ["div.auth-captcha-options", ".captcha-options", "div.auth-captcha-prompt", ".auth-captcha-checkbox"]
        visible_captcha = 0
        for sel in captcha_selectors:
            try:
                els = page.locator(sel).all()
                for el in els:
                    if el.is_visible():
                        visible_captcha += 1
            except:
                continue
        if visible_captcha == 0:
            print("  ✅ 验证通过（弹窗关闭）")
            return True

        success_selectors = [".alert-success", ".text-success", ".success-message",
                             "[class*='success']", "[class*='verified']", ".toast-success", ".notification-success"]
        for sel in success_selectors:
            try:
                els = page.locator(sel).all()
                for el in els:
                    if el.is_visible():
                        txt = el.inner_text().strip()
                        if txt:
                            print(f"  ✅ 验证通过 (成功提示: {txt[:30]})")
                            return True
            except:
                continue
    except Exception as e:
        print(f"  ⚠️ 验证检测异常: {e}")

    print("  ⚠️ 无法确认验证状态，假设通过")
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

        # SPA 内容检测
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
                except:
                    continue
        except:
            pass

        # 登录表单消失检测
        try:
            email_input = page.locator("input[name='email'], input[name='username'], input[type='email']").first
            pwd_input = page.locator("input[type='password']").first
            form_gone = False
            try:
                form_gone = not email_input.is_visible(timeout=500) or not pwd_input.is_visible(timeout=500)
            except:
                form_gone = True

            if form_gone:
                body_text = page.locator("body").inner_text()
                dashboard_keywords = ["Bienvenue", "Dashboard", "Tableau de bord",
                                      "Mes services", "My services", "Accueil", "Commander",
                                      "Suivi des dépenses", "Vos prochains renouvellements"]
                if any(kw in body_text for kw in dashboard_keywords):
                    print(f"  🎉 登录成功（表单消失 + Dashboard 内容）")
                    return True
        except:
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
    except:
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
        except:
            continue

    if not lang_button:
        print("⚠️ 未找到语言切换按钮，跳过")
        return False

    current_lang = ""
    try:
        badge = lang_button.locator(".lang-code-badge").first
        current_lang = badge.inner_text().strip().upper()
    except:
        try:
            flag_img = lang_button.locator("img").first
            alt_text = flag_img.get_attribute("alt") or ""
            if "english" in alt_text.lower():
                current_lang = "EN"
            elif "français" in alt_text.lower() or "francais" in alt_text.lower():
                current_lang = "FR"
        except:
            pass

    if current_lang == "EN":
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
                html = loc.inner_html().lower()
                text = loc.inner_text().lower()
                if "en" in text and "english" in text:
                    en_option = loc
                    print(f"✅ 直接定位到 English 选项: '{sel}'")
                    break
        except:
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
            except:
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
                                
                                if src.startswith("http") or src.startswith("/"):
                                    base_url = page.url.rstrip("/").rstrip("/auth/login")
                                    full_url = src if src.startswith("http") else base_url + src
                                    resp = requests.get(full_url, timeout=10)
                                    img_obj = Image.open(BytesIO(resp.content)).convert("L")
                                    img_obj = img_obj.point(lambda x: 255 if x > 128 else 0)
                                    ocr_text = pytesseract.image_to_string(
                                        img_obj, lang='eng', config='--psm 7'
                                    ).strip().lower()
                                    print(f"        OCR 结果: '{ocr_text}'")
                                    if "en" in ocr_text or "english" in ocr_text or "british" in ocr_text:
                                        en_option = btn
                                        print(f"  ✅ OCR 匹配到 English 选项 {idx + 1}")
                                        break
                        except Exception as e:
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
                except:
                    continue
        except:
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
    except:
        pass

    try:
        badge = page.locator("button[class*='LanguageButton'] .lang-code-badge").first
        if badge.is_visible(timeout=3000):
            if badge.inner_text().strip().upper() == "EN":
                print("✅ 语言切换验证通过：EN")
                return True
    except:
        pass
    
    try:
        body_text = page.locator("body").inner_text()
        if "My services" in body_text or "Dashboard" in body_text:
            print("✅ 语言切换验证通过（页面内容已英文）")
            return True
    except:
        pass
        
    print("⚠️ 无法验证语言切换结果，继续执行")
    return True


def needs_renewal(status_text):
    if not status_text:
        return False
    status_lower = status_text.lower()
    keywords = [
        "suspended", "expired", "suspendu", "expiré", "terminé",
        "inactive", "inactif", "ended", "non actif", "renouvellement",
        "renewal", "renouveler", "renew"
    ]
    return any(kw in status_lower for kw in keywords)


def perform_renewal(page):
    global NEED_RENEWAL, RENEWAL_SUCCESS
    print("\n🔄 开始执行续期操作...")
    close_install_popup(page)

    try:
        table = page.locator("div.home-renewal-table, .renewal-table, [class*='renewal']").first
        table.wait_for(state="visible", timeout=15000)
        print("✅ 续期表格已加载")
        table.scroll_into_view_if_needed()
        time.sleep(1)

        row_selectors = [
            "div.home-renewal-row", ".renewal-row",
            "tr[class*='renewal']", "div[class*='renewal-row']",
            ".home-renewal-table > div > div"
        ]

        renewal_rows = []
        for sel in row_selectors:
            rows = page.locator(sel).all()
            if len(rows) > 0:
                renewal_rows = rows
                print(f"📋 使用选择器 '{sel}' 找到 {len(rows)} 个续期项目")
                break

        if not renewal_rows:
            print("⚠️ 未找到续期项目")
            return True

        for idx, row in enumerate(renewal_rows):
            try:
                status = safe_find_text(row, ["span.home-renewal-status", ".renewal-status", "td:nth-child(4)", ".status"])
                model_name = safe_find_text(row, ["strong.home-renewal-name", ".renewal-name", "td:nth-child(2)", ".model"])
                renewal_date = safe_find_text(row, ["strong.home-renewal-date-main", ".renewal-date", "td:nth-child(3)", ".date"])

                print(f"\n📦 项目 {idx + 1}: {model_name or '未知'}")
                print(f"  📅 续期日期: {renewal_date or '未知'}")
                print(f"  📊 状态: {status or '未知'}")

                if not status:
                    continue

                if needs_renewal(status):
                    NEED_RENEWAL = True
                    print(f"  ⚠️ 需要续期！")
                    row.scroll_into_view_if_needed()
                    time.sleep(0.5)

                    renew_sels = [
                        "button.home-renew-action", ".renew-action",
                        "button[class*='renew']", "td:last-child button", ".actions button"
                    ]
                    renew_button = None
                    for rsel in renew_sels:
                        try:
                            rb = row.locator(rsel).first
                            if rb and rb.is_visible():
                                renew_button = rb
                                print(f"  ✅ 找到 Renew 按钮 ({rsel})")
                                break
                        except:
                            continue

                    if not renew_button:
                        print(f"  ❌ 未找到 Renew 按钮")
                        continue

                    renew_button.click()
                    print(f"  ✅ 已点击 Renew 按钮")
                    time.sleep(2)

                    if process_captcha(page, flow_name="renewal_popup"):
                        RENEWAL_SUCCESS = True
                        print("✅ 续期验证通过！")
                    else:
                        print("❌ 续期验证失败")
                    time.sleep(2)
                    close_install_popup(page)
                else:
                    print(f"  ✅ 状态正常，无需续期")

            except Exception as e:
                print(f"  ❌ 处理项目 {idx + 1} 出错: {e}")
                continue

        return True

    except PlaywrightTimeout:
        print("❌ 续期表格加载超时")
        return False
    except Exception as e:
        print(f"❌ 续期操作失败: {e}")
        return False


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
        except:
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
    print("\n🔧 点击 Manage / Gérer 按钮...")
    manage_selectors = [
        "a.client-btn--primary[href^='/server/']",
        "a[href^='/server/'].client-btn",
        "//a[contains(@href, '/server/') and contains(@class, 'client-btn--primary')]",
        "//a[contains(text(), 'Manage')]",
        "//a[contains(text(), 'Gérer')]",
        ".client-btn--primary",
        "a:has-text('Manage')",
        "a:has-text('Gérer')"
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
        except:
            continue

    if not manage_btn:
        print("❌ 未找到 Manage 按钮")
        return False

    manage_btn.scroll_into_view_if_needed()
    time.sleep(0.5)
    manage_btn.click()
    time.sleep(3)
    print("✅ 已进入服务器详情页")
    return True


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
    except:
        info["server_name"] = "ACL Cloud Server"

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
            else:
                print(f"  ⚪ 未知状态: {txt}")
    except Exception as e:
        print(f"  ⚠️ status-badge 检测失败: {e}")

    try:
        info_container = None
        info_selectors = [
            "div[style*='background: rgba(49, 95, 79']",
            "div[style*='background: rgba(49, 95, 79, 0.06)']",
            ".server-info-card",
            "[class*='server-info']",
            "//div[contains(text(), 'Time remaining')]",
            "//div[contains(text(), 'Temps restant')]",
        ]
        for sel in info_selectors:
            try:
                if sel.startswith("//"):
                    loc = page.locator(f"xpath={sel}").first
                else:
                    loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    info_container = loc
                    print(f"  ✅ 找到信息容器: '{sel}'")
                    break
            except:
                continue

        if info_container:
            text = info_container.inner_text()
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            for line in lines:
                if "Time remaining" in line or "Temps restant" in line:
                    info["time_remaining"] = line.split(":", 1)[-1].strip()
                elif any(w in line.lower() for w in ["plan", "gratuit", "free", "套餐"]):
                    info["plan"] = line
                elif any(w in line.lower() for w in ["renewal", "renouvellement", "renew"]):
                    info["renewal_note"] = line

            print(f"  ⏰ Time remaining: {info['time_remaining'] or '未获取'}")
            print(f"  📋 Plan: {info['plan'] or '未获取'}")
            print(f"  📝 Renewal: {info['renewal_note'] or '未获取'}")
        else:
            print("  ⚠️ 未找到信息容器（Time remaining 等）")
    except Exception as e:
        print(f"  ❌ 获取 Time remaining 信息失败: {e}")

    try:
        stat_items = page.locator("div.stat-item").all()
        for item in stat_items:
            try:
                label = item.locator(".stat-label").first.inner_text(timeout=1000).strip()
                if "Status" in label or "Uptime" in label or "status" in label.lower():
                    value = item.locator(".stat-value").first.inner_text(timeout=1000).strip()
                    info["uptime"] = value
                    SERVER_UPTIME = value
                    print(f"  ⏱️ Status/Uptime: {value}")
                    break
            except:
                continue
        if not info["uptime"]:
            print("  ℹ️ 未获取到 Uptime 信息")
    except Exception as e:
        print(f"  ⚠️ 获取 Uptime 失败: {e}")

    diagnostic_screenshot(page, "server_info")
    return info


def manage_server_power(page):
    global POWER_ACTION, SERVER_UPTIME
    print("\n⚡ 检测服务器电源状态并执行操作...")

    is_offline = False
    status_text = ""

    try:
        badge = page.locator("span.status-badge[data-status], .status-badge, [class*='status-badge']").first
        if badge.is_visible(timeout=3000):
            ds = (badge.get_attribute("data-status") or "").lower()
            txt = badge.inner_text().strip().lower()
            status_text = ds or txt
            print(f"  📊 状态徽章: data-status={ds}, text={txt}")
    except Exception as e:
        print(f"  ⚠️ status-badge 检测失败: {e}")

    if not status_text:
        try:
            stat_items = page.locator("div.stat-item").all()
            for item in stat_items:
                try:
                    label = item.locator(".stat-label").first.inner_text(timeout=1000).strip()
                    if "Status" in label or "Uptime" in label:
                        value = item.locator(".stat-value").first.inner_text(timeout=1000).strip()
                        if any(c in value for c in ["h", "m", "s", "d"]):
                            status_text = "online"
                            SERVER_UPTIME = value
                            print(f"  📊 stat-item 判断: Online (uptime={value})")
                        break
                except:
                    continue
        except:
            pass

    is_offline = any(w in status_text for w in ["offline", "hors ligne", "inactif", "stopped"])

    if is_offline:
        print("🔴 服务器 Offline，执行 Start...")
        if wait_and_click(page, [
            "button.power-btn[data-variant='start']",
            "button[data-variant='start']",
            "//button[contains(@class, 'power-btn') and contains(., 'Start')]",
            "//button[contains(@class, 'power-btn') and contains(., 'Démarrer')]",
        ], label="Start 按钮"):
            POWER_ACTION = "start"
            print("✅ Start 已点击")
            return True
        print("❌ Start 按钮未找到或被禁用")
        return False
    else:
        print("🟢 服务器 Online，不执行重启，仅获取 Uptime...")
        try:
            stat_items = page.locator("div.stat-item").all()
            for item in stat_items:
                try:
                    label = item.locator(".stat-label").first.inner_text(timeout=1000).strip()
                    if "Status" in label or "Uptime" in label or "status" in label.lower():
                        value = item.locator(".stat-value").first.inner_text(timeout=1000).strip()
                        SERVER_UPTIME = value
                        print(f"  ⏱️ Uptime: {value}")
                        break
                except:
                    continue
        except:
            pass
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
        power_text = "🚀 已执行 Start（服务器离线，执行启动）"
    elif power_action == "restart":
        power_text = "🔄 已执行 Restart（服务器在线，执行重启）"
    else:
        power_text = "➖ 未执行电源操作（服务器在线）"

    sem = "🟢" if status == "online" else "🔴" if status == "offline" else "⚪"

    content = f"""{color} <b>ACL Cloud 服务器状态报告</b> {color}

📌 <b>服务器:</b> {server_name}
{sem} <b>当前状态:</b> {status.upper()}
⏱️ <b>运行时间:</b> {uptime}
⏰ <b>剩余时间:</b> {time_remaining}
📋 <b>套餐信息:</b> {plan}
📝 <b>续期提示:</b> {renewal_note or '无'}

📊 <b>续期状态:</b> {status_emoji} {status_text}
🔧 <b>执行动作:</b> {action_text}
⚡ <b>电源状态:</b> {power_text}

🔗 <a href=\"{server_url}\">点击访问服务器详情页</a>

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
    video_path = ""
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
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1920,1080",
                    "--start-maximized",
                ]
            )

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
                print(f"\n🔄 验证码尝试 {attempt+1}/{MAX_RETRIES}")
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
                renewal_table = page.locator("div.home-renewal-table, .renewal-table, [class*='renewal']").first
                renew_buttons = page.locator("button.home-renew-action, .renew-action, button[class*='renew']").all()
                visible_renew = [b for b in renew_buttons if b.is_visible()]
                if renewal_table.is_visible() and len(visible_renew) > 0:
                    has_renewal = True
                    print("✅ 检测到需要续期的项目")
                else:
                    print("ℹ️ 未检测到需要续期的项目")
            except Exception:
                print("ℹ️ 仪表盘上没有续期表格")

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
                video_path = RECORDING_FILE
                print(f"✅ 视频: {video_path}")


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
