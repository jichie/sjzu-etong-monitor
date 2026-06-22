#!/usr/bin/env python3
"""
山东建筑大学 电费监控服务 v9.0
- 每小时检查电量，低于阈值立即告警
- 每天 19:10 推送当日电量日报
- 支持 systemd 开机自启
- 📡 无本地数据时自动通过 API 拉取楼栋和房间列表
- 双校区自动识别：同时加载 rooms.json + 烟台校区_rooms.json
- 🔑 动态签名：无需抓包，自动计算 Sign
- 🛡️ 浏览器指纹模拟：使用 curl_cffi 模拟 Chrome TLS 指纹，防止 CTTICKET 过期
- 🔐 自动处理二次验证：固定设备 ID，首次 2FA 后永久免验证
- 📱 手机验证码接收：临时 HTTP 服务，手机浏览器完成图片验证码+短信验证码

GitHub: https://github.com/jichie/sjzu-etong-monitor
"""

import hashlib
import json
import sys
import time
import os
import signal
import urllib3
import re
import socket
from datetime import datetime
from urllib.parse import unquote

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- HTTP 客户端：优先使用 curl_cffi（模拟浏览器指纹）---
USE_CURL_CFFI = False
try:
    from curl_cffi import requests as cffi_requests
    USE_CURL_CFFI = True
    print("✅ 使用 curl_cffi（浏览器指纹模式）")
except ImportError:
    try:
        import requests
        print("⚠️  未安装 curl_cffi，使用普通 requests（建议 pip3 install curl_cffi）")
    except ImportError:
        print("pip3 install curl_cffi 或 pip3 install requests"); sys.exit(1)

try:
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
    import base64
except ImportError:
    try:
        from Cryptodome.PublicKey import RSA
        from Cryptodome.Cipher import PKCS1_v1_5
        import base64
    except ImportError:
        print("pip3 install pycryptodome"); sys.exit(1)


# ====================== 配置区域 ======================

# --- 登录账号 ---
SSO_USERNAME = "STUDENT_ID_PLACEHOLDER"              # 你的学号
SSO_PASSWORD = "PASSWORD_PLACEHOLDER"              # SSO 密码

# --- 房间配置 ---
BUILDING_NAME = "梅二-照明"
ROOM_NAME = "413"

# --- MD5 签名密钥 ---
MD5_KEY = "ok15we1@oid8x5afd@"

# --- 认证 Token ---
# JWT Token：从浏览器拿到一次，永久可用（留空则自动获取）
JWT_TOKEN = "JWT_TOKEN_PLACEHOLDER"

# CTTICKET：从浏览器获取（教程见 README），有效期数月，留空则走 SSO 自动登录
CTTICKET = "CTTICKET_PLACEHOLDER"

# --- 推送配置 ---
WECOM_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=WEBHOOK_KEY_PLACEHOLDER"             # 企业微信机器人 Webhook
BARK_KEY = ""                  # Bark 推送 Key（iOS）
PUSHPLUS_TOKEN = ""            # PushPlus Token

# --- 监控设置 ---
LOW_BALANCE_THRESHOLD = 10.0   # 低电量告警阈值（度）
CHECK_INTERVAL = 3600          # 检查间隔（秒），默认 1 小时
DAILY_REPORT_HOUR = 19         # 日报推送时间（小时）
DAILY_REPORT_MINUTE = 10       # 日报推送时间（分钟）
ALERT_COOLDOWN = 21600         # 告警冷却时间（秒），默认 6 小时

# --- 二次验证设置 ---
SMS_SERVER_PORT = 8899         # 验证码接收服务端口
SMS_CODE_TIMEOUT = 300         # 验证码等待超时（秒），默认 5 分钟

# --- 房间数据文件 ---
JINAN_ROOMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rooms.json")
YANTAI_ROOMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "烟台校区_rooms.json")

# ====================== 以下一般无需修改 ======================

# 硬编码 JWT（永久有效）
JWT_TOKEN = "JWT_TOKEN_PLACEHOLDER"


ROOM_CONFIG = {
    "AccNum": "0",
    "AreaNo": "1",
    "BuildingNo": "",
    "FloorNo": "0",
    "ItemNum": "2",
    "RoomNo": "",
}

# ====================== 配置结束 ======================

COOKIE_FILE = "/tmp/etong_cookies.json"
STATE_FILE = "/tmp/etong_state.json"
ROOMS_CACHE_FILE = "/tmp/etong_rooms_cache.json"
LOG_FILE = "/var/log/etong.log"

running = True


def signal_handler(sig, frame):
    global running
    log("收到停止信号，正在退出...")
    running = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ====================== HTTP 请求封装 ======================

def http_get(url, **kwargs):
    kwargs.setdefault("timeout", 15)
    kwargs.setdefault("verify", False)
    if USE_CURL_CFFI:
        kwargs.setdefault("impersonate", "chrome120")
        return cffi_requests.get(url, **kwargs)
    else:
        return requests.get(url, **kwargs)


def http_post(url, **kwargs):
    kwargs.setdefault("timeout", 15)
    kwargs.setdefault("verify", False)
    if USE_CURL_CFFI:
        kwargs.setdefault("impersonate", "chrome120")
        return cffi_requests.post(url, **kwargs)
    else:
        return requests.post(url, **kwargs)


# ====================== 日志与状态 ======================

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except:
        pass


def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"last_alert_time": 0, "last_daily_report": "", "last_balance": None}


def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except:
        pass


# ====================== 二次验证（2FA）处理 ======================

def get_device_id():
    """生成固定的设备 ID（基于学号，不再随机）"""
    return hashlib.md5(f"etong_monitor_{SSO_USERNAME}_sjzu_2026".encode()).hexdigest()


def get_local_ip():
    """获取本机 IP（内网优先）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "你的服务器IP"


# ====================== SSO 二次验证接口（根据抓包确认）======================
#
# 已确认的接口：
# 1. POST /ssoApi/getPhoneCaptcha  — 发送短信验证码（参数：loginMode=2, phone）
# 2. POST /ssoApi/verifyPhone      — 提交短信验证码（参数：loginMode=2, phone, verifyMsgCode, code, locationurl, device）
#
# 待确认：图片验证码的获取和提交接口
# 如果你知道图片验证码的接口，请在下方 CAPTCHA_URLS 中添加

SSO_BASE = "https://sso.sdjzu.edu.cn"

# 图片验证码获取接口（已确认）
# POST /ssoApi/getPictureVerifyCode  参数: chkHeight=38, chkWidth=90
CAPTCHA_IMG_URL = f"{SSO_BASE}/ssoApi/getPictureVerifyCode"
CAPTCHA_IMG_PARAMS = {"chkHeight": "38", "chkWidth": "90"}

# 图片验证码提交接口（待确认，依次尝试）
# 提交图片验证码后才会发送短信
CAPTCHA_VERIFY_URLS = [
    f"{SSO_BASE}/ssoApi/checkPictureVerifyCode",
    f"{SSO_BASE}/ssoApi/verifyPictureCode",
    f"{SSO_BASE}/ssoApi/checkCaptcha",
    f"{SSO_BASE}/ssoApi/verifyCaptcha",
    f"{SSO_BASE}/ssoApi/checkImgCode",
]


def fetch_captcha_image(session):
    """
    获取图片验证码，返回 (图片二进制, captchaId) 或 (None, None)
    """
    url = f"{SSO_BASE}/ssoApi/getPictureVerifyCode"
    boundary = "----BoundaryCaptcha"
    CRLF = chr(13) + chr(10)
    
    # 构造 multipart/form-data 请求体
    body = ""
    for k, v in {"chkHeight": "38", "chkWidth": "90"}.items():
        body += "--" + boundary + CRLF
        body += 'Content-Disposition: form-data; name="' + k + '"' + CRLF + CRLF
        body += v + CRLF
    body += "--" + boundary + "--" + CRLF
    
    try:
        resp = session.post(
            url,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            data=body.encode('utf-8'),
            timeout=15
        )
        if resp.status_code == 200:
            # 解析 JSON 响应
            try:
                data = resp.json()
                inner = data.get("data", data)
                if isinstance(inner, dict):
                    b64 = inner.get("base64") or ""
                    captcha_id = inner.get("captchaId") or ""
                    if b64 and captcha_id:
                        if "," in b64:
                            b64 = b64.split(",", 1)[1]
                        img_data = base64.b64decode(b64)
                        log(f"✅ 获取图片验证码成功，captchaId: {captcha_id}")
                        return img_data, captcha_id
            except Exception as e:
                log(f"⚠️  解析图片验证码响应失败: {e}")
    except Exception as e:
        log(f"⚠️  获取图片验证码失败: {e}")
    
    return None, None


def submit_img_captcha(session, captcha_code, captcha_id):
    """
    提交图片验证码验证
    返回 (success: bool, error_msg: str)
    """
    url = f"{SSO_BASE}/ssoApi/verifyCaptcha"
    boundary = "----BoundaryVerify"
    CRLF = chr(13) + chr(10)
    
    # 构造 multipart/form-data 请求体
    body = ""
    for k, v in {"captchaId": captcha_id, "verifyValue": captcha_code}.items():
        body += "--" + boundary + CRLF
        body += 'Content-Disposition: form-data; name="' + k + '"' + CRLF + CRLF
        body += v + CRLF
    body += "--" + boundary + "--" + CRLF
    
    try:
        resp = session.post(
            url,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            data=body.encode('utf-8'),
            timeout=15
        )
        result = resp.json()
        msg = result.get("msg", "")
        if result.get("code") == "0x000000" or result.get("success") or result.get("status") == 1:
            log(f"✅ 图片验证码验证成功")
            return True, ""
        else:
            log(f"⚠️  图片验证码验证失败: {msg}")
            return False, msg
    except Exception as e:
        log(f"⚠️  图片验证码验证异常: {e}")
        return False, str(e)



def send_sms_code(session, phone, secondary_code=None):
    """
    调用 getPhoneCaptcha 发送短信验证码
    返回 (success: bool, error_msg: str)
    """
    url = f"{SSO_BASE}/ssoApi/getPhoneCaptcha"
    boundary = "----BoundarySMS"
    CRLF = chr(13) + chr(10)
    fields = {
        "loginMode": "2",
        "phone": phone,
    }
    if secondary_code:
        fields["code"] = secondary_code

    body = ""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
    body += f"--{boundary}--\r\n"

    try:
        resp = session.post(
            url,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            data=body.encode('utf-8'),
            timeout=15
        )
        result = resp.json()
        msg = result.get("msg", str(result))
        if result.get("code") == "0x000000" or result.get("success") or result.get("status") == 1:
            log(f"📱 短信验证码已发送到 {phone}")
            return True, ""
        else:
            log(f"⚠️  发送短信失败: {msg}")
            return False, msg
    except Exception as e:
        log(f"⚠️  发送短信异常: {e}")
        return False, str(e)


def submit_sms_code(session, phone, sms_code, secondary_code, device, locationurl):
    """
    调用 verifyPhone 提交短信验证码
    返回 True/False
    """
    url = f"{SSO_BASE}/ssoApi/verifyPhone"
    boundary = "----BoundaryVerify"
    fields = {
        "loginMode": "2",
        "phone": phone,
        "verifyMsgCode": sms_code,
        "code": secondary_code,
        "locationurl": locationurl,
        "device": device,
    }

    body = ""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
    body += f"--{boundary}--\r\n"

    try:
        resp = session.post(
            url,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            data=body.encode('utf-8'),
            timeout=15
        )
        result = resp.json()
        if result.get("code") == "0x000000" or result.get("success") or result.get("status") == 1:
            log(f"✅ 短信验证通过！后续不再需要验证码了")
            return True
        else:
            log(f"⚠️  短信验证失败: {result.get('msg', result)}")
            return False
    except Exception as e:
        log(f"⚠️  提交短信异常: {e}")
        return False


def run_2fa_web_server(session, phone, secondary_code, device, locationurl,
                       captcha_data, captcha_id, timeout=SMS_CODE_TIMEOUT):
    """
    启动两步验证的 HTTP 服务：
    Step 1: 显示图片验证码，用户输入 → 提交图片验证码 → 发送短信
    Step 2: 显示短信验证码输入框，用户输入 → 提交短信验证码
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import parse_qs

    state = {
        "step": 1,
        "done": False,
        "error": None,
    }

    # 图片验证码转 base64
    captcha_b64 = base64.b64encode(captcha_data).decode() if captcha_data else ""

    class Handler(BaseHTTPRequestHandler):

        def _send_html(self, html):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))

        def _send_json(self, data):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_GET(self):
            if state["step"] == 1:
                html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>电费验证 - 第1步</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 30px 24px;
            width: 90%;
            max-width: 380px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
        }}
        h2 {{ color: #333; margin-bottom: 6px; font-size: 20px; }}
        .subtitle {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
        .step-badge {{
            display: inline-block;
            background: #667eea;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            margin-bottom: 16px;
        }}
        .captcha-img {{
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            margin: 16px auto;
            display: block;
        }}
        .captcha-hint {{ color: #999; font-size: 12px; margin-bottom: 16px; }}
        input {{
            width: 100%;
            font-size: 24px;
            text-align: center;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            outline: none;
            letter-spacing: 6px;
        }}
        input:focus {{ border-color: #667eea; }}
        button {{
            width: 100%;
            margin-top: 16px;
            padding: 14px;
            font-size: 17px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: bold;
        }}
        button:active {{ opacity: 0.8; }}
        button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .error {{ color: #f44336; font-size: 13px; margin-top: 10px; display: none; }}
        .loading {{ display: none; color: #667eea; font-size: 14px; margin-top: 10px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="step-badge">步骤 1/2</div>
        <h2>🔐 图片验证码</h2>
        <p class="subtitle">请输入下方图片中的字符</p>
        <img class="captcha-img" src="data:image/png;base64,{captcha_b64}" alt="验证码">
        <p class="captcha-hint">看不清？<a href="javascript:void(0)" onclick="refreshCaptcha()" style="color:#667eea;text-decoration:none;font-weight:bold;">点击刷新图片</a></p>
        <form id="form">
            <input id="captchaInput" type="text" placeholder="图片验证码"
                   maxlength="6" autofocus autocomplete="off">
            <button type="submit" id="btn">发送短信验证码 →</button>
        </form>
        <p class="loading" id="loading">⏳ 正在发送短信...</p>
        <p class="error" id="error"></p>
    </div>
    <script>
        document.getElementById('form').addEventListener('submit', function(e) {{
            e.preventDefault();
            var code = document.getElementById('captchaInput').value.trim();
            if (!code) return;
            var btn = document.getElementById('btn');
            btn.textContent = '提交中...';
            btn.disabled = true;
            document.getElementById('loading').style.display = 'block';
            fetch('/submit_captcha', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                body: 'captcha=' + encodeURIComponent(code)
            }}).then(r => r.json()).then(data => {{
                if (data.ok) {{
                    window.location.href = '/step2';
                }} else {{
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('error').textContent = data.msg || '提交失败';
                    document.getElementById('error').style.display = 'block';
                    btn.textContent = '发送短信验证码 →';
                    btn.disabled = false;
                }}
            }}).catch(err => {{
                document.getElementById('loading').style.display = 'none';
                document.getElementById('error').textContent = '网络错误: ' + err;
                document.getElementById('error').style.display = 'block';
                btn.textContent = '发送短信验证码 →';
                btn.disabled = false;
            }});
        }});
        
        function refreshCaptcha() {{
            fetch('/refresh_captcha', {{ method: 'POST' }})
                .then(r => r.json())
                .then(data => {{
                    if (data.ok) {{
                        document.querySelector('.captcha-img').src = data.img;
                        document.getElementById('captchaInput').value = '';
                        document.getElementById('error').style.display = 'none';
                    }} else {{
                        alert('刷新失败: ' + data.msg);
                    }}
                }})
                .catch(err => alert('网络错误: ' + err));
        }}
    </script>
</body>
</html>"""
                self._send_html(html)

            elif self.path == "/step2":
                html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>电费验证 - 第2步</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #43a047 0%, #1b5e20 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .card {{
            background: white;
            border-radius: 16px;
            padding: 30px 24px;
            width: 90%;
            max-width: 380px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
        }}
        h2 {{ color: #333; margin-bottom: 6px; font-size: 20px; }}
        .subtitle {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
        .step-badge {{
            display: inline-block;
            background: #43a047;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            margin-bottom: 16px;
        }}
        .sms-icon {{ font-size: 48px; margin: 10px 0; }}
        input {{
            width: 100%;
            font-size: 28px;
            text-align: center;
            padding: 14px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            outline: none;
            letter-spacing: 8px;
        }}
        input:focus {{ border-color: #43a047; }}
        button {{
            width: 100%;
            margin-top: 16px;
            padding: 14px;
            font-size: 17px;
            background: linear-gradient(135deg, #43a047 0%, #1b5e20 100%);
            color: white;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: bold;
        }}
        button:active {{ opacity: 0.8; }}
        button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
        .success {{
            display: none;
            color: #43a047;
            font-size: 18px;
            margin-top: 20px;
        }}
        .error {{ color: #f44336; font-size: 13px; margin-top: 10px; display: none; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="step-badge">步骤 2/2 ✓</div>
        <h2>📱 短信验证码</h2>
        <p class="subtitle">短信已发送到你的手机</p>
        <div class="sms-icon">📨</div>
        <form id="form">
            <input id="smsInput" type="text" inputmode="numeric"
                   placeholder="短信验证码" maxlength="6" autofocus
                   autocomplete="off">
            <button type="submit" id="btn">完成验证 ✓</button>
        </form>
        <p class="error" id="error"></p>
        <p class="success" id="success">✅ 验证成功！可以关闭此页面</p>
    </div>
    <script>
        document.getElementById('form').addEventListener('submit', function(e) {{
            e.preventDefault();
            var code = document.getElementById('smsInput').value.trim();
            if (!code) return;
            var btn = document.getElementById('btn');
            btn.textContent = '验证中...';
            btn.disabled = true;
            fetch('/submit_sms', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                body: 'sms=' + encodeURIComponent(code)
            }}).then(r => r.json()).then(data => {{
                if (data.ok) {{
                    document.getElementById('form').style.display = 'none';
                    document.getElementById('success').style.display = 'block';
                }} else {{
                    document.getElementById('error').textContent = data.msg || '验证失败';
                    document.getElementById('error').style.display = 'block';
                    btn.textContent = '完成验证 ✓';
                    btn.disabled = false;
                }}
            }}).catch(err => {{
                document.getElementById('error').textContent = '网络错误: ' + err;
                document.getElementById('error').style.display = 'block';
                btn.textContent = '完成验证 ✓';
                btn.disabled = false;
            }});
        }});
    </script>
</body>
</html>"""
                self._send_html(html)
            else:
                self._send_html("<h2>已完成</h2>")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            params = parse_qs(body)

            if self.path == "/refresh_captcha":
                # 刷新图片验证码
                nonlocal captcha_data, captcha_b64, captcha_id
                new_captcha_data, new_captcha_id = fetch_captcha_image(session)
                if new_captcha_data and new_captcha_id:
                    captcha_data = new_captcha_data
                    captcha_id = new_captcha_id
                    captcha_b64 = base64.b64encode(captcha_data).decode()
                    log("🔄 刷新图片验证码")
                    self._send_json({"ok": True, "img": f"data:image/png;base64,{captcha_b64}"})
                else:
                    self._send_json({"ok": False, "msg": "刷新失败"})
                return

            if self.path == "/submit_captcha":
                captcha = params.get("captcha", [""])[0].strip()
                if not captcha:
                    self._send_json({"ok": False, "msg": "请输入验证码"})
                    return

                log(f"📋 用户输入图片验证码: {captcha}")

                # 先尝试提交图片验证码（如果有单独的接口）
                img_ok, img_err = submit_img_captcha(session, captcha, captcha_id)
                
                # 发送短信时带上图片验证码
                sms_ok, sms_err = send_sms_code(session, phone, secondary_code)
                
                if sms_ok:
                    state["step"] = 2
                    self._send_json({"ok": True})
                elif img_ok:
                    # 图片验证码通过了但短信发送失败，再试一次
                    sms_ok, sms_err = send_sms_code(session, phone, secondary_code)
                    if sms_ok:
                        state["step"] = 2
                        self._send_json({"ok": True})
                    else:
                        self._send_json({"ok": False, "msg": f"短信发送失败: {sms_err}"})
                else:
                    error_msg = sms_err or img_err or "未知错误"
                    self._send_json({"ok": False, "msg": f"验证失败: {error_msg}"})

            elif self.path == "/submit_sms":
                sms = params.get("sms", [""])[0].strip()
                if not sms or len(sms) < 4:
                    self._send_json({"ok": False, "msg": "请输入有效验证码"})
                    return

                log(f"📋 用户输入短信验证码: {sms}")

                success = submit_sms_code(session, phone, sms, secondary_code, device, locationurl)
                if success:
                    state["done"] = True
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "msg": "短信验证码错误，请重新输入"})

            else:
                self._send_json({"ok": False, "msg": "unknown"})

        def log_message(self, *args):
            pass

    local_ip = get_local_ip()
    
    # 使用 SO_REUSEADDR 防止端口占用问题
    import socketserver
    class ReusableTCPServer(HTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True
    
    server = ReusableTCPServer(("0.0.0.0", SMS_SERVER_PORT), Handler)
    server.timeout = 5

    log(f"📱 验证服务已启动 (端口 {SMS_SERVER_PORT})")
    log(f"👉 手机浏览器打开: http://{local_ip}:{SMS_SERVER_PORT}")

    send_notification(
        "🔐 电费脚本需要验证",
        f"📱 手机浏览器打开:\n"
        f"http://{local_ip}:{SMS_SERVER_PORT}\n"
        f"完成两步验证即可\n"
        f"⏰ {timeout}秒内完成"
    )

    start_time = time.time()
    while time.time() - start_time < timeout:
        server.handle_request()
        if state["done"]:
            log("✅ 二次验证全部完成！")
            server.server_close()
            return True

    log("❌ 等待验证超时")
    server.server_close()
    return False


# ====================== 房间数据管理 ======================

_room_name_cache = None
_building_no_cache = None
_room_no_cache = None
_campus_cache = None


def fetch_rooms_via_api():
    global _room_name_cache, _building_no_cache, _room_no_cache, _campus_cache
    log("🌐 未找到本地数据，尝试通过 API 拉取...")
    if CTTICKET:
        cookies = {}
    elif SSO_USERNAME and SSO_PASSWORD:
        cookies = sso_login()
        if not cookies:
            return False
    else:
        return False

    name_map, bld_map, rn_map = {}, {}, {}
    campuses = [("1", "2", "济南校区"), ("0", "6", "烟台校区")]
    url = "https://etong.sdjzu.edu.cn/easytong_app"
    headers = {"Authorization": JWT_TOKEN, "h5req": "Y", "Content-Type": "application/x-www-form-urlencoded"}
    cookies_dict = {"md5": "1", "etToken": JWT_TOKEN}
    cookies_dict.update(cookies)

    for area_no, item_num, campus_name in campuses:
        try:
            ts = time.strftime("%Y%m%d%H%M%S")
            sign = hashlib.md5(f"{area_no}|{item_num}|{ts}|{MD5_KEY}".encode()).hexdigest()
            data = {"AreaNo": area_no, "ItemNum": item_num, "Time": ts, "Sign": sign, "ContentType": "application/json"}
            resp = http_post(f"{url}/GetBuildingInfoByAreaNo", data=data, headers=headers, cookies=cookies_dict)
            if resp.status_code != 200:
                continue
            buildings = resp.json().get("dormList", [])
            for bld in buildings:
                bld_name, bld_no = bld.get("name", ""), bld.get("no", "")
                if bld_name and bld_no:
                    bld_map[(campus_name, bld_name)] = (bld_no, area_no)
                if bld_no:
                    ts2 = time.strftime("%Y%m%d%H%M%S")
                    sign2 = hashlib.md5(f"{area_no}|{bld_no}|{item_num}|{ts2}|{MD5_KEY}".encode()).hexdigest()
                    data2 = {"AreaNo": area_no, "BuildingNo": bld_no, "ItemNum": item_num, "Time": ts2, "Sign": sign2, "ContentType": "application/json"}
                    resp2 = http_post(f"{url}/GetRoomInfo", data=data2, headers=headers, cookies=cookies_dict)
                    if resp2.status_code != 200:
                        continue
                    rooms = resp2.json().get("dormList", [])
                    for room in rooms:
                        room_name, room_no = room.get("name", ""), room.get("no", "")
                        if room_no:
                            name_map[room_no] = f"{bld_name} {room_name}"
                        if bld_name and room_name and room_no:
                            rn_map[(bld_name, room_name)] = (room_no, bld_no, area_no, campus_name)
            log(f"📡 已拉取 {campus_name} 数据")
        except Exception as e:
            log(f"⚠️  {campus_name} API 拉取失败: {e}")

    if rn_map:
        _room_name_cache = name_map
        _building_no_cache = bld_map
        _room_no_cache = rn_map
        _campus_cache = {"loaded": True}
        log(f"✅ API 拉取完成: {len(name_map)} 个房间, {len(bld_map)} 个楼栋")
        try:
            cache = {
                "name_map": name_map,
                "bld_map": {"|".join(k): list(v) for k, v in bld_map.items()},
                "rn_map": {"|".join(k): list(v) for k, v in rn_map.items()},
            }
            with open(ROOMS_CACHE_FILE, 'w') as f:
                json.dump(cache, f)
        except:
            pass
        return True
    return False


def load_rooms_data():
    global _room_name_cache, _building_no_cache, _room_no_cache, _campus_cache
    if _room_name_cache is not None:
        return
    name_map, bld_map, rn_map = {}, {}, {}
    campus_files = [(JINAN_ROOMS_PATH, "济南校区", "1"), (YANTAI_ROOMS_PATH, "烟台校区", "0")]
    loaded = 0
    for filepath, campus_name, area_no in campus_files:
        if not os.path.exists(filepath):
            continue
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for building in data.get("buildings", []):
                bld_name, bld_no = building.get("building_name", ""), building.get("building_no", "")
                if bld_name and bld_no:
                    bld_map[(campus_name, bld_name)] = (bld_no, area_no)
                for room in building.get("rooms", []):
                    room_no, room_name = room.get("no", ""), room.get("name", "")
                    if room_no:
                        name_map[room_no] = f"{bld_name} {room_name}"
                    if bld_name and room_name and room_no:
                        rn_map[(bld_name, room_name)] = (room_no, bld_no, area_no, campus_name)
            loaded += 1
            log(f"📂 已加载 {campus_name} 房间数据")
        except Exception as e:
            log(f"⚠️  加载 {filepath} 失败: {e}")
    if loaded > 0:
        _room_name_cache = name_map
        _building_no_cache = bld_map
        _room_no_cache = rn_map
        _campus_cache = {"loaded": True}
        log(f"✅ 共加载 {len(name_map)} 个房间, {len(bld_map)} 个楼栋")
        return
    try:
        if os.path.exists(ROOMS_CACHE_FILE):
            with open(ROOMS_CACHE_FILE, 'r') as f:
                cache = json.load(f)
            name_map = cache.get("name_map", {})
            bld_map = {tuple(k.split("|")): tuple(v) for k, v in cache.get("bld_map", {}).items()}
            rn_map = {tuple(k.split("|")): tuple(v) for k, v in cache.get("rn_map", {}).items()}
            if rn_map:
                _room_name_cache = name_map
                _building_no_cache = bld_map
                _room_no_cache = rn_map
                _campus_cache = {"loaded": True}
                return
    except:
        pass
    if not fetch_rooms_via_api():
        log("❌ 无法获取房间数据")
        _room_name_cache, _building_no_cache, _room_no_cache, _campus_cache = {}, {}, {}, {}
        sys.exit(1)


def resolve_room_config():
    load_rooms_data()
    if BUILDING_NAME and ROOM_NAME:
        result = _room_no_cache.get((BUILDING_NAME, ROOM_NAME))
        if not result:
            log(f"❌ 未找到: {BUILDING_NAME} {ROOM_NAME}")
            sys.exit(1)
        room_no, bld_no, area_no, campus_name = result
        ROOM_CONFIG["BuildingNo"] = bld_no
        ROOM_CONFIG["RoomNo"] = room_no
        ROOM_CONFIG["AreaNo"] = area_no
        ROOM_CONFIG["AccNum"] = "0"
        ROOM_CONFIG["ItemNum"] = "6" if area_no == "0" else "2"
        log(f"🏠 房间: {BUILDING_NAME} {ROOM_NAME} ({campus_name}, room_no={room_no})")
    elif ROOM_CONFIG.get("BuildingNo") and ROOM_CONFIG.get("RoomNo"):
        load_rooms_data()
    else:
        log("❌ 请配置 BUILDING_NAME + ROOM_NAME")
        sys.exit(1)


def get_room_display():
    room_no = ROOM_CONFIG.get("RoomNo", "")
    if room_no:
        load_rooms_data()
        readable = _room_name_cache.get(room_no, "")
        if readable:
            return readable
    if BUILDING_NAME and ROOM_NAME:
        return f"{BUILDING_NAME} {ROOM_NAME}"
    return room_no or "未知房间"


# ====================== SSO 登录（v9.2 精确接口）======================

def rsa_encrypt(text, public_key_pem):
    pem = public_key_pem.strip()
    pem = pem.replace("-----BEGIN RSA Public Key-----", "-----BEGIN PUBLIC KEY-----")
    pem = pem.replace("-----END RSA Public Key-----", "-----END PUBLIC KEY-----")
    key = RSA.import_key(pem)
    cipher = PKCS1_v1_5.new(key)
    max_len = key.size_in_bytes() - 11
    data = text.encode('utf-8')
    encrypted = b''
    for i in range(0, len(data), max_len):
        encrypted += cipher.encrypt(data[i:i + max_len])
    return base64.b64encode(encrypted).decode('utf-8')


def save_cookies(cookies_dict):
    try:
        with open(COOKIE_FILE, 'w') as f:
            json.dump({"cookies": cookies_dict, "timestamp": time.time()}, f)
    except:
        pass


def save_ctticket_to_config(cookies_dict):
    """保存 CTTICKET 到脚本配置文件"""
    ctticket = cookies_dict.get("CTTICKET", cookies_dict.get("ctticket", ""))
    if not ctticket:
        return
    try:
        script_path = os.path.abspath(__file__)
        with open(script_path, 'r') as f:
            content = f.read()
        content = re.sub(r'CTTICKET = "[^"]*"', f'CTTICKET = "{ctticket}"', content)
        # 写入临时文件，再用 sudo cp
        with open("/tmp/query_ctticket.py", "w") as f:
            f.write(content)
        import os as _os
        _os.system('echo "PASSWORD_PLACEHOLDER." | sudo -S cp /tmp/query_ctticket.py ' + script_path + ' 2>/dev/null')
        log(f"💾 CTTICKET 已保存到脚本配置")
    except Exception as e:
        log(f"⚠️  保存 CTTICKET 失败: {e}")


def save_jwt_to_config(jwt_token):
    """保存 JWT/etToken 到脚本配置文件"""
    if not jwt_token:
        return
    try:
        script_path = os.path.abspath(__file__)
        with open(script_path, 'r') as f:
            content = f.read()
        content = re.sub(r'JWT_TOKEN = "[^"]*"', f'JWT_TOKEN = "{jwt_token}"', content)
        with open(script_path, 'w') as f:
            f.write(content)
        log(f"💾 JWT_TOKEN 已保存到脚本配置")
    except Exception as e:
        log(f"⚠️  保存 JWT_TOKEN 失败: {e}")


def load_cookies():
    try:
        with open(COOKIE_FILE, 'r') as f:
            data = json.load(f)
        age = (time.time() - data.get("timestamp", 0)) / 3600
        if age > 20:
            return None
        return data.get("cookies", {})
    except:
        return None


def sso_login():
    """
    SSO 登录获取 CTTICKET 和 etToken
    v9.2: 固定设备 ID + 图片验证码 + 短信验证码两步验证
    接口已根据实际抓包确认：
    - POST /ssoApi/getPhoneCaptcha  发送短信
    - POST /ssoApi/verifyPhone      提交短信验证码
    """
    global JWT_TOKEN
    if not SSO_USERNAME or not SSO_PASSWORD:
        log("⚠️  未配置 SSO 账号密码")
        return None

    log("🔐 SSO 登录中...")

    if USE_CURL_CFFI:
        from curl_cffi import requests as cffi
        session = cffi.Session(impersonate="chrome120")
    else:
        session = requests.Session()
    session.verify = False

    try:
        # 1. 获取 RSA 公钥
        resp = session.post(
            f"{SSO_BASE}/ssoApi/getLoginBasicInfo",
            data={"md5": "1"}, timeout=15
        )
        public_key = resp.json().get("data", {}).get("publicEn")
        if not public_key:
            log("❌ 获取公钥失败")
            return None

        # 2. RSA 加密
        enc_account = rsa_encrypt(SSO_USERNAME, public_key)
        enc_password = rsa_encrypt(SSO_PASSWORD, public_key)

        # 3. 固定设备 ID
        device = get_device_id()
        log(f"📱 设备 ID: {device[:16]}...")

        # 4. 构造登录请求
        boundary = "----BoundaryPython"
        fields = {
            "loginMode": "1", "account": enc_account, "password": enc_password,
            "clientType": "0", "code": "0x010040001",
            "locationurl": "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
            "device": device,
        }
        body = ""
        for k, v in fields.items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
        body += f"--{boundary}--\r\n"

        resp = session.post(
            f"{SSO_BASE}/ssoApi/verifyWebUser",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            data=body.encode('utf-8'), timeout=15
        )
        result = resp.json()
        resp_code = result.get("code", "")
        resp_msg = result.get("msg", "")

        log(f"📋 SSO 响应: code={resp_code}, msg={resp_msg}")

        # 5. 检测是否需要二次验证
        # 关键：即使 code=0x000000，如果 data.action=="secondary" 也需要二次验证
        need_2fa = False
        secondary_code = None  # 二次验证的 session code
        phone = None           # 脱敏手机号
        locationurl = "https://etong.sdjzu.edu.cn/easytong_webapp/"

        resp_data = result.get("data", {})
        if isinstance(resp_data, dict):
            secondary_code = resp_data.get("code")
            phone = resp_data.get("zh") or resp_data.get("phone") or resp_data.get("mobile")
            action = resp_data.get("action", "")
            if action == "secondary":
                need_2fa = True
            locat_url = resp_data.get("locatUrl", "")
            if locat_url:
                url_match = re.search(r'locationurl=([^&]+)', locat_url)
                if url_match:
                    from urllib.parse import unquote
                    locationurl = unquote(url_match.group(1))

        if not need_2fa and resp_code != "0x000000":
            msg_lower = resp_msg.lower()
            if any(kw in msg_lower for kw in ["验证", "短信", "二次", "2fa", "sms", "verify", "设备"]):
                need_2fa = True
            if not secondary_code:
                code_match = re.search(r'code=([a-f0-9]{32})', resp_msg)
                if code_match:
                    secondary_code = code_match.group(1)
            if not phone:
                phone_match = re.search(r'(\d{3}\*{4}\d{4})', resp_msg)
                if phone_match:
                    phone = phone_match.group(1)

        if need_2fa:
            log("🔐 检测到需要二次验证")
            log(f"   二次验证 code: {secondary_code or '未获取到'}")
            log(f"   手机号: {phone or '未获取到'}")

            if not phone:
                log("❌ 未获取到手机号，无法发送短信")
                log("💡 请检查 verifyWebUser 的返回数据中是否包含手机号")
                log(f"   完整返回: {json.dumps(result, ensure_ascii=False)}")
                return None

            if not secondary_code:
                log("⚠️  未获取到二次验证 code，尝试继续...")

            # 5.1 获取图片验证码
            captcha_data, captcha_id = fetch_captcha_image(session)
            if not captcha_data or not captcha_id:
                log("❌ 无法获取图片验证码或 captchaId")
                return None

            # 5.2 启动 web 服务完成两步验证
            success = run_2fa_web_server(
                session, phone, secondary_code, device, locationurl,
                captcha_data, captcha_id
            )
            if not success:
                log("❌ 二次验证未完成")
                return None

            # 5.3 验证通过后，跟随 SSO 重定向获取 CTTICKET
            log("🔄 二次验证完成，跟随重定向获取 CTTICKET...")
            
            # 访问二次验证成功后的重定向 URL
            redirect_url = f"{SSO_BASE}/?action=secondary&code={secondary_code}&before=0&type=0&zh={phone}&locationurl={unquote(locationurl)}"
            resp = session.get(redirect_url, timeout=15, allow_redirects=True)
            
            cookies = session.cookies.get_dict()
            log(f"🍪 重定向后 cookies: {list(cookies.keys())}")
            
            # 从页面中提取 etToken
            match = re.search(r"setCookie\('etToken',\s*'([^']+)'", resp.text)
            if match:
                log("✅ 已获取 etToken")
            
            if any("CTTICKET" in k.upper() for k in cookies):
                save_cookies(cookies)
                log("✅ 已获取 CTTICKET")
                
                # 保存 CTTICKET 到配置文件
                save_ctticket_to_config(cookies)
                
                # 提取 etToken 并保存
                match = re.search(r"setCookie\('etToken',\s*'([^']+)'", resp.text)
                if match:
                    JWT_TOKEN = match.group(1)
                    save_jwt_to_config(JWT_TOKEN)
                
                return cookies
            
            # 如果重定向后还是没有，尝试直接访问 etong 首页
            resp = session.get("https://etong.sdjzu.edu.cn/easytong_webapp/index.html", timeout=15)
            cookies = session.cookies.get_dict()
            if any("CTTICKET" in k.upper() for k in cookies):
                save_cookies(cookies)
                log("✅ 已获取 CTTICKET")
                
                # 保存 CTTICKET 到配置文件
                save_ctticket_to_config(cookies)
                
                # 提取 etToken
                match = re.search(r"setCookie\('etToken',\s*'([^']+)'", resp.text)
                if match:
                    JWT_TOKEN = match.group(1)
                    save_jwt_to_config(JWT_TOKEN)
                
                return cookies
            
            log("❌ 未获取到 CTTICKET")
            log(f"   当前 cookies: {list(cookies.keys())}")
            return None

        elif result.get("code") != "0x000000":
            log(f"❌ SSO 登录失败: {result.get('msg')}")
            return None

        # 如果不需要 2FA 且登录成功，直接获取 CTTICKET
        if not need_2fa:
            log("✅ SSO 登录成功（无需二次验证）")

        # 6. 获取 CTTICKET 和 etToken
        resp = session.get("https://etong.sdjzu.edu.cn/easytong_webapp/index.html", timeout=15)
        cookies = session.cookies.get_dict()

        match = re.search(r"setCookie\('etToken',\s*'([^']+)'", resp.text)
        if match:
            JWT_TOKEN = match.group(1)
            log("✅ 已获取 etToken")
        else:
            log("⚠️  未能从页面提取 etToken")

        if any("CTTICKET" in k.upper() for k in cookies):
            save_cookies(cookies)
            log("✅ 已获取 CTTICKET")
            
            # 保存 CTTICKET 到配置文件
            save_ctticket_to_config(cookies)
            
            # 提取 etToken 并保存
            match = re.search(r"setCookie\('etToken',\s*'([^']+)'", resp.text)
            if match:
                JWT_TOKEN = match.group(1)
                save_jwt_to_config(JWT_TOKEN)
            
            return cookies
        log("❌ 未获取到 CTTICKET")
        return None
    except Exception as e:
        log(f"❌ SSO 异常: {e}")
        import traceback
        traceback.print_exc()
        return None


# ====================== 电费查询 ======================

def query_balance(cookies_dict=None):
    url = "https://etong.sdjzu.edu.cn/easytong_app/GetPayAccInfoNew"
    ts = time.strftime("%Y%m%d%H%M%S")
    sign_str = f"0|{ROOM_CONFIG['AreaNo']}|{ROOM_CONFIG['BuildingNo']}|{ROOM_CONFIG['FloorNo']}|{ROOM_CONFIG['ItemNum']}|{ROOM_CONFIG['RoomNo']}|{ts}|{MD5_KEY}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    post_data = {**ROOM_CONFIG, "Time": ts, "Sign": sign, "ContentType": "application/json"}
    headers = {
        "Authorization": JWT_TOKEN, "h5req": "Y",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://etong.sdjzu.edu.cn",
        "Referer": "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    cookies = {"md5": "1", "etToken": JWT_TOKEN}
    if CTTICKET:
        cookies["CTTICKET"] = CTTICKET
        cookies["APPCTTICKET"] = CTTICKET
    if cookies_dict:
        cookies.update(cookies_dict)
    try:
        resp = http_post(url, data=post_data, headers=headers, cookies=cookies, timeout=30, allow_redirects=False)
        if resp.status_code == 302:
            log("❌ CTTICKET 已过期")
            return None
        result = resp.json()
        if result.get("code") == 1:
            return float(result.get("balance", 0))
        log(f"❌ 查询失败: {result.get('msg')}")
        return None
    except Exception as e:
        log(f"❌ 请求异常: {e}")
        return None


def get_balance():
    if CTTICKET:
        balance = query_balance()
        if balance is not None:
            return balance
        log("⚠️  CTTICKET 可能已过期")
    cached = load_cookies()
    if cached:
        balance = query_balance(cached)
        if balance is not None:
            return balance
    log("SSO 登录中...")
    try:
        new_cookies = sso_login()
        if new_cookies:
            return query_balance(new_cookies)
    except Exception as e:
        log(f"❌ SSO 登录失败: {e}")
    log("❌ 所有认证方式均失败")
    return None


# ====================== 推送通知 ======================

def send_notification(title, message):
    sent = False
    if WECOM_WEBHOOK:
        try:
            import requests as _req
            urllib3.disable_warnings()
            resp = _req.post(WECOM_WEBHOOK, json={"msgtype": "text", "text": {"content": f"{title}\n{message}"}}, timeout=10, verify=False)
            log(f"📱 企业微信推送成功 (status={resp.status_code})"); sent = True
        except Exception as e:
            log(f"⚠️  企业微信推送失败: {e}")
    if BARK_KEY:
        try:
            http_get(f"{BARK_KEY}/{title}/{message}", timeout=10)
            log("📱 Bark 推送成功"); sent = True
        except: pass
    if PUSHPLUS_TOKEN:
        try:
            http_post("http://www.pushplus.plus/send", json={"token": PUSHPLUS_TOKEN, "title": title, "content": message}, timeout=10)
            log("📱 PushPlus 推送成功"); sent = True
        except: pass
    if not sent:
        log("⚠️  未配置推送渠道")
    return sent


# ====================== 监控逻辑 ======================

def check_and_notify():
    now = datetime.now()
    state = load_state()
    log(f"⚡ 查询电量...")
    balance = get_balance()
    if balance is None:
        log("❌ 本次查询失败")
        if time.time() - state.get("last_fail_alert", 0) > 21600:
            send_notification("⚡ 电费查询失败", f"⏰ {now.strftime('%m-%d %H:%M')}\n请检查网络或 SSO 账号")
            state["last_fail_alert"] = time.time()
            save_state(state)
        return
    log(f"⚡ 当前电量: {balance} 度")
    state["last_balance"] = balance
    state["last_query_time"] = now.strftime('%Y-%m-%d %H:%M:%S')
    if balance <= LOW_BALANCE_THRESHOLD:
        last_alert = state.get("last_alert_time", 0)
        if time.time() - last_alert > ALERT_COOLDOWN:
            log(f"🚨 电量不足 {LOW_BALANCE_THRESHOLD} 度，发送告警！")
            room_display = get_room_display()
            send_notification("🚨 电费余额严重不足！",
                f"━━━━━━━━━━━━━━\n🏠 房间: {room_display}\n🔋 剩余: {balance} 度\n"
                f"⏰ 时间: {now.strftime('%m-%d %H:%M')}\n━━━━━━━━━━━━━━\n⚠️ 请立即充值！")
            state["last_alert_time"] = time.time()
    today_str = now.strftime('%Y-%m-%d')
    is_report_time = (now.hour == DAILY_REPORT_HOUR and DAILY_REPORT_MINUTE <= now.minute < DAILY_REPORT_MINUTE + 59)
    if is_report_time and state.get("last_daily_report") != today_str:
        log("📊 发送每日电量报告")
        status = "🟢 充足" if balance > 50 else "🟡 正常" if balance > 20 else "🟠 偏低" if balance > 10 else "🔴 不足"
        room_display = get_room_display()
        send_notification("📊 每日电量报告",
            f"━━━━━━━━━━━━━━\n🏠 房间: {room_display}\n🔋 剩余: {balance} 度\n"
            f"📶 状态: {status}\n📅 日期: {today_str}\n━━━━━━━━━━━━━━")
        state["last_daily_report"] = today_str
    save_state(state)


_heartbeat_stop = False
import threading

def heartbeat_loop():
    """心跳保活：每 10 分钟访问 etong 首页，刷新 CTTICKET"""
    global _heartbeat_stop
    log("💓 心跳保活线程已启动 (每 600 秒)")
    while not _heartbeat_stop:
        import time as _t
        _t.sleep(600)
        if _heartbeat_stop:
            break
        try:
            # 使用保存的 cookies 访问 etong 首页保持 session
            import requests as _req
            _s = _req.Session()
            _s.verify = False
            # 加载保存的 cookies
            try:
                with open("/tmp/etong_cookies.json", 'r') as _f:
                    _c = json.load(_f).get("cookies", {})
                for _k, _v in _c.items():
                    _s.cookies.set(_k, _v, domain=".sdjzu.edu.cn")
            except:
                log("💓 心跳保活: 无 cookies，跳过")
                continue
            # 访问 etong 首页
            try:
                _r = _s.get("https://etong.sdjzu.edu.cn/easytong_webapp/index.html", timeout=10, allow_redirects=True)
                if any("CTTICKET" in k.upper() for k in _s.cookies.get_dict()):
                    _nc = _s.cookies.get_dict()
                    with open("/tmp/etong_cookies.json", 'w') as _f:
                        json.dump({"cookies": _nc, "timestamp": _t.time()}, _f)
                    log("💓 心跳保活: session 已刷新")
                else:
                    log("💓 心跳保活: session 可能已过期")
            except Exception as _e:
                log(f"💓 心跳保活异常: {_e}")
        except Exception as _e:
            log(f"⚠️  心跳保活失败: {_e}")
    log("💓 心跳保活线程已停止")


def daemon_mode():
    resolve_room_config()
    log("=" * 50)
    log("⚡ 电费监控服务启动 (v9.0)")
    log(f"📋 房间: {get_room_display()}")
    log(f"⏰ 检查间隔: {CHECK_INTERVAL}秒")
    log(f"📊 日报时间: 每天 {DAILY_REPORT_HOUR}:{DAILY_REPORT_MINUTE:02d}")
    log(f"🚨 告警阈值: {LOW_BALANCE_THRESHOLD} 度")
    log(f"🛡️ 指纹模式: {'curl_cffi (Chrome)' if USE_CURL_CFFI else 'requests (普通)'}")
    log(f"📱 验证码端口: {SMS_SERVER_PORT}")
    log("💓 心跳间隔: 600秒")
    log("=" * 50)
    
    # 启动心跳保活
    global _heartbeat_stop
    _heartbeat_stop = False
    _hb = threading.Thread(target=heartbeat_loop, daemon=True)
    _hb.start()
    
    check_and_notify()
    while running:
        now = datetime.now()
        next_check = now.timestamp() + CHECK_INTERVAL
        today_report = now.replace(hour=DAILY_REPORT_HOUR, minute=DAILY_REPORT_MINUTE, second=0, microsecond=0)
        if today_report.timestamp() > now.timestamp():
            secs_to_report = today_report.timestamp() - now.timestamp()
            if secs_to_report < CHECK_INTERVAL:
                next_check = today_report.timestamp()
        wait_secs = max(next_check - time.time(), 60)
        log(f"💤 下次检查: {datetime.fromtimestamp(next_check).strftime('%H:%M:%S')} ({wait_secs/60:.0f}分钟后)")
        wait_until = time.time() + wait_secs
        while running and time.time() < wait_until:
            time.sleep(10)
        if running:
            check_and_notify()
    _heartbeat_stop = True
    time.sleep(1)
    log("⚡ 电费监控服务已停止")


def once_mode():
    resolve_room_config()
    log("=" * 50)
    log("⚡ 单次查询模式（强制推送）")
    balance = get_balance()
    now = datetime.now().strftime('%m-%d %H:%M')
    if balance is not None:
        log(f"⚡ 当前电量: {balance} 度")
        status = "🟢 充足" if balance > 50 else "🟡 正常" if balance > 20 else "🟠 偏低" if balance > 10 else "🔴 不足"
        room_display = get_room_display()
        send_notification("⚡ 电费查询结果",
            f"━━━━━━━━━━━━━━\n🏠 房间: {room_display}\n🔋 剩余: {balance} 度\n"
            f"📶 状态: {status}\n⏰ 时间: {now}\n━━━━━━━━━━━━━━")
    else:
        send_notification("⚡ 电费查询失败", f"⏰ {now}\n请检查配置")
    log("=" * 50)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        once_mode()
    else:
        daemon_mode()
