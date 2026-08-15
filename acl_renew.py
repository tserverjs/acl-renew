#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================
  ACL Cloud 自动登录 + 续期脚本（修复版）
  修复：MP4兼容录屏、弹窗关闭、健壮元素查找、多语言状态支持
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
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, TimeoutException, StaleElementReferenceException

# ========== 配置区域 ==========
USERNAME = os.getenv("ACL_USERNAME", "")
PASSWORD = os.getenv("ACL_PASSWORD", "")
LOGIN_URL = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
MAX_RETRIES = 3
SCREENSHOT_DIR = "screenshots"
RECORDING_FILE = "full_operation_recording.mp4"  # 改为 MP4，兼容性更好
# =============================

def ensure_screenshot_dir():
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)
        print(f"📁 创建截图目录: {SCREENSHOT_DIR}")

def take_screenshot(driver, step_name):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    filename = f"{SCREENSHOT_DIR}/{timestamp}_{step_name}.png"
    try:
        driver.save_screenshot(filename)
        print(f"📸 截图已保存: {filename}")
    except Exception as e:
        print(f"⚠️ 截图保存失败: {e}")
    return filename

def start_ffmpeg_recording():
    """使用 H.264 + AAC 生成 MP4，全平台兼容"""
    print("🎥 启动 ffmpeg MP4 全程录屏...")
    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "x11grab",
        "-video_size", "1920x1080",
        "-i", ":99",
        "-r", "15",
        "-pix_fmt", "yuv420p",          # 确保兼容性
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-movflags", "+faststart",       # 优化网络播放
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        RECORDING_FILE
    ]
    process = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    print(f"✅ MP4 录屏已启动，输出文件: {RECORDING_FILE}")
    return process

def stop_ffmpeg_recording(process):
    print("⏹️  停止 ffmpeg 录屏...")
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    print(f"✅ MP4 录屏已保存完成: {RECORDING_FILE}")

def setup_driver():
    print("🚀 启动浏览器...")
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--disable-popup-blocking")
    
    chrome_options.binary_location = "/usr/bin/chromium-browser"
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    driver.set_page_load_timeout(30)
    
    take_screenshot(driver, "01_browser_started")
    print("✅ 浏览器启动完成")
    return driver

def close_install_popup(driver):
    """关闭右下角的安装弹窗"""
    try:
        # 尝试多种选择器关闭弹窗
        selectors = [
            "//button[contains(text(), 'Fermer')]",
            "//button[contains(text(), 'Close')]",
            "//div[contains(@class, 'pwa-install')]//button[1]",
            "//div[contains(@class, 'install-popup')]//button[contains(@class, 'close')]"
        ]
        for selector in selectors:
            try:
                btn = driver.find_element(By.XPATH, selector)
                driver.execute_script("arguments[0].click();", btn)
                print("✅ 已关闭安装弹窗")
                time.sleep(0.5)
                return True
            except:
                continue
    except Exception as e:
        print(f"ℹ️ 无需关闭弹窗或关闭失败: {e}")
    return False

def open_login_page(driver):
    print(f"🌐 打开登录页面: {LOGIN_URL}")
    driver.get(LOGIN_URL)
    time.sleep(3)
    take_screenshot(driver, "02_login_page_loaded")
    print("✅ 登录页面已加载")

def login(driver):
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
            if username_input and username_input.is_displayed():
                break
        except:
            continue
    
    if not username_input:
        raise Exception("无法找到用户名输入框")
    
    try:
        password_input = driver.find_element(By.CSS_SELECTOR, "input[name='password'], input[type='password']")
    except NoSuchElementException:
        raise Exception("无法找到密码输入框")
    
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
    print(f"🔄 开始处理{flow_name}人机验证...")
    
    try:
        checkbox = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "div.auth-captcha-checkbox, input[type='checkbox'] + label, .captcha-checkbox"))
        )
        actions = ActionChains(driver)
        actions.move_to_element(checkbox).pause(0.3).click().perform()
        time.sleep(1.5)
        take_screenshot(driver, f"{flow_name}_captcha_checkbox_clicked")
    except Exception as e:
        print(f"⚠️ 点击复选框失败: {e}")
        return False
    
    try:
        prompt_element = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.auth-captcha-prompt, .captcha-prompt"))
        )
        strong_text = prompt_element.find_element(By.TAG_NAME, "strong").text
        print(f"📝 验证码提示文字: {strong_text}")
        take_screenshot(driver, f"{flow_name}_captcha_prompt_displayed")
    except Exception as e:
        print(f"⚠️ 获取提示文字失败: {e}")
        return False
    
    try:
        options_container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.auth-captcha-options, .captcha-options"))
        )
        option_buttons = options_container.find_elements(By.CSS_SELECTOR, "button.auth-captcha-option, .captcha-option")
    except Exception as e:
        print(f"⚠️ 获取选项失败: {e}")
        return False
    
    options = []
    for idx, button in enumerate(option_buttons):
        try:
            img = button.find_element(By.CSS_SELECTOR, "img.auth-captcha-option-img, img")
            img_src = img.get_attribute("src")
            options.append({"index": idx, "button": button, "img_src": img_src})
            print(f"  📍 选项 {idx + 1}: {img_src[:50]}...")
        except:
            continue
    
    take_screenshot(driver, f"{flow_name}_captcha_options_displayed")
    
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
            
            target = strong_text.lower().replace(" ", "").replace("-", "")
            ocr_clean = ocr_text.lower().replace(" ", "").replace("-", "")
            
            if target in ocr_clean or ocr_clean in target:
                print(f"✅ 找到匹配选项: 选项 {option['index'] + 1} (OCR: {ocr_text})")
                actions = ActionChains(driver)
                actions.move_to_element(option["button"]).pause(0.2).click().perform()
                time.sleep(1)
                take_screenshot(driver, f"{flow_name}_option_{option['index']+1}_clicked")
                break
        except Exception as e:
            print(f"  ❌ 选项识别失败: {e}")
    
    try:
        verified_label = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "span.auth-captcha-label, .captcha-label"))
        )
        if "Verified" in verified_label.text or "Vérifié" in verified_label.text:
            print("✅ 人机验证通过！")
            take_screenshot(driver, f"{flow_name}_verification_passed")
            return True
    except:
        pass
    
    print("❌ 人机验证失败")
    take_screenshot(driver, f"{flow_name}_verification_failed")
    return False

def click_signin(driver):
    print("👆 点击 Sign in 按钮...")
    try:
        signin_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Sign in')]"))
        )
        actions = ActionChains(driver)
        actions.move_to_element(signin_button).pause(0.3).click().perform()
        time.sleep(2)
        take_screenshot(driver, "09_signin_button_clicked")
        print("✅ 已点击 Sign in 按钮")
    except Exception as e:
        print(f"❌ 点击 Sign in 失败: {e}")
        raise

def check_login_success(driver):
    current_url = driver.current_url
    if "login" not in current_url.lower():
        print(f"🎉 登录成功！当前 URL: {current_url}")
        take_screenshot(driver, "10_login_success")
        return True
    else:
        print("⚠️ 登录失败，可能用户名或密码错误")
        take_screenshot(driver, "10_login_failed")
        return False

def needs_renewal(status_text):
    """判断是否需要续期，支持多语言"""
    status_lower = status_text.lower()
    keywords = [
        "suspended", "expired", "suspendu", "expiré", "terminé",
        "inactive", "inactif", "ended", "non actif"
    ]
    return any(kw in status_lower for kw in keywords)

def safe_find_text(element, selector, default=""):
    """安全获取元素文本"""
    try:
        el = element.find_element(By.CSS_SELECTOR, selector)
        return el.text.strip()
    except NoSuchElementException:
        return default

def perform_renewal(driver):
    print("\n🔄 开始执行续期操作...")
    take_screenshot(driver, "11_renewal_started")
    
    # 先关闭可能遮挡的安装弹窗
    close_install_popup(driver)
    take_screenshot(driver, "11b_popup_closed")
    
    try:
        # 等待续期表格加载
        renewal_table = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.home-renewal-table, .renewal-table, [class*='renewal']"))
        )
        print("✅ 续期表格已加载")
        take_screenshot(driver, "12_renewal_table_loaded")
        
        # 滚动到表格区域确保可见
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", renewal_table)
        time.sleep(1)
        
        # 尝试多种选择器查找行
        row_selectors = [
            "div.home-renewal-row",
            ".renewal-row",
            "tr[class*='renewal']",
            "div[class*='renewal-row']",
            ".home-renewal-table > div > div"  # 可能的嵌套结构
        ]
        
        renewal_rows = []
        for selector in row_selectors:
            rows = driver.find_elements(By.CSS_SELECTOR, selector)
            if len(rows) > 0:
                renewal_rows = rows
                print(f"📋 使用选择器 '{selector}' 找到 {len(rows)} 个续期项目")
                break
        
        if not renewal_rows:
            print("⚠️ 未找到续期项目行，可能表格为空或选择器不匹配")
            take_screenshot(driver, "12b_no_renewal_rows")
            return True  # 没有需要续期的项目，不算失败
        
        for idx, row in enumerate(renewal_rows):
            try:
                # 健壮地获取各字段，支持多种选择器
                status = safe_find_text(row, "span.home-renewal-status, .renewal-status, td:nth-child(4), .status")
                model_name = safe_find_text(row, "strong.home-renewal-name, .renewal-name, td:nth-child(2), .model")
                renewal_date = safe_find_text(row, "strong.home-renewal-date-main, .renewal-date, td:nth-child(3), .date")
                
                print(f"\n📦 项目 {idx + 1}: {model_name or '未知'}")
                print(f"  📅 续期日期: {renewal_date or '未知'}")
                print(f"  📊 状态: {status or '未知'}")
                
                if not status:
                    print(f"  ⚠️ 无法获取状态，跳过")
                    continue
                
                if needs_renewal(status):
                    print(f"  ⚠️ 需要续期！")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row)
                    time.sleep(0.5)
                    take_screenshot(driver, f"14_renewal_row_{idx + 1}_scrolled")
                    
                    # 尝试多种 Renew 按钮选择器
                    renew_selectors = [
                        "button.home-renew-action",
                        ".renew-action",
                        "button[class*='renew']",
                        "td:last-child button",
                        ".actions button"
                    ]
                    
                    renew_button = None
                    for rsel in renew_selectors:
                        try:
                            renew_button = row.find_element(By.CSS_SELECTOR, rsel)
                            if renew_button and renew_button.is_displayed():
                                print(f"  ✅ 找到 Renew 按钮 (选择器: {rsel})")
                                break
                        except:
                            continue
                    
                    if not renew_button:
                        print(f"  ❌ 未找到 Renew 按钮")
                        continue
                    
                    # 使用 JavaScript 点击，更可靠
                    driver.execute_script("arguments[0].click();", renew_button)
                    print(f"  ✅ 已点击 {model_name or '项目'} 的 Renew 按钮")
                    take_screenshot(driver, f"15_renew_button_clicked_{idx + 1}")
                    time.sleep(2)
                    
                    # 处理续期弹窗的人机验证
                    process_captcha_flow(driver, flow_name="renewal_popup")
                    time.sleep(2)
                    take_screenshot(driver, f"16_after_renew_verification_{idx + 1}")
                    
                    # 再次关闭可能出现的弹窗
                    close_install_popup(driver)
                else:
                    print(f"  ✅ 状态正常，无需续期")
                    
            except StaleElementReferenceException:
                print(f"  ⚠️ 项目 {idx + 1} 元素已过期，跳过")
                continue
            except Exception as e:
                print(f"  ❌ 处理项目 {idx + 1} 时出错: {e}")
                take_screenshot(driver, f"13_renewal_row_{idx + 1}_error")
                continue
        
        take_screenshot(driver, "19_renewal_completed")
        return True
        
    except TimeoutException:
        print("❌ 续期表格加载超时")
        take_screenshot(driver, "99_renewal_timeout")
        return False
    except Exception as e:
        print(f"❌ 续期操作失败: {e}")
        take_screenshot(driver, "99_renewal_error")
        return False

def main():
    if not USERNAME or not PASSWORD:
        print("❌ 错误: 未设置 ACL_USERNAME 或 ACL_PASSWORD 环境变量")
        sys.exit(1)
    
    ensure_screenshot_dir()
    
    # 启动虚拟桌面
    subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x24"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ["DISPLAY"] = ":99"
    time.sleep(2)
    
    recording_process = start_ffmpeg_recording()
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
        
        # 导航到仪表盘
        if "dashboard" not in driver.current_url.lower():
            driver.get(base_url + "/dashboard")
            time.sleep(3)
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
        stop_ffmpeg_recording(recording_process)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
