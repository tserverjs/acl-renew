import os
import sys
import time
import requests
from io import BytesIO
from PIL import Image
import pytesseract
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains

# ========== 配置区域 ==========
USERNAME = os.getenv("ACL_USERNAME", "")
PASSWORD = os.getenv("ACL_PASSWORD", "")
LOGIN_URL = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
MAX_RETRIES = 3  # 验证码最大重试次数
# =============================

def setup_driver():
    """配置并启动 Chrome 浏览器（无头模式）"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    # 设置 Chrome 二进制路径（GitHub Actions 环境）
    chrome_options.binary_location = "/usr/bin/chromium-browser"
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def click_checkbox(driver):
    """点击验证码复选框"""
    print("🔄 点击验证码复选框...")
    checkbox = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "div.auth-captcha-checkbox"))
    )
    # 使用 ActionChains 模拟人类点击
    actions = ActionChains(driver)
    actions.move_to_element(checkbox).pause(0.3).click().perform()
    time.sleep(1.5)  # 等待验证码弹窗出现
    print("✅ 复选框已点击")

def get_prompt_text(driver):
    """获取验证码提示文字"""
    print("🔍 获取验证码提示文字...")
    prompt_element = WebDriverWait(driver, 5).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.auth-captcha-prompt"))
    )
    # 提取 <strong> 标签内的文字
    strong_text = prompt_element.find_element(By.TAG_NAME, "strong").text
    print(f"📝 提示文字: {strong_text}")
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
    
    return options

def download_and_ocr_image(img_url, base_url):
    """下载图片并进行 OCR 识别"""
    try:
        # 处理相对路径
        if img_url.startswith("/"):
            full_url = base_url + img_url
        else:
            full_url = img_url
        
        print(f"  ⬇️ 下载图片: {full_url}")
        response = requests.get(full_url, timeout=10)
        img = Image.open(BytesIO(response.content))
        
        # 图片预处理：灰度化 + 二值化，提高 OCR 识别率
        img = img.convert("L")  # 灰度化
        threshold = 128
        img = img.point(lambda x: 255 if x > threshold else 0)  # 二值化
        
        # 使用 pytesseract 进行 OCR 识别
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
        ocr_text = download_and_ocr_image(option["img_src"], base_url)
        
        # 比较 OCR 结果与目标文字（忽略大小写和空格）
        if target_text.lower().replace(" ", "") in ocr_text.lower().replace(" ", ""):
            print(f"✅ 找到匹配选项: 选项 {option['index'] + 1}")
            # 模拟人类点击
            actions = ActionChains(driver)
            actions.move_to_element(option["button"]).pause(0.2).click().perform()
            time.sleep(1)
            return True
    
    print("❌ 未找到匹配选项")
    return False

def check_verification(driver):
    """检查验证是否通过"""
    try:
        verified_label = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "span.auth-captcha-label"))
        )
        if "Verified" in verified_label.text:
            print("✅ 验证码验证通过！")
            return True
    except:
        pass
    print("❌ 验证码验证未通过")
    return False

def login(driver):
    """执行登录操作"""
    print("🔑 输入用户名和密码...")
    # 尝试多种选择器
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
    
    print("✅ 用户名和密码已输入")

def click_signin(driver):
    """点击登录按钮"""
    print("👆 点击 Sign in 按钮...")
    signin_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Sign in')]"))
    )
    actions = ActionChains(driver)
    actions.move_to_element(signin_button).pause(0.3).click().perform()
    print("✅ 已点击 Sign in 按钮")

def main():
    """主函数"""
    # 检查必要环境变量
    if not USERNAME or not PASSWORD:
        print("❌ 错误: 未设置 ACL_USERNAME 或 ACL_PASSWORD 环境变量")
        sys.exit(1)
    
    driver = setup_driver()
    base_url = LOGIN_URL.rstrip("/auth/login").rstrip("/")
    
    try:
        print(f"🌐 打开登录页面: {LOGIN_URL}")
        driver.get(LOGIN_URL)
        time.sleep(3)
        
        # 1. 输入用户名和密码
        login(driver)
        time.sleep(1)
        
        # 验证码重试逻辑
        for attempt in range(MAX_RETRIES):
            print(f"\n🔄 验证码尝试 {attempt + 1}/{MAX_RETRIES}")
            
            # 2. 点击验证码复选框
            click_checkbox(driver)
            time.sleep(1)
            
            # 3. 获取提示文字
            prompt_text = get_prompt_text(driver)
            
            # 4. 获取选项图片
            options = get_option_images(driver)
            
            # 5. 识别并点击正确选项
            success = click_correct_option(driver, options, prompt_text, base_url)
            
            if success:
                # 6. 检查验证结果
                if check_verification(driver):
                    # 7. 点击登录
                    click_signin(driver)
                    time.sleep(3)
                    
                    # 检查是否登录成功
                    current_url = driver.current_url
                    if "login" not in current_url.lower():
                        print(f"🎉 登录成功！当前 URL: {current_url}")
                        return True
                    else:
                        print("⚠️ 登录失败，可能用户名或密码错误")
                        driver.save_screenshot("login_failed.png")
                        return False
                else:
                    print("⚠️ 验证码验证失败，准备重试...")
            else:
                print("⚠️ 未能找到匹配选项，准备重试...")
            
            # 重试前等待
            time.sleep(2)
        
        print("❌ 达到最大重试次数，登录失败")
        driver.save_screenshot("error_screenshot.png")
        return False
        
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        driver.save_screenshot("error_screenshot.png")
        print("📸 错误截图已保存")
        return False
    finally:
        driver.quit()

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
