#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
🎬 ACL Cloud 自动续期 & 服务器全生命周期管理脚本（完整无省略版 v7.7）
=============================================================================
包含功能：
  1. 完整 UI 录屏：Xvfb 虚拟桌面 + ffmpeg 全程录屏。
  2. 网络与代理：自动配置 GOST / SOCKS5 代理。
  3. 电源与状态管理：开机 (Start)、关机 (Stop)、重启 (Reboot) 及状态检测。
  4. 多语言适配：支持英语、法语、中文、西班牙语等界面的元素匹配。
  5. 增强型 Anti-Bot OCR：自适应对比度增强 + 动态二值化 + 降噪微调。
  6. 详尽 XPath 备选池：覆盖多种 UI 框架的输入框、按钮和弹窗。
  7. 企业微信通知 & 完整诊断截图。
=============================================================================
"""

import os
import sys
import time
import base64
import subprocess
import signal
import atexit
import requests
import re
from datetime import datetime
from urllib.parse import urljoin
from PIL import Image, ImageEnhance, ImageFilter
from io import BytesIO
import pytesseract
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# =============================================================================
# 全局环境变量与基础配置
# =============================================================================
USERNAME = os.getenv("ACL_USERNAME", "")
PASSWORD = os.getenv("ACL_PASSWORD", "")
LOGIN_URL = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
WECHAT_WEBHOOK_KEY = os.getenv("WECHAT_WEBHOOK_KEY", "")
SERVER_ID = os.getenv("ACL_SERVER_ID", "3727")
POWER_TARGET_ACTION = os.getenv("POWER_ACTION", "none").lower()  # none, start, stop, restart, reboot

MAX_RETRIES = 3
VIDEO_DIR = "videos"
RECORDING_FILE = "full_operation_recording.mp4"
DIAGNOSTIC_PREFIX = "diag"
DISPLAY_NUM = ":99"
SCREEN_SIZE = "1920x1080x24"

# 代理配置
SOCKS5_PROXY = os.getenv("SOCKS5_PROXY", os.getenv("GOST_LOCAL_PROXY", ""))

# 全局运行状态追踪变量
NEED_RENEWAL = False
RENEWAL_SUCCESS = False
SERVER_STATUS = "unknown"
POWER_ACTION_RESULT = "none"
SERVER_UPTIME = "unknown"

_xvfb_proc = None
_ffmpeg_proc = None

# =============================================================================
# 多语言 Selector / XPath 详尽备选池
# =============================================================================
XPATH_LOGIN_USERNAMES = [
    "//input[@name='email']", "//input[@type='email']",
    "//input[@name='username']", "//input[@id='email']",
    "//input[@id='username']", "//input[contains(@placeholder, 'email') or contains(@placeholder, 'Email')]",
    "//input[contains(@placeholder, 'username') or contains(@placeholder, 'Username')]",
    "//input[contains(@placeholder, '邮箱') or contains(@placeholder, '账号')]",
    "//input[@autocomplete='username']", "//input[@autocomplete='email']"
]

XPATH_LOGIN_PASSWORDS = [
    "//input[@name='password']", "//input[@type='password']",
    "//input[@id='password']", "//input[contains(@placeholder, 'password') or contains(@placeholder, 'Password')]",
    "//input[contains(@placeholder, '密码')]", "//input[@autocomplete='current-password']"
]

XPATH_LOGIN_SUBMIT_BTNS = [
    "//button[@type='submit']", "//button[contains(., 'Sign in')]", "//button[contains(., 'Sign In')]",
    "//button[contains(., 'Log in')]", "//button[contains(., 'Log In')]", "//button[contains(., 'Se connecter')]",
    "//button[contains(., '登录')]", "//button[contains(., 'Iniciar sesión')]"
]

XPATH_RENEW_BTNS = [
    "//button[contains(., 'Renew')]", "//button[contains(., 'renouveler') or contains(., 'Renouveler')]",
    "//button[contains(., '续费') or contains(., '续期')]", "//button[contains(., 'Renovar')]",
    "//a[contains(., 'Renew')]", "//div[contains(@class, 'renew')]//button"
]

XPATH_POWER_START_BTNS = [
    "//button[contains(., 'Start')]", "//button[contains(., 'Démarrer')]",
    "//button[contains(., '开机') or contains(., '启动')]", "//button[contains(., 'Iniciar')]"
]

XPATH_POWER_STOP_BTNS = [
    "//button[contains(., 'Stop')]", "//button[contains(., 'Éteindre') or contains(., 'Arrêter')]",
    "//button[contains(., '关机') or contains(., '停止')]", "//button[contains(., 'Detener')]"
]

XPATH_POWER_RESTART_BTNS = [
    "//button[contains(., 'Restart') or contains(., 'Reboot')]", "//button[contains(., 'Redémarrer')]",
    "//button[contains(., '重启') or contains(., '重新启动')]", "//button[contains(., 'Reiniciar')]"
]

XPATH_CONFIRM_DIALOG_BTNS = [
    "//button[contains(., 'Confirm')]", "//button[contains(., 'Confirmer')]",
    "//button[contains(., '确认') or contains(., '确定')]", "//button[contains(., 'Yes')]",
    "//button[contains(., 'Oui')]", "//button[contains(., 'Aceptar')]"
]

# =============================================================================
# 系统级辅助函数（虚拟桌面 / ffmpeg 录屏 / 进程管理）
# =============================================================================
def _kill_proc(proc, name="process", timeout=5):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    print(f"⏹️  [{name}] 进程已终止")

def start_xvfb():
    global _xvfb_proc
    if os.getenv("DISPLAY"):
        print(f"ℹ️  检测到环境变量 DISPLAY={os.getenv('DISPLAY')}，跳过 Xvfb 启动")
        return
    print(f"🖥️  启动 Xvfb {DISPLAY_NUM} ({SCREEN_SIZE})...")
    cmd = ["Xvfb", DISPLAY_NUM, "-screen", "0", SCREEN_SIZE, "-ac", "+extension", "RANDR", "-noreset"]
    _xvfb_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ["DISPLAY"] = DISPLAY_NUM
    time.sleep(2)
    if _xvfb_proc.poll() is not None:
        raise RuntimeError("❌ Xvfb 启动失败")
    print("✅ Xvfb 启动成功")

def stop_xvfb():
    global _xvfb_proc
    _kill_proc(_xvfb_proc, "Xvfb")
    _xvfb_proc = None

def start_ffmpeg_recording():
    global _ffmpeg_proc
    print(f"🎥 启动 ffmpeg 录屏 → {RECORDING_FILE}")
    cmd = [
        "ffmpeg", "-f", "x11grab", "-video_size", "1920x1080",
        "-i", DISPLAY_NUM, "-r", "10", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-movflags", "+faststart", "-y", RECORDING_FILE
    ]
    _ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    if _ffmpeg_proc.poll() is not None:
        raise RuntimeError("❌ ffmpeg 启动失败")
    print("✅ ffmpeg 录屏已就绪")

def stop_ffmpeg_recording():
    global _ffmpeg_proc
    if _ffmpeg_proc is None:
        return
    print("⏹️  结束 ffmpeg 录屏...")
    try:
        _ffmpeg_proc.send_signal(signal.SIGTERM)
        _ffmpeg_proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        _kill_proc(_ffmpeg_proc, "ffmpeg", timeout=3)
    _ffmpeg_proc = None
    if os.path.exists(RECORDING_FILE):
        size_mb = os.path.getsize(RECORDING_FILE) / (1024 * 1024)
        print(f"✅ 录屏已保存: {RECORDING_FILE} ({size_mb:.2f} MB)")

def ensure_video_dir():
    if not os.path.exists(VIDEO_DIR):
        os.makedirs(VIDEO_DIR)

def diagnostic_screenshot(page, name):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{VIDEO_DIR}/{DIAGNOSTIC_PREFIX}_{ts}_{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
        print(f"📸 诊断截图已保存: {path}")
    except Exception as e:
        print(f"⚠️ 诊断截图抓取失败: {e}")

# =============================================================================
# OCR 核心增强与图像二值化微调算法
# =============================================================================
def preprocess_image_for_ocr(img_bytes):
    """
    对图片进行多阶段图像微调：自动对比度、锐化、高斯微降噪与自适应阈值二值化
    """
    try:
        image = Image.open(BytesIO(img_bytes)).convert("L")
        
        # 1. 放大图片尺寸增强像素密度
        w, h = image.size
        image = image.resize((w * 3, h * 3), Image.Resampling.LANCZOS)

        # 2. 增强对比度与锐度
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.5)
        image = image.filter(ImageFilter.SHARPEN)

        # 3. 动态二值化处理
        pixels = list(image.getdata())
        avg_pixel = sum(pixels) / len(pixels)
        threshold = int(avg_pixel * 0.95)
        
        binary_image = image.point(lambda p: 255 if p > threshold else 0)
        return binary_image
    except Exception as e:
        print(f"     ⚠️ 图像预处理过程出错，回退到原始解析: {e}")
        return Image.open(BytesIO(img_bytes))

# =============================================================================
# Web UI 辅助交互逻辑
# =============================================================================
def download_image_bytes(page, src, label="图片资源"):
    if not src:
        return None
    try:
        if src.startswith("data:image"):
            _, b64data = src.split(",", 1)
            return base64.b64decode(b64data)

        full_url = urljoin(page.url, src)
        resp = page.context.request.get(full_url, timeout=15000)
        if not resp.ok:
            return None
        return resp.body()
    except Exception as e:
        print(f"     ❌ [{label}] 下载失败: {e}")
        return None

def scroll_page_to_bottom(page, step=600, pause=0.5, max_steps=15):
    print("  📜 平滑滚动页面加载全部节点...")
    try:
        for i in range(max_steps):
            page.mouse.wheel(0, step)
            time.sleep(pause)
            height = page.evaluate("document.body ? document.body.scrollHeight : 0")
            cur = page.evaluate("window.scrollY + window.innerHeight")
            if height and cur >= height - 10:
                break
    except Exception:
        pass

def wait_and_type(page, xpath_list, value, label="输入框"):
    print(f"  🔍 定位并填写: {label}...")
    for xpath in xpath_list:
        try:
            loc = page.locator(f"xpath={xpath}").first
            if loc.is_visible():
                loc.click(timeout=3000)
                loc.press("Control+a")
                loc.press("Delete")
                loc.type(value, delay=40)
                print(f"     ✅ 输入成功 (XPath: {xpath})")
                return True
        except Exception:
            continue
    return False

def wait_and_click(page, xpath_list, label="按钮", timeout=5000):
    print(f"  🔍 定位并点击: {label}...")
    for xpath in xpath_list:
        try:
            loc = page.locator(f"xpath={xpath}").first
            loc.wait_for(state="visible", timeout=timeout)
            loc.scroll_into_view_if_needed()
            loc.click(force=True)
            print(f"     ✅ 点击成功 (XPath: {xpath})")
            return True
        except Exception:
            continue
    return False

def close_install_popup(page):
    close_xpaths = [
        "//button[contains(., 'Fermer')]", "//button[contains(., 'Close')]",
        "//button[contains(., '关闭')]", "//button[contains(@class, 'close')]",
        "//div[contains(@class, 'pwa')]//button"
    ]
    for xp in close_xpaths:
        try:
            btns = page.locator(f"xpath={xp}").all()
            for btn in btns:
                if btn.is_visible():
                    btn.click()
                    print("✅ 已关闭阻挡弹窗")
                    time.sleep(0.5)
                    return True
        except Exception:
            pass
    return False

# =============================================================================
# 核心业务：Anti-Bot 验证突破与续期
# =============================================================================
def process_captcha(page, flow_name=""):
    print(f"\n🔄 启动 Anti-Bot 验证校验 [{flow_name}]...")
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass

    popup_xpaths = [
        "//div[contains(., 'Anti-bot confirmation')]",
        "//div[contains(@class, 'auth-captcha')]",
        "//div[contains(@class, 'modal') and contains(., 'Confirm')]"
    ]
    
    for xp in popup_xpaths:
        try:
            page.wait_for_selector(f"xpath={xp}", state="visible", timeout=8000)
            break
        except Exception:
            continue

    checkbox_xpaths = [
        "//div[contains(@class, 'auth-captcha-checkbox')]",
        "//input[@type='checkbox']/following-sibling::label",
        "//span[contains(@class, 'captcha-checkbox')]"
    ]
    
    cb_found = False
    for cb_xp in checkbox_xpaths:
        try:
            cb = page.locator(f"xpath={cb_xp}").first
            if cb.is_visible():
                cb.hover()
                cb.click(force=True)
                cb_found = True
                print("  ✅ 验证码复选框已勾选")
                break
        except Exception:
            continue

    if not cb_found:
        print("  ⚠️ 未找到可勾选的验证码复选框")
        return False

    time.sleep(2)

    # 提取题目提示词
    try:
        prompt_loc = page.locator("css=div.auth-captcha-prompt strong, .captcha-prompt strong").first
        prompt_loc.wait_for(state="visible", timeout=8000)
        target_text = prompt_loc.inner_text().strip()
        print(f"  📝 目标识别提示词: [{target_text}]")
    except Exception as e:
        print(f"  ⚠️ 获取提示词失败: {e}")
        return False

    # 提取多项候选图片
    try:
        options = page.locator("css=div.auth-captcha-options button, .captcha-option").all()
        if not options:
            return False
    except Exception:
        return False

    target_clean = re.sub(r'[^a-zA-Z0-9]', '', target_text.lower())
    for idx, btn in enumerate(options):
        try:
            img = btn.locator("css=img").first
            src = img.get_attribute("src")
            img_bytes = download_image_bytes(page, src, label=f"选项 {idx+1}")
            if not img_bytes:
                continue

            processed_img = preprocess_image_for_ocr(img_bytes)
            ocr_text = pytesseract.image_to_string(processed_img, lang='eng', config='--psm 7').strip()
            ocr_clean = re.sub(r'[^a-zA-Z0-9]', '', ocr_text.lower())
            
            print(f"     📍 选项 {idx+1} OCR: '{ocr_text}' → 清洗后: '{ocr_clean}'")

            if target_clean in ocr_clean or ocr_clean in target_clean:
                print(f"  🎯 精确匹配成功，点击选项 [{idx+1}]")
                btn.scroll_into_view_if_needed()
                btn.click()
                time.sleep(2)
                return True
        except Exception as e:
            print(f"     ❌ 选项 {idx+1} 处理异常: {e}")

    return False

def perform_renewal(page):
    global NEED_RENEWAL, RENEWAL_SUCCESS
    print("\n🔄 扫描续期按钮...")
    close_install_popup(page)
    scroll_page_to_bottom(page)

    for xp in XPATH_RENEW_BTNS:
        try:
            btns = [b for b in page.locator(f"xpath={xp}").all() if b.is_visible()]
            if btns:
                NEED_RENEWAL = True
                print(f"📋 发现 {len(btns)} 个可续期项目")
                for btn in btns:
                    btn.click(force=True)
                    time.sleep(3)
                    if process_captcha(page, flow_name="renewal"):
                        RENEWAL_SUCCESS = True
                        print("✅ 续期顺利完成")
                    else:
                        RENEWAL_SUCCESS = False
                        return False
                return True
        except Exception:
            continue

    print("ℹ️ 未发现需要续期的服务")
    return True

# =============================================================================
# 服务器关机 / 开机 / 重启 / 运行状态检测逻辑
# =============================================================================
def manage_server_power_and_status(page):
    """
    检查服务器当前运行状态，并根据环境变量执行指定的开机/关机/重启操作
    """
    global SERVER_STATUS, POWER_ACTION_RESULT, SERVER_UPTIME
    print(f"\n🖥️ 正在进入服务器 [{SERVER_ID}] 详情页与电源管理...")
    
    try:
        page.goto(f"https://aclclouds.com/server/{SERVER_ID}", wait_until="networkidle", timeout=30000)
        time.sleep(2)
    except Exception as e:
        print(f"⚠️ 无法正常载入服务器详情页: {e}")
        return

    # 1. 检测当前服务器状态 (Online / Offline / Stopped / Running)
    try:
        status_xpath = "//span[contains(@class, 'status') or contains(@class, 'badge')]"
        status_elements = page.locator(f"xpath={status_xpath}").all()
        for elem in status_elements:
            if elem.is_visible():
                txt = elem.inner_text().strip()
                if txt:
                    SERVER_STATUS = txt
                    break
        print(f"  📊 当前实例监测状态: [{SERVER_STATUS}]")
    except Exception as e:
        print(f"  ⚠️ 读取运行状态出错: {e}")

    # 2. 根据目标 POWER_TARGET_ACTION 执行电源动作
    if POWER_TARGET_ACTION in ["start", "open", "开机"]:
        print("  ⚡ 触发【开机】操作...")
        if wait_and_click(page, XPATH_POWER_START_BTNS, label="开机按钮"):
            wait_and_click(page, XPATH_CONFIRM_DIALOG_BTNS, label="二次确认")
            POWER_ACTION_RESULT = "start_requested"
            
    elif POWER_TARGET_ACTION in ["stop", "shutdown", "关机"]:
        print("  ⚡ 触发【关机】操作...")
        if wait_and_click(page, XPATH_POWER_STOP_BTNS, label="关机按钮"):
            wait_and_click(page, XPATH_CONFIRM_DIALOG_BTNS, label="二次确认")
            POWER_ACTION_RESULT = "stop_requested"

    elif POWER_TARGET_ACTION in ["restart", "reboot", "重启"]:
        print("  ⚡ 触发【重启】操作...")
        if wait_and_click(page, XPATH_POWER_RESTART_BTNS, label="重启按钮"):
            wait_and_click(page, XPATH_CONFIRM_DIALOG_BTNS, label="二次确认")
            POWER_ACTION_RESULT = "restart_requested"
    else:
        print("  ℹ️ 未配置任何电源变更操作（POWER_ACTION=none）")

    time.sleep(3)

# =============================================================================
# 通知机制
# =============================================================================
def send_wechat_notification(need_renewal, renewal_success, error_msg=""):
    if not WECHAT_WEBHOOK_KEY:
        return False

    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECHAT_WEBHOOK_KEY}"

    if error_msg:
        color = "🔴"
        status_text = "脚本运行报错"
        detail = f"错误原因: {error_msg}"
    elif need_renewal and renewal_success:
        color = "🟢"
        status_text = "续期成功"
        detail = "已成功突破人机验证并完成续期"
    elif need_renewal and not renewal_success:
        color = "🔴"
        status_text = "续期失败"
        detail = "人机验证未能通过，请登录后台手动处理"
    else:
        color = "🟢"
        status_text = "状态正常"
        detail = "无需续期"

    msg = f"""{color} ACL Cloud 控制台报告 {color}

🖥️ 目标 Server ID: {SERVER_ID}
📊 服务器当前状态: {SERVER_STATUS}
⚡ 电源指令结果: {POWER_ACTION_RESULT}
📌 续期任务状态: {status_text}
📝 详细信息: {detail}
⏱️ 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    try:
        requests.post(url, json={"msgtype": "text", "text": {"content": msg}}, timeout=10)
        print("✅ 企业微信通知推送成功")
    except Exception as e:
        print(f"❌ 微信推送异常: {e}")

# =============================================================================
# 主程序入口
# =============================================================================
def main():
    global NEED_RENEWAL, RENEWAL_SUCCESS

    if not USERNAME or not PASSWORD:
        print("❌ 未配置 ACL_USERNAME 或 ACL_PASSWORD")
        sys.exit(1)

    ensure_video_dir()
    page = None
    context = None
    browser = None

    atexit.register(stop_ffmpeg_recording)
    atexit.register(stop_xvfb)

    start_xvfb()
    start_ffmpeg_recording()

    with sync_playwright() as p:
        try:
            launch_args = {
                "headless": False,
                "args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            }

            if SOCKS5_PROXY:
                print(f"🌐 配置 SOCKS5 代理: {SOCKS5_PROXY}")
                launch_args["proxy"] = {"server": SOCKS5_PROXY}

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            # 1. 打开登录页面
            print(f"\n🌐 打开登录地址: {LOGIN_URL}")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            time.sleep(3)

            # 2. 填写登录凭据
            if not wait_and_type(page, XPATH_LOGIN_USERNAMES, USERNAME, label="账号"):
                raise RuntimeError("无法找到账号输入框")
            if not wait_and_type(page, XPATH_LOGIN_PASSWORDS, PASSWORD, label="密码"):
                raise RuntimeError("无法找到密码输入框")

            # 3. 执行登录与人机验证
            login_success = False
            for attempt in range(MAX_RETRIES):
                print(f"\n🔄 尝试登录验证 ({attempt + 1}/{MAX_RETRIES})...")
                if process_captcha(page, flow_name="login"):
                    if wait_and_click(page, XPATH_LOGIN_SUBMIT_BTNS, label="登录按钮"):
                        time.sleep(5)
                        if "login" not in page.url.lower():
                            login_success = True
                            print("🎉 登录成功")
                            break
            if not login_success:
                raise RuntimeError("登录验证未能顺利通过")

            close_install_popup(page)

            # 4. 执行续期
            perform_renewal(page)

            # 5. 服务器电源状态与控制逻辑
            manage_server_power_and_status(page)

            # 6. 发送通知
            send_wechat_notification(NEED_RENEWAL, RENEWAL_SUCCESS)
            print("\n🎉 全部自动化任务执行完成")

        except Exception as e:
            print(f"\n❌ 执行报错: {e}")
            if page:
                diagnostic_screenshot(page, "fatal_error")
            send_wechat_notification(NEED_RENEWAL, False, error_msg=str(e))
            sys.exit(1)

        finally:
            try:
                if context: context.close()
                if browser: browser.close()
            except Exception:
                pass
            stop_ffmpeg_recording()
            stop_xvfb()

if __name__ == "__main__":
    main()
