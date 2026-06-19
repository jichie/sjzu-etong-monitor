#!/usr/bin/env python3
"""
山东建筑大学电费监控 - CTTICKET 获取助手

用法:
  python3 login_helper.py
  选 1 → 粘贴浏览器 Console 中的 CTTICKET
  选 2 → 自动打开浏览器，扫码登录后提取
"""

import json, os, sys, re, time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "etong_monitor.py")


def main():
    print("=" * 55)
    print("  山东建筑大学 — CTTICKET 获取助手")
    print("=" * 55)
    print()
    print("1. 手动获取（粘贴浏览器 Console 的值）")
    print("2. 自动获取（Playwright 自动打开浏览器登录提取）")
    print()
    choice = input("请选择 (1/2): ").strip()

    if choice == "1":
        ctticket = input("\n粘贴 CTTICKET: ").strip()
        if ctticket:
            if save_ctticket(ctticket):
                print(f"\n✅ 已保存，现在可以运行: python3 etong_monitor.py --once")
        else:
            print("输入为空")

    elif choice == "2":
        auto_get_ctticket()
    else:
        print("无效选择")


def auto_get_ctticket():
    """Playwright 自动打开浏览器，等待用户登录后提取 CTTICKET"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\n❌ Playwright 未安装: pip install playwright && playwright install chromium")
        return

    has_display = 'DISPLAY' in os.environ
    print(f"\n{'='*50}")
    print("  🚀 启动 Playwright 浏览器...")
    print(f"  {'🖥️ 有界面模式' if has_display else '💻 无界面模式（服务端）'}")
    print(f"  {'='*50}")
    if not has_display:
        print("  截图将保存到 /tmp/etong_screenshot.png")
        print("  请下载查看截图并完成登录")
    print()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not has_display)
            context = browser.new_context()
            page = context.new_page()

            # 监听所有请求的 Cookie
            page.goto("https://etong.sdjzu.edu.cn/easytong_webapp/index.html", 
                       timeout=30000, wait_until="networkidle")

            # 截图
            shot_path = "/tmp/etong_screenshot.png"
            page.screenshot(path=shot_path, full_page=True)
            print(f"  📸 截图已保存: {shot_path}")

            if not has_display:
                print(f"\n  ⚠️ 请下载 /tmp/etong_screenshot.png 到本地查看")
                print(f"     在浏览器中完成登录（扫码/账号密码）")
                print(f"     登录成功后脚本会自动检测 CTTICKET\n")
            else:
                print(f"\n  👀 请在打开的浏览器窗口中登录")

            print("  ⏳ 等待登录完成（最长 10 分钟）...")
            for i in range(600):
                time.sleep(1)
                cookies = context.cookies()
                for c in cookies:
                    if 'CTTICKET' in c['name'].upper():
                        val = c['value']
                        print(f"\n  ✅ 检测到 CTTICKET!")
                        print(f"  🔑 {val}")
                        save_ctticket(val)
                        time.sleep(2)
                        browser.close()
                        print(f"\n  🎉 完成！现在可以运行: python3 etong_monitor.py --once")
                        return

                if i > 0 and i % 60 == 0:
                    # 每分钟重新截一次图
                    page.screenshot(path=shot_path)
                    print(f"  ⏱️ 已等待 {i//60} 分钟，截图已更新")

            print("\n  ⏰ 超时，未检测到 CTTICKET")
            browser.close()
    except Exception as e:
        print(f"\n  ❌ 出错: {e}")
        print(f"     可尝试方式一手动获取")


def save_ctticket(ctticket):
    """写入 etong_monitor.py"""
    if not os.path.exists(CONFIG_PATH):
        print(f"\n  未找到 {CONFIG_PATH}，请手动将 CTTICKET 填入脚本")
        print(f"  CTTICKET = \"{ctticket}\"")
        return False

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = re.sub(r'CTTICKET\s*=\s*""', f'CTTICKET = "{ctticket}"', content)
    if new_content == content:
        print(f"  CTTICKET: {ctticket}")
        print(f"  请手动填入脚本")
        return False

    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"  ✅ 已写入 {CONFIG_PATH}")
    return True


if __name__ == "__main__":
    main()
