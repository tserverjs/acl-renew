#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================
  ACL Cloud 自动登录 + 续期脚本（弹窗验证码+全程录屏版）
  版本：v5.0
  功能：自动登录、处理登录页验证码、续期弹窗验证码、ffmpeg全程录屏
  依赖：selenium, pillow, pytesseract, requests, ffmpeg
============================================
"""

import os
import sys
import time
import subprocess
import requests
from io import BytesIO
from datetime import datetime
from PIL import Image
import pytesseract
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains

# ========== 配置区域 ==========
USERNAME = os.getenv("ACL_USERNAME", "")
PASSWORD = os.getenv("ACL_PASSWORD", "")
LOGIN_URL = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
MAX_RETRIES = 3
SCREENSHOT_DIR = "screenshots"
RECORDING_FILE = "full_operation_recording.mp4"
# =============================

def ensure_dirs():
    """确保截图目录存在"""
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)
        print(f"📁 创建截图目录: {SCREENSHOT_DIR}")

def take_screenshot(driver, step_name):
    """通用截图函数，自动添加时间戳"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    filename = f"{SCREENSHOT_DIR}/{timestamp}_{step_name}.png"
    driver.save_screenshot(filename)
    print(f"📸 截图已保存: {filename}")
    return filename

def start_ffmpeg_recording():
    """启动 ffmpeg 屏幕录制，适配 GitHub Actions 虚拟桌面"""
    print("🎥 启动 ffmpeg 全程录屏...")
    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "x11grab",
        "-video_size", "1920x1080",
        "-i", ":99",  # GitHub Actions 默认虚拟桌面 DISPLAY=:99
        "-r", "15",
        "-pix_fmt", "yuv420p",
        "-y",
        RECORDING_FILE
    ]
    process = subprocess.Popen

---
(
        ffmpeg_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    print(f"✅ 录屏已启动，输出文件: {RECORDING_FILE}")
    return process

def stop_ffmpeg_recording(process):
    """停止 ffmpeg 录屏进程"""
    print("⏹️  停止 ffmpeg 录屏...")
    process.terminate()
    process.wait()
    print(f"✅ 录屏已保存完成")

def setup_driver():
    """配置并启动 Chrome 浏览器（无头模式，适配 Chromium 151+）"""
    print("🚀 启动浏览器...")
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.binary_location = "/usr/bin/chromium-browser"
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    take_screenshot(driver, "01_browser_started")
    print("✅ 浏览器启动完成")
    return driver

def process_captcha_flow(driver, step_prefix):
    """通用验证码处理流程，登录页和续期弹窗通用"""
    print(f"🔄 执行验证码流程 [{step_prefix}]...")
    
    # 点击验证码复选框
    checkbox = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "div.auth-captcha-checkbox"))
    )
    ActionChains(driver).move_to_element(checkbox).pause(0.3).click().perform()
    time.sleep(1.5)
    take_screenshot(driver, f"{step_prefix}_captcha_checkbox_clicked")
    
    # 获取提示文字
    prompt_element = WebDriverWait(driver, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.auth-captcha-prompt"))
    )
    target_text = prompt_element.find_element(By.TAG_NAME, "strong").text.strip()
    print(f"📝 验证码提示文字: {target_text}")
    take_screenshot(driver, f"{step_prefix}_captcha_prompt_displayed")
    
    # 获取所有选项图片
    options_container = WebDriverWait(driver, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.auth-captcha-options"))
    )
    option_buttons = options_container.find_elements(By.CSS_SELECTOR, "button.auth-captcha-option")
    base_url = LOGIN_URL.rstrip("/auth/login").rstrip("/")
    
    for idx, button in enumerate(option_buttons):
        img = button.find_element(By.CSS_SELECTOR, "img.auth-captcha-option-img")
        img_src = img.get_attribute("src")
        
        # 下载并OCR识别
        if img_src.startswith("/"):
            full_url = base_url + img_src
        else:
            full_url = img_src
        
        response = requests.get(full_url, timeout=10)
        img_obj = Image.open(BytesIO(response.content)).convert("L")
        img_obj = img_obj.point(lambda x: 255 if x > 128 else 0)
        ocr_text = pytesseract.image_to_string(img_obj, lang='eng', config='--psm 7').strip()
        
        if target_text.lower().replace(" ", "") in ocr_text.lower().replace(" ", ""):
            print(f"✅ 找到匹配选项: 第 {idx+1} 张图片")
            ActionChains(driver).move_to_element(button).pause(0.2).click().perform()
            time.sleep(1)
            take_screenshot(driver, f"{step_prefix}_correct_option_clicked")
            
            # 验证结果
            try:
                verified_label = WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "span.auth-captcha-label"))
                )
                if "Verified" in verified_label.text:
                    print("✅ 验证码验证通过")
                    take_screenshot(driver, f"{step_prefix}_verification_passed")
                    return True
            except:
                pass
    
    print("❌ 验证码验证失败")
    take_screenshot(driver, f"{step_prefix}_verification_failed")
    return False

def login(driver):
    """执行登录操作"""
    print("🔑 输入用户名和密码...")
    selectors = [
        "input[name='email']", "input[type='email']",
        "input[placeholder*='email' i]", "input[name='username']"
    ]
    
    username_input = None
    for selector in selectors:
        try:
            username_input = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            if username_input: break
        except: continue
    
    password_input = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
    username_input.clear()
    password_input.clear()
    
    for char in USERNAME: username_input.send_keys(char); time.sleep(0.05)
    for char in PASSWORD: password_input.send_keys(char); time.sleep(0.05)
    
    take_screenshot(driver, "03_credentials_entered")
    print("✅ 账号密码已输入")
    
    # 处理登录页验证码
    captcha_success = process_captcha_flow(driver, "04_login_page")
    if not captcha_success:
        return False
    
    # 点击登录按钮
    signin_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Sign in')]"))
    )
    ActionChains(driver).move_to_element(signin_button).pause(0.3).click().perform()
    time.sleep(3)
    take_screenshot(driver, "05_login_page_after_click")
    
    return "login" not in driver.current_url.lower()

def perform_renewal(driver):
    """执行续期操作，自动处理弹窗验证码"""
    print("\n🔄 开始执行续期操作...")
    take_screenshot(driver, "06_renewal_started")
    
    # 等待续期表格加载
    renewal_table = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.home-renewal-table"))
    )
    renewal_rows = driver.find_elements(By.CSS_SELECTOR, "div.home-renewal-row")
    print(f"📋 找到 {len(renewal_rows)} 个续期项目")
    
    for idx, row in enumerate(renewal_rows):
        status = row.find_element(By.CSS_SELECTOR, "span.home-renewal-status").text.strip()
        model_name = row.find_element(By.CSS_SELECTOR, "strong.home-renewal-name").text.strip()
        
        if "Suspended" in status or "Expired" in status:
            print(f"📦 项目 {model_name} 需要续期")
            driver.execute_script("arguments[0].scrollIntoView(true);", row)
            time.sleep(0.5)
            
            # 点击 Renew 按钮，弹出验证码弹窗
            renew_button = row.find_element(By.CSS_SELECTOR, "button.home-renew-action")
            ActionChains(driver).move_to_element(renew_button).pause(0.3).click().perform()
            time.sleep(2)
            take_screenshot(driver, f"07_renew_button_clicked_{idx+1}")
            
            # 处理续期弹窗里的验证码
            renew_captcha_success = process_captcha_flow(driver, f"08_renew_popup_{idx+1}")
            if renew_captcha_success:
                print(f"✅ {model_name} 续期成功")
                time.sleep(2)
                take_screenshot(driver, f"09_renewal_completed_{idx+1}")
            else:
                print(f"❌ {model_name} 续期失败")
    
    take_screenshot(driver, "10_all_operations_done")
    return True

def main():
    if not USERNAME or not PASSWORD:
        print("❌ 未配置账号密码环境变量")
        sys.exit(1)
    
    ensure_dirs()
    
    # 启动虚拟显示 + 录屏
    subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x16"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ["DISPLAY"] = ":99"
    recording_process = start_ffmpeg_recording()
    
    driver = setup_driver()
    
    try:
        driver.get(LOGIN_URL)
        time.sleep(3)
        take_screenshot(driver, "02_login_page_loaded")
        
        # 登录流程
        login_success = False
        for attempt in range(MAX_RETRIES):
            print(f"\n🔄 登录尝试 {attempt+1}/{MAX_RETRIES}")
            if login(driver):
                login_success = True
                print("🎉 登录成功")
                break
            time.sleep(2)
        
        if not login_success:
            print("❌ 登录失败，达到最大重试次数")
            return False
        
        # 续期流程
        perform_renewal(driver)
        print("\n🎉 所有操作全部完成")
        return True
        
    except Exception as e:
        print(f"❌ 运行出错: {e}")
        take_screenshot(driver, "99_error_occurred")
        return False
    finally:
        driver.quit()
        stop_ffmpeg_recording(recording_process)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
