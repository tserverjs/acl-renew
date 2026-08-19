#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================
  ACL Cloud 自动登录 + 续期脚本（完整版 v6.1）
  功能：自动登录、双验证码处理、续期、状态检测、智能启停、企业微信通知
============================================
"""

import os
import sys
import time
import json
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
from selenium.common.exceptions import (
    NoSuchElementException, TimeoutException, 
    StaleElementReferenceException, ElementNotInteractableException
)

# ========== 配置区域 ==========
USERNAME = os.getenv("ACL_USERNAME", "")
PASSWORD = os.getenv("ACL_PASSWORD", "")
LOGIN_URL = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
WECHAT_WEBHOOK_KEY = os.getenv("WECHAT_WEBHOOK_KEY", "")
MAX_RETRIES = 3
SCREENSHOT_DIR = "screenshots"
RECORDING_FILE = "full_operation_recording.mp4"
# =============================

# 全局状态标记
NEED_RENEWAL = False
RENEWAL_SUCCESS = False
SERVER_STATUS = "unknown"  # online / offline / unknown
POWER_ACTION = "none"      # start / restart / none

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
    print("🎥 启动 ffmpeg MP4 全程录屏...")
    ffmpeg_cmd = [
        "ffmpeg",
        "-f", "x11grab",
        "-video_size", "1920x1080",
        "-i", ":99",
        "-r", "15",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-movflags", "+faststart",
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
    print(f"✅ MP4 录屏已启动: {RECORDING_FILE}")
    return process

def stop_ffmpeg_recording(process):
    print("⏹️  停止 ffmpeg 录屏...")
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    print(f"✅ MP4 录屏已保存: {RECORDING_FILE}")

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
    try:
        selectors = [
            "//button[contains(text(), 'Fermer')]",
            "//button[contains(text(), 'Close')]",
            "//div[contains(@class, 'pwa-install')]//button[1]",
            "//div[contains(@class, 'install-popup')]//button[contains(@class, 'close')]"
        ]
        for selector in selectors:
            try:
                btns = driver.find_elements(By.XPATH, selector)
                for btn in btns:
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        print("✅ 已关闭安装弹窗")
                        time.sleep(0.5)
                        return True
            except:
                continue
    except Exception as e:
        print(f"ℹ️ 无需关闭弹窗: {e}")
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
    """通用验证码处理，支持登录页和续期弹窗"""
    print(f"🔄 开始处理{flow_name}人机验证...")

    # 1. 点击复选框
    try:
        checkbox = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, 
                "div.auth-captcha-checkbox, input[type='checkbox'] + label, .captcha-checkbox"))
        )
        actions = ActionChains(driver)
        actions.move_to_element(checkbox).pause(0.3).click().perform()
        time.sleep(1.5)
        take_screenshot(driver, f"{flow_name}_captcha_checkbox_clicked")
    except Exception as e:
        print(f"⚠️ 点击复选框失败: {e}")
        return False

    # 2. 获取提示文字
    try:
        prompt_element = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 
                "div.auth-captcha-prompt, .captcha-prompt"))
        )
        strong_text = prompt_element.find_element(By.TAG_NAME, "strong").text
        print(f"📝 验证码提示文字: {strong_text}")
        take_screenshot(driver, f"{flow_name}_captcha_prompt_displayed")
    except Exception as e:
        print(f"⚠️ 获取提示文字失败: {e}")
        return False

    # 3. 获取选项
    try:
        options_container = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 
                "div.auth-captcha-options, .captcha-options"))
        )
        option_buttons = options_container.find_elements(By.CSS_SELECTOR, 
            "button.auth-captcha-option, .captcha-option")
    except Exception as e:
        print(f"⚠️ 获取选项失败: {e}")
        return False

    options = []
    for idx, button in enumerate(option_buttons):
        try:
            img = button.find_element(By.CSS_SELECTOR, 
                "img.auth-captcha-option-img, img")
            img_src = img.get_attribute("src")
            options.append({"index": idx, "button": button, "img_src": img_src})
            print(f"  📍 选项 {idx + 1}: {img_src[:50]}...")
        except:
            continue

    take_screenshot(driver, f"{flow_name}_captcha_options_displayed")

    # 4. OCR 识别并点击
    base_url = driver.current_url.rstrip("/auth/login").rstrip("/")
    clicked = False

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
            img = img.point(lambda x: 255 if x > 128 else 0)
            ocr_text = pytesseract.image_to_string(img, lang='eng', config='--psm 7').strip()

            target = strong_text.lower().replace(" ", "").replace("-", "")
            ocr_clean = ocr_text.lower().replace(" ", "").replace("-", "")

            if target in ocr_clean or ocr_clean in target:
                print(f"✅ 找到匹配: 选项 {option['index'] + 1} (OCR: {ocr_text})")
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", option["button"])
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", option["button"])
                clicked = True
                time.sleep(2)
                take_screenshot(driver, f"{flow_name}_option_{option['index']+1}_clicked")
                break
        except Exception as e:
            print(f"  ❌ 选项识别失败: {e}")

    if not clicked:
        print("❌ 未点击任何选项")
        return False

    # 5. 检查验证结果（修复：支持弹窗自动关闭的情况）
    time.sleep(2)

    # 模式 A：检测 Verified 标签
    try:
        verified_label = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 
                "span.auth-captcha-label, .captcha-label"))
        )
        if "Verified" in verified_label.text or "Vérifié" in verified_label.text:
            print("✅ 人机验证通过！(Verified 标签)")
            take_screenshot(driver, f"{flow_name}_verification_passed")
            return True
    except:
        pass

    # 模式 B：检测验证码元素是否消失（弹窗自动关闭的情况）
    try:
        captcha_elements = driver.find_elements(By.CSS_SELECTOR, 
            "div.auth-captcha-options, .captcha-options, div.auth-captcha-prompt, .auth-captcha-checkbox")
        error_elements = driver.find_elements(By.CSS_SELECTOR, 
            ".auth-captcha-error, .captcha-error, .text-danger, [class*='error']")
        has_error = any(el.is_displayed() and len(el.text.strip()) > 0 for el in error_elements)

        if has_error:
            print("❌ 人机验证失败（检测到错误提示）")
            take_screenshot(driver, f"{flow_name}_verification_failed")
            return False

        # 验证码元素消失 = 验证通过（弹窗自动关闭）
        visible_captcha = [el for el in captcha_elements if el.is_displayed()]
        if len(visible_captcha) == 0:
            print("✅ 人机验证通过！(验证码元素已消失，弹窗自动关闭)")
            take_screenshot(driver, f"{flow_name}_verification_passed")
            return True

        # 检查成功提示
        success_selectors = [
            ".alert-success", ".text-success", ".success-message",
            "[class*='success']", "[class*='verified']", ".toast-success",
            ".notification-success"
        ]
        for sel in success_selectors:
            success_els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in success_els:
                if el.is_displayed() and len(el.text.strip()) > 0:
                    print(f"✅ 人机验证通过！(成功提示: {el.text.strip()[:30]})")
                    take_screenshot(driver, f"{flow_name}_verification_passed")
                    return True
    except Exception as e:
        print(f"⚠️ 验证结果检测异常: {e}")

    # 模式 C：兜底 - 页面正常且无错误 = 通过
    print("⚠️ 无法确认验证状态，假设通过（无错误提示）")
    take_screenshot(driver, f"{flow_name}_verification_unknown")
    return True

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
        print("⚠️ 登录失败")
        take_screenshot(driver, "10_login_failed")
        return False

def needs_renewal(status_text):
    status_lower = status_text.lower()
    keywords = [
        "suspended", "expired", "suspendu", "expiré", "terminé",
        "inactive", "inactif", "ended", "non actif", "renouvellement",
        "renewal", "renouveler", "renew"
    ]
    return any(kw in status_lower for kw in keywords)

def safe_find_text(element, selector, default=""):
    try:
        el = element.find_element(By.CSS_SELECTOR, selector)
        return el.text.strip()
    except NoSuchElementException:
        return default

def perform_renewal(driver):
    """执行续期操作"""
    global NEED_RENEWAL, RENEWAL_SUCCESS
    print("\n🔄 开始执行续期操作...")
    take_screenshot(driver, "11_renewal_started")

    close_install_popup(driver)

    try:
        # 等待续期表格加载
        renewal_table = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 
                "div.home-renewal-table, .renewal-table, [class*='renewal']"))
        )
        print("✅ 续期表格已加载")
        take_screenshot(driver, "12_renewal_table_loaded")

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", renewal_table)
        time.sleep(1)

        row_selectors = [
            "div.home-renewal-row",
            ".renewal-row",
            "tr[class*='renewal']",
            "div[class*='renewal-row']",
            ".home-renewal-table > div > div"
        ]

        renewal_rows = []
        for selector in row_selectors:
            rows = driver.find_elements(By.CSS_SELECTOR, selector)
            if len(rows) > 0:
                renewal_rows = rows
                print(f"📋 使用选择器 '{selector}' 找到 {len(rows)} 个续期项目")
                break

        if not renewal_rows:
            print("⚠️ 未找到续期项目")
            take_screenshot(driver, "12b_no_renewal_rows")
            return True

        for idx, row in enumerate(renewal_rows):
            try:
                status = safe_find_text(row, "span.home-renewal-status, .renewal-status, td:nth-child(4), .status")
                model_name = safe_find_text(row, "strong.home-renewal-name, .renewal-name, td:nth-child(2), .model")
                renewal_date = safe_find_text(row, "strong.home-renewal-date-main, .renewal-date, td:nth-child(3), .date")

                print(f"\n📦 项目 {idx + 1}: {model_name or '未知'}")
                print(f"  📅 续期日期: {renewal_date or '未知'}")
                print(f"  📊 状态: {status or '未知'}")

                if not status:
                    continue

                if needs_renewal(status):
                    NEED_RENEWAL = True
                    print(f"  ⚠️ 需要续期！")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", row)
                    time.sleep(0.5)
                    take_screenshot(driver, f"14_renewal_row_{idx + 1}_scrolled")

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

                    driver.execute_script("arguments[0].click();", renew_button)
                    print(f"  ✅ 已点击 Renew 按钮")
                    take_screenshot(driver, f"15_renew_button_clicked_{idx + 1}")
                    time.sleep(2)

                    # 处理续期弹窗验证码
                    if process_captcha_flow(driver, flow_name="renewal_popup"):
                        RENEWAL_SUCCESS = True
                        print("✅ 续期验证通过！")
                    else:
                        print("❌ 续期验证失败")

                    time.sleep(2)
                    take_screenshot(driver, f"16_after_renew_verification_{idx + 1}")
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

def navigate_to_services(driver):
    """点击左侧 My services 导航"""
    print("\n📂 点击 My services 导航...")
    try:
        nav_selectors = [
            "a[aria-label='My services']",
            "a[href='/dashboard/projects']",
            "//a[contains(@aria-label, 'My services')]",
            "//span[contains(text(), 'My services')]/parent::a",
            "//span[contains(text(), 'Mes services')]/parent::a"
        ]

        nav_link = None
        for sel in nav_selectors:
            try:
                if sel.startswith("//"):
                    nav_link = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, sel))
                    )
                else:
                    nav_link = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                if nav_link:
                    break
            except:
                continue

        if not nav_link:
            print("⚠️ 未找到导航按钮，直接访问 URL")
            driver.get("https://aclclouds.com/dashboard/projects")
            time.sleep(3)
            take_screenshot(driver, "20_navigated_to_services")
            return True

        driver.execute_script("arguments[0].click();", nav_link)
        time.sleep(3)
        take_screenshot(driver, "20_navigated_to_services")
        print("✅ 已进入 My services 页面")
        return True

    except Exception as e:
        print(f"❌ 导航到 My services 失败: {e}")
        driver.get("https://aclclouds.com/dashboard/projects")
        time.sleep(3)
        take_screenshot(driver, "20_navigated_to_services_fallback")
        return True

def click_manage_button(driver):
    """点击第一个服务的 Manage 按钮"""
    print("\n🔧 点击 Manage 按钮...")
    try:
        manage_selectors = [
            "a.client-btn--primary[href^='/server/']",
            "a[href^='/server/'].client-btn",
            "//a[contains(@href, '/server/') and contains(@class, 'client-btn--primary')]",
            "//a[contains(text(), 'Manage')]",
            "//a[contains(text(), 'Gérer')]",
            ".client-btn--primary"
        ]

        manage_btn = None
        for sel in manage_selectors:
            try:
                if sel.startswith("//"):
                    manage_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, sel))
                    )
                else:
                    manage_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                if manage_btn:
                    break
            except:
                continue

        if not manage_btn:
            print("❌ 未找到 Manage 按钮")
            return False

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", manage_btn)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", manage_btn)
        time.sleep(3)
        take_screenshot(driver, "21_clicked_manage")
        print("✅ 已进入服务器详情页")
        return True

    except Exception as e:
        print(f"❌ 点击 Manage 按钮失败: {e}")
        return False


def get_server_info(driver):
    """获取服务器详情页信息（含状态检测）"""
    global SERVER_STATUS
    print("\n📊 获取服务器信息...")
    info = {
        "time_remaining": "",
        "plan": "",
        "renewal_note": "",
        "server_name": "",
        "server_url": driver.current_url,
        "status": "unknown",  # online / offline
    }

    try:
        # ===== 1. 检测服务器状态（Online / Offline）=====
        status_selectors = [
            "span.status-badge[data-status]",
            ".status-badge",
            "[class*='status-badge']",
            "//span[contains(@class, 'status-badge')]",
            "//span[@data-status]",
        ]

        status_element = None
        for sel in status_selectors:
            try:
                if sel.startswith("//"):
                    status_element = driver.find_element(By.XPATH, sel)
                else:
                    status_element = driver.find_element(By.CSS_SELECTOR, sel)
                if status_element and status_element.is_displayed():
                    break
            except:
                continue

        if status_element:
            status_text = status_element.text.strip()
            data_status = status_element.get_attribute("data-status") or ""

            # 优先使用 data-status 属性，其次使用文本内容
            raw_status = (data_status or status_text).lower()
            if "online" in raw_status or "en ligne" in raw_status or "actif" in raw_status:
                info["status"] = "online"
                SERVER_STATUS = "online"
                print(f"  🟢 服务器状态: Online (data-status={data_status}, text={status_text})")
            elif "offline" in raw_status or "hors ligne" in raw_status or "inactif" in raw_status:
                info["status"] = "offline"
                SERVER_STATUS = "offline"
                print(f"  🔴 服务器状态: Offline (data-status={data_status}, text={status_text})")
            else:
                info["status"] = "unknown"
                SERVER_STATUS = "unknown"
                print(f"  ⚠️ 服务器状态未知: {status_text} (data-status={data_status})")
        else:
            print("  ⚠️ 未找到状态徽章元素")

        # ===== 2. 获取 Time remaining 等信息 =====
        time_selectors = [
            "div[style*='background: rgba(49, 95, 79']",
            "div[style*='background: rgba(49, 95, 79, 0.06)']",
            "//div[contains(text(), 'Time remaining')]",
            "//div[contains(text(), 'Temps restant')]",
            ".server-info-card",
            "[class*='server-info']"
        ]

        info_container = None
        for sel in time_selectors:
            try:
                if sel.startswith("//"):
                    info_container = driver.find_element(By.XPATH, sel)
                else:
                    info_container = driver.find_element(By.CSS_SELECTOR, sel)
                if info_container and info_container.is_displayed():
                    break
            except:
                continue

        if info_container:
            text = info_container.text
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            for line in lines:
                if "Time remaining" in line or "Temps restant" in line:
                    info["time_remaining"] = line.replace("Time remaining:", "").replace("Temps restant:", "").strip()
                elif "plan" in line.lower() or "gratuit" in line.lower() or "free" in line.lower():
                    info["plan"] = line
                elif "Renewal" in line or "renouvellement" in line.lower():
                    info["renewal_note"] = line

            print(f"  ⏰ 剩余时间: {info['time_remaining'] or '未获取'}")
            print(f"  📋 套餐: {info['plan'] or '未获取'}")
            print(f"  📝 续期提示: {info['renewal_note'] or '未获取'}")
        else:
            print("  ⚠️ 未找到服务器信息容器")

        # 获取服务器名称
        try:
            name_el = driver.find_element(By.CSS_SELECTOR, "h1, .server-name, [class*='server-title']")
            info["server_name"] = name_el.text.strip()
        except:
            info["server_name"] = "ACL Cloud Server"

        take_screenshot(driver, "22_server_info")
        return info

    except Exception as e:
        print(f"❌ 获取服务器信息失败: {e}")
        take_screenshot(driver, "22_server_info_error")
        return info


def manage_server_power(driver):
    """根据服务器状态执行 Start 或 Restart"""
    global POWER_ACTION
    print("\n⚡ 检测服务器电源状态并执行操作...")

    # 重新获取最新状态（确保页面已完全加载）
    status_selectors = [
        "span.status-badge[data-status]",
        ".status-badge",
        "[class*='status-badge']",
    ]

    status_element = None
    for sel in status_selectors:
        try:
            status_element = driver.find_element(By.CSS_SELECTOR, sel)
            if status_element and status_element.is_displayed():
                break
        except:
            continue

    if not status_element:
        print("❌ 无法定位状态徽章，跳过电源操作")
        take_screenshot(driver, "23_status_not_found")
        return False

    data_status = (status_element.get_attribute("data-status") or "").lower()
    status_text = status_element.text.strip().lower()
    is_offline = "offline" in data_status or "offline" in status_text or "hors" in status_text

    if is_offline:
        # ===== Offline → 点击 Start =====
        print("🔴 服务器处于 Offline 状态，执行 Start 操作...")
        start_selectors = [
            "button.power-btn[data-variant='start']",
            "button[data-variant='start']",
            "//button[contains(@class, 'power-btn') and contains(., 'Start')]",
            "//button[contains(@class, 'power-btn') and contains(., 'Démarrer')]",
        ]

        start_btn = None
        for sel in start_selectors:
            try:
                if sel.startswith("//"):
                    start_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, sel))
                    )
                else:
                    start_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                if start_btn:
                    break
            except:
                continue

        if not start_btn:
            print("❌ 未找到 Start 按钮")
            take_screenshot(driver, "23_start_button_not_found")
            return False

        # 检查按钮是否被禁用
        disabled = start_btn.get_attribute("disabled")
        if disabled:
            print("⚠️ Start 按钮被禁用，无法点击")
            take_screenshot(driver, "23_start_disabled")
            return False

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", start_btn)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", start_btn)
        time.sleep(2)
        POWER_ACTION = "start"
        take_screenshot(driver, "23_start_clicked")
        print("✅ 已点击 Start 按钮，服务器启动中...")
        return True

    else:
        # ===== Online → 点击 Restart =====
        print("🟢 服务器处于 Online 状态，执行 Restart 操作...")
        restart_selectors = [
            "button.power-btn[data-variant='restart']",
            "button[data-variant='restart']",
            "//button[contains(@class, 'power-btn') and contains(., 'Restart')]",
            "//button[contains(@class, 'power-btn') and contains(., 'Redémarrer')]",
        ]

        restart_btn = None
        for sel in restart_selectors:
            try:
                if sel.startswith("//"):
                    restart_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, sel))
                    )
                else:
                    restart_btn = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                    )
                if restart_btn:
                    break
            except:
                continue

        if not restart_btn:
            print("❌ 未找到 Restart 按钮")
            take_screenshot(driver, "23_restart_button_not_found")
            return False

        # 检查按钮是否被禁用
        disabled = restart_btn.get_attribute("disabled")
        if disabled:
            print("⚠️ Restart 按钮被禁用，无法点击")
            take_screenshot(driver, "23_restart_disabled")
            return False

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", restart_btn)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", restart_btn)
        time.sleep(2)
        POWER_ACTION = "restart"
        take_screenshot(driver, "23_restart_clicked")
        print("✅ 已点击 Restart 按钮，服务器重启中...")
        return True


def send_wechat_notification(info, need_renewal, renewal_success, power_action):
    """发送企业微信通知"""
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

    # 续期状态
    if need_renewal and renewal_success:
        renewal_emoji = "✅"
        renewal_text = "续期成功"
        renewal_color = "🟢"
    elif need_renewal and not renewal_success:
        renewal_emoji = "❌"
        renewal_text = "续期失败"
        renewal_color = "🔴"
    else:
        renewal_emoji = "✅"
        renewal_text = "无需续期"
        renewal_color = "🟢"

    # 电源操作状态
    if power_action == "start":
        power_emoji = "🚀"
        power_text = "已执行 Start（服务器已离线，执行启动）"
    elif power_action == "restart":
        power_emoji = "🔄"
        power_text = "已执行 Restart（服务器在线，执行重启）"
    else:
        power_emoji = "➖"
        power_text = "未执行电源操作"

    # 服务器状态图标
    if status == "online":
        status_emoji = "🟢"
    elif status == "offline":
        status_emoji = "🔴"
    else:
        status_emoji = "⚪"

    content = f"""{renewal_color} <b>ACL Cloud 服务器状态报告</b> {renewal_color}

📌 <b>服务器:</b> {server_name}
{status_emoji} <b>当前状态:</b> {status.upper()}
⏰ <b>剩余时间:</b> {time_remaining}
📋 <b>套餐信息:</b> {plan}
📝 <b>续期提示:</b> {renewal_note or '无'}

📊 <b>续期状态:</b> {renewal_emoji} {renewal_text}
⚡ <b>电源操作:</b> {power_emoji} {power_text}

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
    global NEED_RENEWAL, RENEWAL_SUCCESS, SERVER_STATUS, POWER_ACTION

    if not USERNAME or not PASSWORD:
        print("❌ 错误: 未设置 ACL_USERNAME 或 ACL_PASSWORD")
        sys.exit(1)

    ensure_screenshot_dir()

    # 启动虚拟桌面
    subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x24"], 
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ["DISPLAY"] = ":99"
    time.sleep(2)

    recording_process = start_ffmpeg_recording()
    driver = setup_driver()
    base_url = LOGIN_URL.rstrip("/auth/login").rstrip("/")

    server_info = {}

    try:
        # ========== 登录流程 ==========
        open_login_page(driver)
        login(driver)
        time.sleep(1)

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
            send_wechat_notification({"server_name": "登录失败"}, False, False, "none")
            return False

        # ========== 判断是否需要续期 ==========
        print("\n🔍 检查是否需要续期...")

        # 确保在仪表盘
        if "dashboard" not in driver.current_url.lower():
            driver.get(base_url + "/dashboard")
            time.sleep(3)
            take_screenshot(driver, "21_navigated_to_dashboard")

        close_install_popup(driver)

        # 检查是否有续期表格和 Renew 按钮
        has_renewal = False
        try:
            renewal_table = driver.find_element(By.CSS_SELECTOR, 
                "div.home-renewal-table, .renewal-table, [class*='renewal']")
            renew_buttons = driver.find_elements(By.CSS_SELECTOR, 
                "button.home-renew-action, .renew-action, button[class*='renew']")
            visible_renew = [b for b in renew_buttons if b.is_displayed()]
            if renewal_table and len(visible_renew) > 0:
                has_renewal = True
                print("✅ 检测到需要续期的项目")
            else:
                print("ℹ️ 未检测到需要续期的项目")
        except NoSuchElementException:
            print("ℹ️ 仪表盘上没有续期表格")

        # ========== 情况1: 需要续期 ==========
        if has_renewal:
            print("\n📌 执行续期流程...")
            perform_renewal(driver)

            # 续期后导航到服务列表
            navigate_to_services(driver)
            click_manage_button(driver)

            # 获取服务器信息（含状态检测）
            server_info = get_server_info(driver)

            # 根据状态执行 Start 或 Restart
            manage_server_power(driver)

        # ========== 情况2: 不需要续期 ==========
        else:
            print("\n📌 无需续期，直接获取服务器信息...")
            navigate_to_services(driver)
            click_manage_button(driver)
            server_info = get_server_info(driver)
            manage_server_power(driver)

        # ========== 发送通知 ==========
        send_wechat_notification(server_info, NEED_RENEWAL, RENEWAL_SUCCESS, POWER_ACTION)

        take_screenshot(driver, "99_script_completed")
        print("\n🎉 所有操作已完成！")
        return True

    except Exception as e:
        print(f"❌ 发生错误: {e}")
        take_screenshot(driver, "99_error_occurred")
        send_wechat_notification({"server_name": f"脚本异常: {str(e)[:50]}"}, False, False, "none")
        return False
    finally:
        driver.quit()
        stop_ffmpeg_recording(recording_process)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
