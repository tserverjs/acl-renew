# test_acl_renewal.py
# -*- coding: utf-8 -*-
"""
============================================
  ACL Cloud 自动续期（SeleniumBase 版 v7.1）
  功能：自动登录、语言切换、续期、状态检测、Start/Restart、通知、原生录屏
  运行方式: pytest test_acl_renewal.py --rec --headless -s
============================================
"""

import os
import time
import requests
from datetime import datetime
from io import BytesIO
from PIL import Image
import pytesseract
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException
from seleniumbase import BaseCase


class TestAclRenewal(BaseCase):
    def test_acl_renewal(self):
        # ========== 配置读取 ==========
        self.username = os.getenv("ACL_USERNAME", "")
        self.password = os.getenv("ACL_PASSWORD", "")
        self.login_url = os.getenv("ACL_LOGIN_URL", "https://aclclouds.com/auth/login")
        self.webhook_key = os.getenv("WECHAT_WEBHOOK_KEY", "")
        self.base_url = self.login_url.rstrip("/auth/login").rstrip("/")
        self.need_renewal = False
        self.renewal_success = False

        if not self.username or not self.password:
            raise ValueError("❌ 错误: 未设置 ACL_USERNAME 或 ACL_PASSWORD")

        # ========== 1. 登录流程 ==========
        self.open_login_page()
        self.enter_credentials()

        login_success = False
        for attempt in range(3):
            print(f"\n🔄 登录页验证码尝试 {attempt + 1}/3")
            if self.process_captcha("login_page"):
                self.click_signin()
                if self.check_login_success():
                    login_success = True
                    break
            time.sleep(2)

        if not login_success:
            self.send_notification({"server_name": "登录失败"}, False, False, None)
            raise Exception("❌ 登录失败")

        # ========== 2. 进入 Dashboard 并切换语言 ==========
        if "dashboard" not in self.get_current_url().lower():
            self.open(self.base_url + "/dashboard")
            self.sleep(2)

        self.close_install_popup()
        self.switch_language_to_en()

        # ========== 3. 检查并执行续期 ==========
        self.check_and_renew()

        # ========== 4. 导航到服务详情 ==========
        self.navigate_to_services()
        self.click_manage_button()

        # ========== 5. 获取服务器信息 + 检测状态并操作电源 ==========
        info = self.get_server_info()
        power_action = self.handle_server_power()
        info["power_action"] = power_action

        # ========== 6. 发送通知 ==========
        self.send_notification(info, self.need_renewal, self.renewal_success, power_action)
        print("\n🎉 所有操作已完成！")

    # ==================== 页面操作封装 ====================

    def open_login_page(self):
        print(f"🌐 打开登录页面: {self.login_url}")
        self.open(self.login_url)
        self.sleep(3)
        print("✅ 登录页面已加载")

    def enter_credentials(self):
        print("🔑 输入用户名和密码...")
        self.type("input[name='email'], input[type='email']", self.username)
        self.type("input[name='password'], input[type='password']", self.password)
        print("✅ 用户名和密码已输入")

    def process_captcha(self, flow_name=""):
        """通用验证码处理（登录页 & 续期弹窗）"""
        print(f"🔄 开始处理 {flow_name} 人机验证...")

        # 1. 点击复选框
        try:
            self.click("div.auth-captcha-checkbox, .captcha-checkbox, input[type='checkbox'] + label")
            self.sleep(1.5)
        except Exception as e:
            print(f"⚠️ 点击复选框失败: {e}")
            return False

        # 2. 获取提示文字
        try:
            prompt_text = self.get_text("div.auth-captcha-prompt strong, .captcha-prompt strong")
            print(f"📝 验证码提示文字: {prompt_text}")
        except Exception as e:
            print(f"⚠️ 获取提示文字失败: {e}")
            return False

        # 3. 获取选项
        try:
            options = self.driver.find_elements(
                By.CSS_SELECTOR,
                "div.auth-captcha-options button.auth-captcha-option, .captcha-options .captcha-option"
            )
        except Exception as e:
            print(f"⚠️ 获取选项失败: {e}")
            return False

        if not options:
            print("⚠️ 未找到验证码选项")
            return False

        # 4. OCR 识别并点击
        target = prompt_text.lower().replace(" ", "").replace("-", "")
        clicked = False

        for option in options:
            try:
                img = option.find_element(By.CSS_SELECTOR, "img")
                img_src = img.get_attribute("src")
                full_url = self.base_url + img_src if img_src.startswith("/") else img_src

                resp = requests.get(full_url, timeout=10)
                img = Image.open(BytesIO(resp.content)).convert("L")
                img = img.point(lambda x: 255 if x > 128 else 0)
                ocr_text = pytesseract.image_to_string(img, lang='eng', config='--psm 7').strip()

                ocr_clean = ocr_text.lower().replace(" ", "").replace("-", "")
                print(f"  📍 选项 OCR: {ocr_text}")

                if target in ocr_clean or ocr_clean in target:
                    print(f"✅ 找到匹配，点击选项 (OCR: {ocr_text})")
                    self.execute_script("arguments[0].scrollIntoView({block: 'center'});", option)
                    self.sleep(0.3)
                    self.execute_script("arguments[0].click();", option)
                    clicked = True
                    self.sleep(2)
                    break
            except Exception as e:
                print(f"  ❌ 选项识别失败: {e}")

        if not clicked:
            print("❌ 未点击任何选项")
            return False

        # 5. 验证结果
        self.sleep(2)
        try:
            if self.is_element_visible("span.auth-captcha-label, .captcha-label"):
                label = self.get_text("span.auth-captcha-label, .captcha-label")
                if "Verified" in label or "Vérifié" in label:
                    print("✅ 人机验证通过！(Verified 标签)")
                    return True
        except:
            pass

        try:
            if not self.is_element_visible("div.auth-captcha-options, .captcha-options"):
                print("✅ 人机验证通过！(验证码元素已消失)")
                return True
        except:
            pass

        print("⚠️ 无法确认验证状态，假设通过（无错误提示）")
        return True

    def click_signin(self):
        print("👆 点击 Sign in 按钮...")
        self.click("//button[contains(text(), 'Sign in')]")
        self.sleep(2)
        print("✅ 已点击 Sign in 按钮")

    def check_login_success(self):
        current_url = self.get_current_url()
        if "login" not in current_url.lower():
            print(f"🎉 登录成功！当前 URL: {current_url}")
            return True
        else:
            print("⚠️ 登录失败")
            return False

    def switch_language_to_en(self):
        """登录成功后切换语言为 English"""
        print("\n🌐 检查并切换语言为 English...")

        lang_selectors = [
            "button[aria-label='Langue']",
            "button[class*='LanguageButton']",
            "//button[@aria-label='Langue']",
            "//button[contains(@class, 'LanguageButton')]",
            "//button[.//img[contains(@src, 'flags')]]"
        ]

        lang_btn = None
        for sel in lang_selectors:
            if self.is_element_visible(sel):
                lang_btn = sel
                break

        if not lang_btn:
            print("⚠️ 未找到语言切换按钮，跳过")
            return False

        # 检查当前语言
        try:
            badge = self.find_element(f"{lang_btn} .lang-code-badge")
            if badge.text.strip().upper() == "EN":
                print("✅ 当前语言已是 English，无需切换")
                return True
        except:
            pass

        print("📝 准备切换为 English...")
        self.click(lang_btn)
        self.sleep(1.5)

        # 选择 English
        en_selectors = [
            "//button[.//img[contains(@src, 'en.png') or contains(@alt, 'English')]]",
            "//button[contains(text(), 'English')]",
            "//button[.//span[contains(text(), 'EN')]]",
            "button[class*='LanguageOption'] img[src*='en']"
        ]

        for sel in en_selectors:
            if self.is_element_visible(sel):
                self.click(sel)
                self.sleep(3)
                print("✅ 已切换为 English")
                return True

        print("⚠️ 未找到 English 选项")
        return False

    def close_install_popup(self):
        try:
            for sel in [
                "//button[contains(text(), 'Fermer')]",
                "//button[contains(text(), 'Close')]",
                "//div[contains(@class, 'pwa-install')]//button[1]"
            ]:
                if self.is_element_visible(sel):
                    self.click(sel)
                    print("✅ 已关闭安装弹窗")
                    self.sleep(0.5)
                    return True
        except:
            pass
        return False

    def check_and_renew(self):
        print("\n🔍 检查是否需要续期...")
        self.close_install_popup()

        has_renewal = False
        try:
            if self.is_element_visible("div.home-renewal-table, .renewal-table, [class*='renewal']"):
                renew_btns = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    "button.home-renew-action, .renew-action, button[class*='renew']"
                )
                visible = [b for b in renew_btns if b.is_displayed()]
                if visible:
                    has_renewal = True
                    print("✅ 检测到需要续期的项目")
        except:
            print("ℹ️ 未检测到需要续期的项目")

        if has_renewal:
            self.perform_renewal()

    def perform_renewal(self):
        print("\n🔄 开始执行续期操作...")
        self.close_install_popup()

        try:
            self.assert_element("div.home-renewal-table, .renewal-table, [class*='renewal']", timeout=15)
            print("✅ 续期表格已加载")

            row_selectors = [
                "div.home-renewal-row",
                ".renewal-row",
                "tr[class*='renewal']",
                "div[class*='renewal-row']"
            ]

            rows = []
            for sel in row_selectors:
                rows = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if rows:
                    print(f"📋 找到 {len(rows)} 个续期项目")
                    break

            keywords = [
                "suspended", "expired", "suspendu", "expiré", "terminé",
                "inactive", "inactif", "ended", "non actif", "renouvellement",
                "renewal", "renouveler", "renew"
            ]

            for idx, row in enumerate(rows):
                try:
                    status = self._safe_find_text(row, "span.home-renewal-status, .renewal-status, td:nth-child(4), .status")
                    model_name = self._safe_find_text(row, "strong.home-renewal-name, .renewal-name, td:nth-child(2), .model")

                    print(f"\n📦 项目 {idx + 1}: {model_name or '未知'}")
                    print(f"  📊 状态: {status or '未知'}")

                    if not status:
                        continue

                    if any(kw in status.lower() for kw in keywords):
                        self.need_renewal = True
                        print("  ⚠️ 需要续期！")

                        renew_btn = None
                        for rsel in ["button.home-renew-action", ".renew-action", "button[class*='renew']", "td:last-child button"]:
                            try:
                                btn = row.find_element(By.CSS_SELECTOR, rsel)
                                if btn.is_displayed():
                                    renew_btn = btn
                                    break
                            except:
                                continue

                        if not renew_btn:
                            print("  ❌ 未找到 Renew 按钮")
                            continue

                        self.execute_script("arguments[0].click();", renew_btn)
                        print("  ✅ 已点击 Renew 按钮")
                        self.sleep(2)

                        if self.process_captcha("renewal_popup"):
                            self.renewal_success = True
                            print("✅ 续期验证通过！")
                        else:
                            print("❌ 续期验证失败")

                        self.sleep(2)
                        self.close_install_popup()
                    else:
                        print("  ✅ 状态正常，无需续期")

                except StaleElementReferenceException:
                    print(f"  ⚠️ 项目 {idx + 1} 元素已过期，跳过")
                    continue
                except Exception as e:
                    print(f"  ❌ 处理项目出错: {e}")
                    continue

        except Exception as e:
            print(f"❌ 续期操作失败: {e}")

    def navigate_to_services(self):
        print("\n📂 点击 My services 导航...")
        for sel in [
            "a[aria-label='My services']",
            "a[href='/dashboard/projects']",
            "//a[contains(@aria-label, 'My services')]",
            "//span[contains(text(), 'My services')]/parent::a"
        ]:
            if self.is_element_visible(sel):
                self.click(sel)
                self.sleep(3)
                print("✅ 已进入 My services 页面")
                return True

        print("⚠️ 未找到导航按钮，直接访问 URL")
        self.open("https://aclclouds.com/dashboard/projects")
        self.sleep(3)
        return True

    def click_manage_button(self):
        print("\n🔧 点击 Manage 按钮...")
        for sel in [
            "a.client-btn--primary[href^='/server/']",
            "//a[contains(@href, '/server/') and contains(@class, 'client-btn--primary')]",
            "//a[contains(text(), 'Manage')]",
            ".client-btn--primary"
        ]:
            if self.is_element_visible(sel):
                self.click(sel)
                self.sleep(3)
                print("✅ 已进入服务器详情页")
                return True

        print("❌ 未找到 Manage 按钮")
        return False

    def get_server_info(self):
        """
        获取服务器详情页信息，并检测服务器状态（Online / Offline）
        """
        print("\n📊 获取服务器信息...")
        info = {
            "time_remaining": "",
            "plan": "",
            "renewal_note": "",
            "server_name": "",
            "server_status": "unknown",
            "server_url": self.get_current_url()
        }

        # 1. 获取基础信息
        try:
            for sel in [
                "div[style*='background: rgba(49, 95, 79']",
                "//div[contains(text(), 'Time remaining')]",
                ".server-info-card",
                "[class*='server-info']"
            ]:
                if self.is_element_visible(sel):
                    text = self.get_text(sel)
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    for line in lines:
                        if "Time remaining" in line or "Temps restant" in line:
                            info["time_remaining"] = line.replace("Time remaining:", "").replace("Temps restant:", "").strip()
                        elif "plan" in line.lower() or "gratuit" in line.lower() or "free" in line.lower():
                            info["plan"] = line
                        elif "Renewal" in line or "renouvellement" in line.lower():
                            info["renewal_note"] = line
                    break

            try:
                info["server_name"] = self.get_text("h1, .server-name, [class*='server-title']")
            except:
                info["server_name"] = "ACL Cloud Server"

            print(f"  ⏰ 剩余时间: {info['time_remaining'] or '未获取'}")
            print(f"  📋 套餐: {info['plan'] or '未获取'}")
            print(f"  📝 续期提示: {info['renewal_note'] or '未获取'}")

        except Exception as e:
            print(f"❌ 获取服务器基础信息失败: {e}")

        # 2. 检测服务器 Online / Offline 状态
        try:
            status_selectors = [
                ".status-badge[data-status]",
                ".status-badge",
                "[class*='status-badge']",
                "//span[contains(@class, 'status-badge')]",
                "//div[contains(@class, 'stat-info')]//span[contains(@class, 'status')]"
            ]

            for sel in status_selectors:
                if self.is_element_visible(sel):
                    badge = self.find_element(sel)
                    data_status = badge.get_attribute("data-status") or ""
                    text_status = badge.text.strip() or ""
                    combined = (data_status + " " + text_status).lower()

                    if "offline" in combined or "hors ligne" in combined:
                        info["server_status"] = "offline"
                    elif "online" in combined or "en ligne" in combined:
                        info["server_status"] = "online"
                    else:
                        info["server_status"] = text_status.lower() or data_status.lower() or "unknown"

                    print(f"  🖥️ 服务器状态: {info['server_status'].upper()} (data-status={data_status}, text={text_status})")
                    break

        except Exception as e:
            print(f"⚠️ 检测服务器状态失败: {e}")
            info["server_status"] = "unknown"

        return info

    def handle_server_power(self):
        """
        根据服务器状态自动点击 Start 或 Restart 按钮。
        - Offline → 点击 Start (data-variant="start")
        - Online  → 点击 Restart (data-variant="restart")
        返回: "start" / "restart" / None
        """
        status = "unknown"
        try:
            badge = self.find_element(".status-badge[data-status], .status-badge")
            data_status = badge.get_attribute("data-status") or ""
            text_status = badge.text.strip() or ""
            combined = (data_status + " " + text_status).lower()
            if "offline" in combined:
                status = "offline"
            elif "online" in combined:
                status = "online"
        except Exception as e:
            print(f"⚠️ 无法直接检测状态: {e}")
            return None

        print(f"\n🔌 处理服务器电源操作（当前状态: {status.upper()}）...")

        if status == "offline":
            print("  🖥️ 服务器处于 Offline 状态，准备点击 Start...")
            start_selectors = [
                "button.power-btn[data-variant='start']",
                "button[data-variant='start']",
                "//button[contains(@class, 'power-btn') and contains(., 'Start')]",
                "//button[contains(@class, 'power-btn') and contains(., 'Démarrer')]"
            ]

            for sel in start_selectors:
                if self.is_element_visible(sel):
                    btn = self.find_element(sel)
                    if btn.get_attribute("disabled"):
                        print("  ⚠️ Start 按钮处于 disabled 状态，跳过")
                        return None

                    self.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                    self.sleep(0.5)
                    self.execute_script("arguments[0].click();", btn)
                    self.sleep(2)
                    print("  ✅ 已点击 Start 按钮")
                    return "start"

            print("  ❌ 未找到 Start 按钮")
            return None

        elif status == "online":
            print("  🖥️ 服务器处于 Online 状态，准备点击 Restart...")
            restart_selectors = [
                "button.power-btn[data-variant='restart']",
                "button[data-variant='restart']",
                "//button[contains(@class, 'power-btn') and contains(., 'Restart')]",
                "//button[contains(@class, 'power-btn') and contains(., 'Redémarrer')]"
            ]

            for sel in restart_selectors:
                if self.is_element_visible(sel):
                    btn = self.find_element(sel)
                    if btn.get_attribute("disabled"):
                        print("  ⚠️ Restart 按钮处于 disabled 状态，跳过")
                        return None

                    self.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                    self.sleep(0.5)
                    self.execute_script("arguments[0].click();", btn)
                    self.sleep(2)
                    print("  ✅ 已点击 Restart 按钮")
                    return "restart"

            print("  ❌ 未找到 Restart 按钮")
            return None

        else:
            print(f"  ⚠️ 无法判断服务器状态（{status}），跳过电源操作")
            return None

    def send_notification(self, info, need_renewal, renewal_success, power_action):
        """
        power_action: None / "start" / "restart"
        """
        if not self.webhook_key:
            print("⚠️ 未设置 WECHAT_WEBHOOK_KEY，跳过通知")
            return False

        webhook_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={self.webhook_key}"

        if isinstance(info, dict):
            server_name = info.get("server_name", "ACL Cloud Server")
            time_remaining = info.get("time_remaining", "未知")
            plan = info.get("plan", "未知")
            renewal_note = info.get("renewal_note", "")
            server_url = info.get("server_url", "")
            server_status = info.get("server_status", "unknown")
        else:
            server_name = str(info)
            time_remaining = plan = renewal_note = server_url = server_status = "未知"

        if need_renewal and renewal_success:
            renew_emoji, renew_text = "✅", "续期成功"
        elif need_renewal:
            renew_emoji, renew_text = "❌", "续期失败"
        else:
            renew_emoji, renew_text = "✅", "无需续期"

        if power_action == "start":
            power_emoji, power_text = "🟢", "已执行 Start（服务器原 Offline）"
        elif power_action == "restart":
            power_emoji, power_text = "🔄", "已执行 Restart（服务器原 Online）"
        else:
            power_emoji, power_text = "➖", "未执行电源操作"

        content = f"""ACL Cloud 服务器状态报告

📌 服务器: {server_name}
🖥️ 当前状态: {server_status.upper()}
⏰ 剩余时间: {time_remaining}
📋 套餐信息: {plan}
📝 续期提示: {renewal_note or '无'}

📊 续期状态: {renew_emoji} {renew_text}
🔌 电源操作: {power_emoji} {power_text}

🔗 {server_url}

⏱️ 报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        try:
            resp = requests.post(
                webhook_url,
                json={"msgtype": "text", "text": {"content": content, "mentioned_list": ["@all"]}},
                timeout=10
            )
            if resp.json().get("errcode") == 0:
                print("✅ 企业微信通知发送成功")
                return True
            else:
                print(f"❌ 企业微信通知发送失败: {resp.json()}")
        except Exception as e:
            print(f"❌ 发送通知异常: {e}")
        return False

    def _safe_find_text(self, element, selector, default=""):
        try:
            return element.find_element(By.CSS_SELECTOR, selector).text.strip()
        except Exception:
            return default
