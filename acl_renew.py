#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================
  ACL Cloud 自动登录 + 续期脚本（全步骤截图版）
  版本：v4.1
  功能：自动登录 ACL Cloud，处理验证码，执行续期，每一步都截图
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
    """配置并启动 Chrome 浏览器（无头模式）"""
    print("🚀 启动浏览器...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
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
    
    # 查找用户名输入框
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
    
    # 清空输入框
    username_input.clear()
    password_input.clear()
    
    # 模拟人类输入
    for char in USERNAME:
        username_input.send_keys(char)
        time.sleep(0.05)
    
    for char in PASSWORD:
        password_input.send_keys(char)
        time.sleep(0.05)
    
    take_screenshot(driver, "03_credentials_entered")
    print("✅ 用户名和密码已输入")

def click_checkbox(driver):
    """点击验证码复选框"""
    print("🔄 点击验证码复选框...")
    checkbox = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "div.auth-captcha-checkbox"))
    )
    actions = ActionChains(driver)
    actions.move_to_element(checkbox).pause(0.3).click().perform()
    time.sleep(1.5)
    take_screenshot(driver, "04_captcha_checkbox_clicked")
    print("✅ 复选框已点击")

def get_prompt_text(driver):
    """获取验证码提示文字"""
    print("🔍 获取验证码提示文字...")
    prompt_element = WebDriverWait(driver, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.auth-captcha-prompt"))
    )
    strong_text = prompt_element.find_element(By.TAG_NAME, "strong").text
    print(f"📝 提示文字: {strong_text}")
    take_screenshot(driver, "05_captcha_prompt_displayed")
    return strong_text

def get_option_images(driver):
    """获取所有验证码选项的图片元素和 src 地址"""
    print("🖼️ 获取验证码选项图片...")
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
    
    take_screenshot(driver, "06_captcha_options_displayed")
    return options

def download_and_ocr_image(img_url, base_url, option_index):
    """下载图片并进行 OCR 识别"""
    try:
        if img_url.startswith("/"):
            full_url = base_url + img_url
        else:
            full_url = img_url
        
        print(f"  ⬇️ 下载选项 {option_index + 1} 图片: {full_url}")
        response = requests.get(full_url, timeout=10)
        img = Image.open(BytesIO(response.content))
        
        # 保存原始图片
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        img_path = f"{SCREENSHOT_DIR}/{timestamp}_option_{option_index + 1}_original.png"
        img.save(img_path)
        print(f"  📸 选项图片已保存: {img_path}")
        
        # 图片预处理
        img = img.convert("L")
        threshold = 128
        img = img.point(lambda x: 255 if x > threshold else 0)
        
        # 保存预处理后的图片
        processed_path = f"{SCREENSHOT_DIR}/{timestamp}_option_{option_index + 1}_processed.png"
        img.save(processed_path)
        print(f"  📸 预处理后图片已保存: {processed_path}")
        
        text = pytesseract.image_to_string(img, lang='eng', config='--psm 7').strip()
        print(f"  📝 OCR 识别结果: '{text}'")
        return text
    except Exception as e:
        print(f"  ❌ OCR 识别失败: {e}")
        return ""

def click_correct_option(driver, options, target_text, base_url):
    """根据提示文字点击正确的选项"""
    print(f"🎯 寻找包含 '{target_text}' 的选项...")
    
    for option in options:
        ocr_text = download_and_ocr_image(option["img_src"], base_url, option["index"])
        
        if target_text.lower().replace(" ", "") in ocr_text.lower().replace(" ", ""):
            print(f"✅ 找到匹配选项: 选项 {option['index'] + 1}")
            actions = ActionChains(driver)
            actions.move_to_element(option["button"]).pause(0.2).click().perform()
            time.sleep(1)
            take_screenshot(driver, f"07_option_{option['index'] + 1}_clicked")
            return True
    
    print("❌ 未找到匹配选项")
    take_screenshot(driver, "07_no_matching_option_found")
    return False

def check_verification(driver):
    """检查验证是否通过"""
    try:
        verified_label = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "span.auth-captcha-label"))
        )
        if "Verified" in verified_label.text:
            print("✅ 验证码验证通过！")
            take_screenshot(driver, "08_verification_passed")
            return True
    except:
        pass
    print("❌ 验证码验证未通过")
    take_screenshot(driver, "08_verification_failed")
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
    """执行续期操作"""
    print("\n🔄 开始执行续期操作...")
    take_screenshot(driver, "11_renewal_started")
    
    try:
        # 等待续期表格加载
        renewal_table = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.home-renewal-table"))
        )
        print("✅ 续期表格已加载")
        take_screenshot(driver, "12_renewal_table_loaded")
        
        # 查找所有续期行
        renewal_rows = driver.find_elements(By.CSS_SELECTOR, "div.home-renewal-row")
        print(f"📋 找到 {len(renewal_rows)} 个续期项目")
        take_screenshot(driver, "13_renewal_rows_found")
        
        renewal_clicked = False
        
        for idx, row in enumerate(renewal_rows):
            # 获取状态
            status_element = row.find_element(By.CSS_SELECTOR, "span.home-renewal-status")
            status = status_element.text.strip()
            
            # 获取模型名称
            model_name = row.find_element(By.CSS_SELECTOR, "strong.home-renewal-name").text.strip()
            
            # 获取续期日期
            date_element = row.find_element(By.CSS_SELECTOR, "strong.home-renewal-date-main")
            renewal_date = date_element.text.strip()
            
            print(f"\n📦 项目 {idx + 1}: {model_name}")
            print(f"  📅 续期日期: {renewal_date}")
            print(f"  📊 状态: {status}")
            
            # 检查是否需要续期
            if "Suspended" in status or "Expired" in status:
                print(f"  ⚠️ 需要续期！")
                
                # 滚动到该行
                driver.execute_script("arguments.scrollIntoView(true);", row)
                time.sleep(0.5)
                take_screenshot(driver, f"14_renewal_row_{idx + 1}_scrolled")
                
                # 查找并点击 Renew 按钮
                renew_button = row.find_element(By.CSS_SELECTOR, "button.home-renew-action")
                
                actions = ActionChains(driver)
                actions.move_to_element(renew_button).pause(0.3).click().perform()
                print(f"  ✅ 已点击 {model_name} 的 Renew 按钮")
                take_screenshot(driver, f"15_renew_button_clicked_{idx + 1}")
                renewal_clicked = True
                
                # 等待续期确认弹窗或页面跳转
                time.sleep(2)
                take_screenshot(driver, f"16_after_renew_click_{idx + 1}")
                
                # 检查是否有确认弹窗
                try:
                    confirm_button = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Confirm') or contains(text(), '确认')]"))
                    )
                    confirm_button.click()
                    print("  ✅ 已确认续期")
                    time.sleep(1)
                    take_screenshot(driver, f"17_renewal_confirmed_{idx + 1}")
                except:
                    print("  ℹ️ 无需确认，续期已直接执行")
            else:
                print(f"  ✅ 状态正常，无需续期")
        
        if not renewal_clicked:
            print("\nℹ️ 没有需要续期的项目")
            take_screenshot(driver, "18_no_renewal_needed")
        else:
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
    
    # 创建截图目录
    ensure_screenshot_dir()
    
    driver = setup_driver()
    base_url = LOGIN_URL.rstrip("/auth/login").rstrip("/")
    
    try:
        # 1. 打开登录页面
        open_login_page(driver)
        
        # 2. 输入用户名和密码
        login(driver)
        time.sleep(1)
        
        # 3. 验证码重试逻辑
        login_success = False
        for attempt in range(MAX_RETRIES):
            print(f"\n🔄 验证码尝试 {attempt + 1}/{MAX_RETRIES}")
            take_screenshot(driver, f"20_captcha_attempt_{attempt + 1}_start")
            
            click_checkbox(driver)
            time.sleep(1)
            
            prompt_text = get_prompt_text(driver)
            options = get_option_images(driver)
            
            success = click_correct_option(driver, options, prompt_text, base_url)
            
            if success and check_verification(driver):
                click_signin(driver)
                
                if check_login_success(driver):
                    login_success = True
                    break
                else:
                    driver.save_screenshot(f"{SCREENSHOT_DIR}/login_failed_attempt_{attempt + 1}.png")
                    return False
            
            time.sleep(2)
        
        if not login_success:
            print("❌ 达到最大重试次数，登录失败")
            take_screenshot(driver, "99_login_max_retries_reached")
            return False
        
        # 4. 登录成功后执行续期操作
        print("\n" + "="*50)
        print("  续期操作")
        print("="*50)
        
        if "dashboard" in driver.current_url.lower():
            perform_renewal(driver)
        else:
            print("⚠️ 未在仪表盘页面，尝试导航...")
            driver.get(base_url + "/dashboard")
            time.sleep(2)
            take_screenshot(driver, "21_navigated_to_dashboard")
            perform_renewal(driver)
        
        # 最终截图
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
