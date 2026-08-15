#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================
  ACL Cloud 自动登录 + 续期脚本（全流程录屏版）
  版本：v5.0
  功能：自动登录、处理登录页验证码、点击续期按钮、处理续期弹窗验证码
  依赖：selenium, pillow, pytesseract, requests
============================================
"""

import os
import sys
import time
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
# =============================

def ensure_screenshot_dir():
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

def setup_driver():
    """配置并启动 Chrome 浏览器（配合虚拟桌面运行）"""
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

def open_login_page(driver):
    """打开登录页面"""
    print(f"🌐 打开登录页面: {LOGIN_URL}")
    driver.get(LOGIN_URL)
    time.sleep(3)
    take_screenshot(driver, "02_login_page_loaded")
    print("✅ 登录页面已加载")

def login(driver):
    """执行登录操作"""
    print("🔑 输入用户名和密码...")
    
    selectors = [
        "input[name='email']",
        "input[type='email']",
        "input[placeholder*='email' i]",
        "input[name='username']",
        "input[id*='email' i]"
    ]
    
    username_input = None
    for selector in selectors:
        try:
            username_input = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            if username_input:
                break
        except:
            continue
    
    if not username_input:
        raise Exception("无法找到用户名输入框")
    
    password_input = driver.find_element(By.CSS_SELECTOR, "input[name='password'], input[type='password']")
    
    username_input.clear()
    password_input.clear()
    
    for char in USERNAME:
        username_input.send_keys(char)
        time.sleep(0.05)
    
    for char in PASSWORD:
        password_input.send_keys(char)
        time.sleep(0.05)
    
    take_screenshot(driver, "03_credentials_entered")
    print("✅ 用户名和密码已输入")

def process_captcha_flow(driver, flow_name=""):
    """通用验证码处理流程，支持登录页和续期弹窗"""
    print(f"🔄 开始处理{flow_name}人机验证...")
    
    # 点击I am not a robot复选框
    checkbox = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "div.auth-captcha-checkbox, input[type='checkbox'] + label"))
    )
    actions = ActionChains(driver)
    actions.move_to_element(checkbox).pause(0.3).click().perform()
    time.sleep(1.5)
    take_screenshot(driver, f"{flow_name}_captcha_checkbox_clicked")
    
    # 获取提示文字
    prompt_element = WebDriverWait(driver, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.auth-captcha-prompt"))
    )
    strong_text = prompt_element.find_element(By.TAG_NAME, "strong").text
    print(f"📝 验证码提示文字: {strong_text}")
    take_screenshot(driver, f"{flow_name}_captcha_prompt_displayed")
    
    # 获取选项图片
    options_container = WebDriverWait(driver, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.auth-captcha-options"))
    )
    option_buttons = options_container.find_elements(By.CSS_SELECTOR, "button.auth-captcha-option")
    
    options = []
    for idx, button in enumerate(option_buttons):
        img = button.find_element(By.CSS_SELECTOR, "img.auth-captcha-option-img")
        img_src = img.get_attribute("src")
        options.append({
            "index": idx,
            "button": button,
            "img_src": img_src
        })
        print(f"  📍 选项 {idx + 1}: {img_src[:50]}...")
    
    take_screenshot(driver, f"{flow_name}_captcha_options_displayed")
    
    # 识别并点击正确选项
    base_url = driver.current_url.rstrip("/auth/login").rstrip("/")
    for option in options:
        try:
            img_url = option["img_src"]
            if img_url.startswith("/"):
                full_url = base_url + img_url
            else:
                full_url = img_url
            
            response = requests.get(full_url, timeout=10)
            img = Image.open(BytesIO(response.content))
            img = img.convert("L")
            threshold = 128
            img = img.point(lambda x: 255 if x > threshold else 0)
            ocr_text = pytesseract.image_to_string(img, lang='eng', config='--psm 7').strip()
            
            if strong_text.lower().replace(" ", "") in ocr_text.lower().replace(" ", ""):
                print(f"✅ 找到匹配选项: 选项 {option['index'] + 1}")
                actions = ActionChains(driver)
                actions.move_to_element(option["button"]).pause(0.2).click().perform()
                time.sleep(1)
                take_screenshot(driver, f"{flow_name}_option_{option['index']+1}_clicked")
                break
        except Exception as e:
            print(f"  ❌ 选项识别失败: {e}")
    
    # 检查验证结果
    try:
        verified_label = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "span.auth-captcha-label"))
        )
        if "Verified" in verified_label.text:
            print("✅ 人机验证通过！")
            take_screenshot(driver, f"{flow_name}_verification_passed")
            return True
    except:
        pass
    
    print("❌ 人机验证失败")
    take_screenshot(driver, f"{flow_name}_verification_failed")
    return False

def click_signin(driver):
    """点击登录按钮"""
    print("👆 点击 Sign in 按钮...")
    signin_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Sign in')]"))
    )
    actions = ActionChains(driver)
    actions.move_to_element(signin_button).pause(0.3).click().perform()
    time.sleep(2)
    take_screenshot(driver, "09_signin_button_clicked")
    print("✅ 已点击 Sign in 按钮")

def check_login_success(driver):
    """检查登录是否成功"""
    current_url = driver.current_url
    if "login" not in current_url.lower():
        print(f"🎉 登录成功！当前 URL: {current_url}")
        take_screenshot(driver, "10_login_success")
        return True
    else:
        print("⚠️ 登录失败，可能用户名或密码错误")
        take_screenshot(driver, "10_login_failed")
        return False

def perform_renewal(driver):
    """执行续期操作，包含弹窗验证码处理"""
    print("\n🔄 开始执行续期操作...")
    take_screenshot(driver, "11_renewal_started")
    
    try:
        # 等待续期表格加载
        renewal_table = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.home-renewal-table"))
        )
        print("✅ 续期表格已加载")
        take_screenshot(driver, "12_renewal_table_loaded")
        
        renewal_rows = driver.find_elements(By.CSS_SELECTOR, "div.home-renewal-row")
        print(f"📋 找到 {len(renewal_rows)} 个续期项目")
        
        for idx, row in enumerate(renewal_rows):
            status_element = row.find_element(By.CSS_SELECTOR, "span.home-renewal-status")
            status = status_element.text.strip()
            model_name = row.find_element(By.CSS_SELECTOR, "strong.home-renewal-name").text.strip()
            renewal_date = row.find_element(By.CSS_SELECTOR, "strong.home-renewal-date-main").text.strip()
            
            print(f"\n📦 项目 {idx + 1}: {model_name}")
            print(f"  📅 续期日期: {renewal_date}")
            print(f"  📊 状态: {status}")
            
            if "Suspended" in status or "Expired" in status:
                print(f"  ⚠️ 需要续期！")
                driver.execute_script("arguments.scrollIntoView(true);", row)
                time.sleep(0.5)
                take_screenshot(driver, f"14_renewal_row_{idx + 1}_scrolled")
                
                # 点击Renew按钮
                renew_button = row.find_element(By.CSS_SELECTOR, "button.home-renew-action")
                actions = ActionChains(driver)
                actions.move_to_element(renew_button).pause(0.3).click().perform()
                print(f"  ✅ 已点击 {model_name} 的 Renew 按钮")
                take_screenshot(driver, f"15_renew_button_clicked_{idx + 1}")
                time.sleep(2)
                
                # 处理续期弹窗的人机验证
                process_captcha_flow(driver, flow_name="renewal_popup")
                time.sleep(2)
                take_screenshot(driver, f"16_after_renew_verification_{idx + 1}")
            else:
                print(f"  ✅ 状态正常，无需续期")
        
        take_screenshot(driver, "19_renewal_completed")
        return True
        
    except Exception as e:
        print(f"❌ 续期操作失败: {e}")
        take_screenshot(driver, "99_renewal_error")
        return False

def main():
    """主函数"""
    if not USERNAME or not PASSWORD:
        print("❌ 错误: 未设置 ACL_USERNAME 或 ACL_PASSWORD 环境变量")
        sys.exit(1)
    
    ensure_screenshot_dir()
    driver = setup_driver()
    base_url = LOGIN_URL.rstrip("/auth/login").rstrip("/")
    
    try:
        open_login_page(driver)
        login(driver)
        time.sleep(1)
        
        # 登录页验证码处理
        login_success = False
        for attempt in range(MAX_RETRIES):
            print(f"\n🔄 登录页验证码尝试 {attempt + 1}/{MAX_RETRIES}")
            if process_captcha_flow(driver, flow_name="login_page"):
                click_signin(driver)
                if check_login_success(driver):
                    login_success = True
                    break
            time.sleep(2)
        
        if not login_success:
            print("❌ 登录失败")
            return False
        
        # 导航到仪表盘执行续期
        if "dashboard" not in driver.current_url.lower():
            driver.get(base_url + "/dashboard")
            time.sleep(2)
            take_screenshot(driver, "21_navigated_to_dashboard")
        
        perform_renewal(driver)
        take_screenshot(driver, "99_script_completed")
        print("\n🎉 所有操作已完成！")
        return True
        
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        take_screenshot(driver, "99_error_occurred")
        return False
    finally:
        driver.quit()

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
