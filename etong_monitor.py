#!/usr/bin/env python3
"""
山东建筑大学 电费监控服务 v9.4（重构版）
- 每小时检查电量，低于阈值立即告警
- 每天定时推送当日电量日报
- 支持 systemd 开机自启
- 📡 无本地数据时自动通过 API 拉取楼栋和房间列表
- 双校区自动识别：同时加载 rooms.json + 烟台校区_rooms.json
- 🔑 动态签名：无需抓包，自动计算 Sign
- 🛡️ 浏览器指纹模拟：使用 curl_cffi 模拟 Chrome TLS 指纹，防止 CTTICKET 过期
- 🔐 自动处理二次验证：固定设备 ID，首次 2FA 后永久免验证
- 📱 手机验证码接收：临时 HTTP 服务，手机浏览器完成图片验证码+短信验证码
- 🔒 凭据从环境变量 / .env 文件加载，不再硬编码在源码中

GitHub: https://github.com/jichie/sjzu-etong-monitor
"""

import hashlib
import json
import sys
import time
import os
import signal
import threading
import re
import socket
import base64
import urllib3
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote
from typing import Optional, Dict, Any, Tuple

# ====================== 三方库导入 ======================

try:
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
except ImportError:
    try:
        from Cryptodome.PublicKey import RSA
        from Cryptodome.Cipher import PKCS1_v1_5
    except ImportError:
        print("❌ 缺少依赖: pip3 install pycryptodome")
        sys.exit(1)

# curl_cffi — 可选，用于模拟 Chrome TLS 指纹
USE_CURL_CFFI: bool = False
try:
    from curl_cffi import requests as cffi_requests
    USE_CURL_CFFI = True
except ImportError:
    try:
        import requests
    except ImportError:
        print("❌ 缺少依赖: pip3 install curl_cffi 或 pip3 install requests")
        sys.exit(1)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ====================== 配置加载 ======================

# 尝试加载 python-dotenv（可选）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except ImportError:
    pass


def _env_str(key: str, default: str = "") -> str:
    """从环境变量读取字符串配置"""
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    """从环境变量读取浮点配置"""
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int) -> int:
    """从环境变量读取整数配置"""
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


# --- 登录账号（从环境变量读取，优先 .env 文件）---
SSO_USERNAME: str = _env_str("SSO_USERNAME", "")
SSO_PASSWORD: str = _env_str("SSO_PASSWORD", "")

# --- 房间配置 ---
BUILDING_NAME: str = _env_str("BUILDING_NAME", "梅二-照明")
ROOM_NAME: str = _env_str("ROOM_NAME", "413")

# --- MD5 签名密钥 ---
MD5_KEY: str = "ok15we1@oid8x5afd@"

# --- 认证 Token（从环境变量读取）---
JWT_TOKEN: str = _env_str("JWT_TOKEN", "")
CTTICKET: str = _env_str("CTTICKET", "")

# --- 推送配置 ---
WECOM_WEBHOOK: str = _env_str("WECOM_WEBHOOK", "")
BARK_KEY: str = _env_str("BARK_KEY", "")
PUSHPLUS_TOKEN: str = _env_str("PUSHPLUS_TOKEN", "")

# --- 监控设置 ---
LOW_BALANCE_THRESHOLD: float = _env_float("LOW_BALANCE_THRESHOLD", 10.0)
CHECK_INTERVAL: int = _env_int("CHECK_INTERVAL", 3600)
DAILY_REPORT_HOUR: int = _env_int("DAILY_REPORT_HOUR", 19)
DAILY_REPORT_MINUTE: int = _env_int("DAILY_REPORT_MINUTE", 10)
ALERT_COOLDOWN: int = _env_int("ALERT_COOLDOWN", 21600)

# --- 二次验证设置 ---
SMS_SERVER_PORT: int = _env_int("SMS_SERVER_PORT", 8899)
SMS_CODE_TIMEOUT: int = _env_int("SMS_CODE_TIMEOUT", 300)

# --- 路径常量 ---
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
JINAN_ROOMS_PATH: str = os.path.join(PROJECT_DIR, "rooms.json")
YANTAI_ROOMS_PATH: str = os.path.join(PROJECT_DIR, "烟台校区_rooms.json")
CONFIG_FILE: str = os.path.join(PROJECT_DIR, "config.json")
COOKIE_FILE: str = "/tmp/etong_cookies.json"
STATE_FILE: str = "/tmp/etong_state.json"
ROOMS_CACHE_FILE: str = "/tmp/etong_rooms_cache.json"
LOG_FILE: str = "/var/log/etong.log"

# --- 房间查询参数（会在 resolve_room_config 中填充）---
ROOM_CONFIG: Dict[str, str] = {
    "AccNum": "0",
    "AreaNo": "1",
    "BuildingNo": "",
    "FloorNo": "0",
    "ItemNum": "2",
    "RoomNo": "",
}

# ====================== 日志工具 ======================

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("etong")


def log(msg: str) -> None:
    """带时间戳的日志输出（控制台 + 文件）"""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except OSError as e:
        logger.warning("无法写入日志文件 %s: %s", LOG_FILE, e)


# ====================== 状态管理 ======================

def load_state() -> Dict[str, Any]:
    """从 STATE_FILE 加载持久化状态"""
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_alert_time": 0, "last_daily_report": "", "last_balance": None}


def save_state(state: Dict[str, Any]) -> None:
    """持久化状态到 STATE_FILE"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except OSError as e:
        log(f"⚠️  保存状态失败: {e}")


# ====================== HTTP 请求封装 ======================

def http_get(url: str, **kwargs) -> "requests.Response":
    """GET 请求封装（curl_cffi 优先）"""
    kwargs.setdefault("timeout", 15)
    kwargs.setdefault("verify", False)
    if USE_CURL_CFFI:
        kwargs.setdefault("impersonate", "chrome120")
        return cffi_requests.get(url, **kwargs)
    return requests.get(url, **kwargs)


def http_post(url: str, **kwargs) -> "requests.Response":
    """POST 请求封装（curl_cffi 优先）"""
    kwargs.setdefault("timeout", 15)
    kwargs.setdefault("verify", False)
    if USE_CURL_CFFI:
        kwargs.setdefault("impersonate", "chrome120")
        return cffi_requests.post(url, **kwargs)
    return requests.post(url, **kwargs)


# ====================== 工具函数 ======================

def build_multipart(fields: Dict[str, str], boundary: str = "----Boundary") -> Tuple[str, str]:
    """
    构造 multipart/form-data 请求体。

    返回 (body_string, content_type_string)
    """
    CRLF = "\r\n"
    body_parts = []
    for k, v in fields.items():
        body_parts.append(f"--{boundary}{CRLF}")
        body_parts.append(f'Content-Disposition: form-data; name="{k}"{CRLF}{CRLF}')
        body_parts.append(f"{v}{CRLF}")
    body_parts.append(f"--{boundary}--{CRLF}")
    body = "".join(body_parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def get_device_id() -> str:
    """生成固定的设备 ID（基于学号，避免每次随机触发二次验证）"""
    return hashlib.md5(f"etong_monitor_{SSO_USERNAME}_sjzu_2026".encode()).hexdigest()


def get_local_ip() -> str:
    """获取本机内网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "你的服务器IP"


# ====================== 签名算法 ======================

def sign_request(*parts: str) -> str:
    """MD5 签名：参数用 | 连接，末尾拼接 MD5_KEY"""
    raw = "|".join(parts) + f"|{MD5_KEY}"
    return hashlib.md5(raw.encode()).hexdigest()


# ====================== RSA 加密（SSO 登录用）======================

def rsa_encrypt(text: str, public_key_pem: str) -> str:
    """RSA 公钥加密分段数据，返回 base64 编码结果"""
    pem = public_key_pem.strip()
    pem = pem.replace("-----BEGIN RSA Public Key-----", "-----BEGIN PUBLIC KEY-----")
    pem = pem.replace("-----END RSA Public Key-----", "-----END PUBLIC KEY-----")
    key = RSA.import_key(pem)
    cipher = PKCS1_v1_5.new(key)
    max_len = key.size_in_bytes() - 11
    data = text.encode('utf-8')
    encrypted = b""
    for i in range(0, len(data), max_len):
        encrypted += cipher.encrypt(data[i:i + max_len])
    return base64.b64encode(encrypted).decode('utf-8')


# ====================== Cookie / Token 持久化 ======================

def load_cookies() -> Optional[Dict[str, str]]:
    """加载缓存的 cookies，超过 20 小时返回 None"""
    try:
        with open(COOKIE_FILE, 'r') as f:
            data = json.load(f)
        age_hours = (time.time() - data.get("timestamp", 0)) / 3600
        if age_hours > 20:
            return None
        return data.get("cookies", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_cookies(cookies_dict: Dict[str, str]) -> None:
    """持久化 cookies"""
    try:
        with open(COOKIE_FILE, 'w') as f:
            json.dump({"cookies": cookies_dict, "timestamp": time.time()}, f)
    except OSError as e:
        log(f"⚠️  保存 cookies 失败: {e}")


def save_token_to_config_file(token_type: str, token_value: str) -> None:
    """
    将 JWT_TOKEN / CTTICKET 持久化到 config.json（权限 600）。
    不再用 sudo 修改自身源码。
    """
    if not token_value:
        return
    try:
        config: Dict[str, str] = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        config[token_type] = token_value
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        # 设置权限为 600（仅所有者可读写）
        os.chmod(CONFIG_FILE, 0o600)
        log(f"💾 {token_type} 已保存到 {CONFIG_FILE}")
    except OSError as e:
        log(f"⚠️  保存 {token_type} 失败: {e}")


def load_tokens_from_config() -> None:
    """从 config.json 恢复 JWT_TOKEN / CTTICKET（环境变量未设置时）"""
    global JWT_TOKEN, CTTICKET
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        if not JWT_TOKEN:
            JWT_TOKEN = config.get("JWT_TOKEN", "")
        if not CTTICKET:
            CTTICKET = config.get("CTTICKET", "")
        if config.get("JWT_TOKEN") or config.get("CTTICKET"):
            log("📂 已从 config.json 加载认证 Token")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log(f"⚠️  读取 config.json 失败: {e}")


def save_auth_tokens(cookies: Dict[str, str], resp_text: str) -> Dict[str, str]:
    """
    从 cookies 和页面响应中提取并保存 CTTICKET / JWT_TOKEN。
    返回更新后的 cookies 字典。
    """
    global JWT_TOKEN

    if any("CTTICKET" in k.upper() for k in cookies):
        save_cookies(cookies)
        save_token_to_config_file("CTTICKET", cookies.get("CTTICKET", ""))
        log("✅ 已获取 CTTICKET")
    else:
        log("⚠️  未获取到 CTTICKET")

    match = re.search(r"setCookie\('etToken',\s*'([^']+)'", resp_text)
    if match:
        JWT_TOKEN = match.group(1)
        save_token_to_config_file("JWT_TOKEN", JWT_TOKEN)
        log("✅ 已获取 etToken")
    else:
        log("⚠️  未能从页面提取 etToken")

    return cookies


# ====================== SSO 二次验证（2FA）======================

SSO_BASE = "https://sso.sdjzu.edu.cn"

# 图片验证码候选接口（按优先级排列）
CAPTCHA_VERIFY_URLS = [
    f"{SSO_BASE}/ssoApi/verifyCaptcha",
    f"{SSO_BASE}/ssoApi/checkPictureVerifyCode",
    f"{SSO_BASE}/ssoApi/verifyPictureCode",
    f"{SSO_BASE}/ssoApi/checkCaptcha",
    f"{SSO_BASE}/ssoApi/checkImgCode",
]


def fetch_captcha_image(session) -> Tuple[Optional[bytes], Optional[str]]:
    """
    获取图片验证码，返回 (图片二进制数据, captchaId) 或 (None, None)
    """
    fields = {"chkHeight": "38", "chkWidth": "90"}
    boundary = "----BoundaryCaptcha"
    body, content_type = build_multipart(fields, boundary)

    try:
        resp = session.post(
            f"{SSO_BASE}/ssoApi/getPictureVerifyCode",
            headers={"Content-Type": content_type},
            data=body.encode('utf-8'),
            timeout=15,
        )
        if resp.status_code != 200:
            log(f"⚠️  获取图片验证码 HTTP {resp.status_code}")
            return None, None

        data = resp.json()
        inner = data.get("data", data)
        if not isinstance(inner, dict):
            log("⚠️  图片验证码响应格式异常")
            return None, None

        b64_str = inner.get("base64") or ""
        captcha_id = inner.get("captchaId") or ""
        if not b64_str or not captcha_id:
            log("⚠️  验证码响应缺少 base64 或 captchaId")
            return None, None

        if "," in b64_str:
            b64_str = b64_str.split(",", 1)[1]
        img_data = base64.b64decode(b64_str)
        log(f"✅ 获取图片验证码成功，captchaId: {captcha_id}")
        return img_data, captcha_id
    except Exception as e:
        log(f"⚠️  获取图片验证码异常: {e}")
        return None, None


def submit_img_captcha(session, captcha_code: str, captcha_id: str) -> Tuple[bool, str]:
    """
    提交图片验证码验证，循环尝试候选接口。
    返回 (是否成功, 错误消息)
    """
    for idx, verify_url in enumerate(CAPTCHA_VERIFY_URLS):
        fields = {"captchaId": captcha_id, "verifyValue": captcha_code}
        boundary = "----BoundaryVerify"
        body, content_type = build_multipart(fields, boundary)

        try:
            resp = session.post(
                verify_url,
                headers={"Content-Type": content_type},
                data=body.encode('utf-8'),
                timeout=15,
            )
            result = resp.json()
            msg = result.get("msg", "")
            if result.get("code") == "0x000000" or result.get("success") or result.get("status") == 1:
                log(f"✅ 图片验证码验证成功（接口 {idx+1}）")
                return True, ""
            else:
                log(f"⚠️  接口 {idx+1} 验证失败: {msg}")
        except Exception as e:
            log(f"⚠️  接口 {idx+1} 异常: {e}")

    return False, "图片验证码验证失败，已尝试所有候选接口"


def send_sms_code(session, phone: str, secondary_code: Optional[str] = None) -> Tuple[bool, str]:
    """调用 getPhoneCaptcha 发送短信验证码"""
    fields: Dict[str, str] = {
        "loginMode": "2",
        "phone": phone,
    }
    if secondary_code:
        fields["code"] = secondary_code

    boundary = "----BoundarySMS"
    body, content_type = build_multipart(fields, boundary)

    try:
        resp = session.post(
            f"{SSO_BASE}/ssoApi/getPhoneCaptcha",
            headers={"Content-Type": content_type},
            data=body.encode('utf-8'),
            timeout=15,
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


def submit_sms_code(session, phone: str, sms_code: str,
                    secondary_code: str, device: str, locationurl: str) -> bool:
    """调用 verifyPhone 提交短信验证码"""
    fields: Dict[str, str] = {
        "loginMode": "2",
        "phone": phone,
        "verifyMsgCode": sms_code,
        "code": secondary_code,
        "locationurl": locationurl,
        "device": device,
    }
    boundary = "----BoundaryVerify"
    body, content_type = build_multipart(fields, boundary)

    try:
        resp = session.post(
            f"{SSO_BASE}/ssoApi/verifyPhone",
            headers={"Content-Type": content_type},
            data=body.encode('utf-8'),
            timeout=15,
        )
        result = resp.json()
        if result.get("code") == "0x000000" or result.get("success") or result.get("status") == 1:
            log("✅ 短信验证通过！后续不再需要验证码了")
            return True
        else:
            log(f"⚠️  短信验证失败: {result.get('msg', str(result))}")
            return False
    except Exception as e:
        log(f"⚠️  提交短信异常: {e}")
        return False


def run_2fa_web_server(session, phone: str, secondary_code: str,
                       device: str, locationurl: str,
                       captcha_data: bytes, captcha_id: str,
                       timeout: int = SMS_CODE_TIMEOUT) -> bool:
    """
    启动两步验证的 HTTP 服务：
    Step 1: 显示图片验证码 → 用户输入 → 提交验证 → 发送短信
    Step 2: 显示短信验证码输入框 → 用户输入 → 提交验证
    """
    state = {
        "step": 1,
        "done": False,
        "error": None,
    }

    captcha_b64 = base64.b64encode(captcha_data).decode()
    local_ip = get_local_ip()

    step1_html = _build_step1_html(captcha_b64)
    step2_html = _build_step2_html()

    class Handler(BaseHTTPRequestHandler):
        def _send_html(self, html: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))

        def _send_json(self, data: Dict) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_GET(self) -> None:
            nonlocal captcha_data, captcha_b64, captcha_id
            if state["step"] == 1:
                self._send_html(step1_html)
            elif state["step"] == 2 and self.path == "/step2":
                self._send_html(step2_html)
            else:
                self._send_html("<h2>已完成</h2>")

        def do_POST(self) -> None:
            nonlocal captcha_data, captcha_b64, captcha_id
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            params = parse_qs(body)

            if self.path == "/refresh_captcha":
                new_img, new_id = fetch_captcha_image(session)
                if new_img and new_id:
                    captcha_data, captcha_id = new_img, new_id
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

                # 提交图片验证码 + 发送短信
                submit_img_captcha(session, captcha, captcha_id)
                sms_ok, sms_err = send_sms_code(session, phone, secondary_code)

                if sms_ok:
                    state["step"] = 2
                    self._send_json({"ok": True})
                else:
                    error_msg = sms_err or "短信发送失败"
                    self._send_json({"ok": False, "msg": error_msg})

            elif self.path == "/submit_sms":
                sms = params.get("sms", [""])[0].strip()
                if not sms or len(sms) < 4:
                    self._send_json({"ok": False, "msg": "请输入有效验证码"})
                    return
                log(f"📋 用户输入短信验证码: {sms}")

                ok = submit_sms_code(session, phone, sms, secondary_code, device, locationurl)
                if ok:
                    state["done"] = True
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "msg": "短信验证码错误，请重新输入"})
            else:
                self._send_json({"ok": False, "msg": "unknown"})

        def log_message(self, *args) -> None:
            pass

    import socketserver

    class ReusableTCPServer(HTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True

    server = ReusableTCPServer(("0.0.0.0", SMS_SERVER_PORT), Handler)
    server.timeout = 5

    log(f"📱 验证服务已启动（端口 {SMS_SERVER_PORT}）")
    log(f"👉 手机浏览器打开: http://{local_ip}:{SMS_SERVER_PORT}")

    send_notification(
        "🔐 电费脚本需要验证",
        f"📱 手机浏览器打开:\nhttp://{local_ip}:{SMS_SERVER_PORT}\n"
        f"完成两步验证即可\n⏰ {timeout}秒内完成",
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


def _build_step1_html(captcha_b64: str) -> str:
    """生成第一步：图片验证码页面"""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>电费验证 - 第1步</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;display:flex;align-items:center;justify-content:center}}
.card{{background:white;border-radius:16px;padding:30px 24px;width:90%;max-width:380px;box-shadow:0 20px 60px rgba(0,0,0,0.3);text-align:center}}
h2{{color:#333;margin-bottom:6px;font-size:20px}}
.subtitle{{color:#888;font-size:13px;margin-bottom:20px}}
.step-badge{{display:inline-block;background:#667eea;color:white;padding:4px 12px;border-radius:20px;font-size:12px;margin-bottom:16px}}
.captcha-img{{border:2px solid #e0e0e0;border-radius:8px;margin:16px auto;display:block}}
.captcha-hint{{color:#999;font-size:12px;margin-bottom:16px}}
input{{width:100%;font-size:24px;text-align:center;padding:12px;border:2px solid #e0e0e0;border-radius:10px;outline:none;letter-spacing:6px}}
input:focus{{border-color:#667eea}}
button{{width:100%;margin-top:16px;padding:14px;font-size:17px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;border:none;border-radius:10px;cursor:pointer;font-weight:bold}}
button:active{{opacity:0.8}}button:disabled{{opacity:0.5;cursor:not-allowed}}
.error{{color:#f44336;font-size:13px;margin-top:10px;display:none}}
.loading{{display:none;color:#667eea;font-size:14px;margin-top:10px}}
</style></head><body>
<div class="card"><div class="step-badge">步骤 1/2</div>
<h2>🔐 图片验证码</h2><p class="subtitle">请输入下方图片中的字符</p>
<img class="captcha-img" src="data:image/png;base64,{captcha_b64}" alt="验证码">
<p class="captcha-hint">看不清？<a href="javascript:void(0)" onclick="refreshCaptcha()" style="color:#667eea;text-decoration:none;font-weight:bold;">点击刷新图片</a></p>
<form id="form">
<input id="captchaInput" type="text" placeholder="图片验证码" maxlength="6" autofocus autocomplete="off">
<button type="submit" id="btn">发送短信验证码 →</button></form>
<p class="loading" id="loading">⏳ 正在发送短信...</p><p class="error" id="error"></p></div>
<script>
document.getElementById('form').addEventListener('submit',function(e){{e.preventDefault();
var c=document.getElementById('captchaInput').value.trim();if(!c)return;
var btn=document.getElementById('btn');btn.textContent='提交中...';btn.disabled=true;
document.getElementById('loading').style.display='block';
fetch('/submit_captcha',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'captcha='+encodeURIComponent(c)}})
.then(r=>r.json()).then(d=>{{if(d.ok){{window.location.href='/step2'}}else{{document.getElementById('loading').style.display='none';
document.getElementById('error').textContent=d.msg||'提交失败';document.getElementById('error').style.display='block';
btn.textContent='发送短信验证码 →';btn.disabled=false}}}})
.catch(err=>{{document.getElementById('loading').style.display='none';
document.getElementById('error').textContent='网络错误: '+err;document.getElementById('error').style.display='block';
btn.textContent='发送短信验证码 →';btn.disabled=false}});
}});
function refreshCaptcha(){{fetch('/refresh_captcha',{{method:'POST'}}).then(r=>r.json()).then(d=>{{if(d.ok){{document.querySelector('.captcha-img').src=d.img;
document.getElementById('captchaInput').value='';document.getElementById('error').style.display='none'}}else{{alert('刷新失败: '+d.msg)}}}})
.catch(err=>alert('网络错误: '+err))}}
</script></body></html>"""


def _build_step2_html() -> str:
    """生成第二步：短信验证码页面"""
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>电费验证 - 第2步</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:linear-gradient(135deg,#43a047 0%,#1b5e20 100%);min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:white;border-radius:16px;padding:30px 24px;width:90%;max-width:380px;box-shadow:0 20px 60px rgba(0,0,0,0.3);text-align:center}
h2{color:#333;margin-bottom:6px;font-size:20px}
.subtitle{color:#888;font-size:13px;margin-bottom:20px}
.step-badge{display:inline-block;background:#43a047;color:white;padding:4px 12px;border-radius:20px;font-size:12px;margin-bottom:16px}
.sms-icon{font-size:48px;margin:10px 0}
input{width:100%;font-size:28px;text-align:center;padding:14px;border:2px solid #e0e0e0;border-radius:10px;outline:none;letter-spacing:8px}
input:focus{border-color:#43a047}
button{width:100%;margin-top:16px;padding:14px;font-size:17px;background:linear-gradient(135deg,#43a047 0%,#1b5e20 100%);color:white;border:none;border-radius:10px;cursor:pointer;font-weight:bold}
button:active{opacity:0.8}button:disabled{opacity:0.5;cursor:not-allowed}
.success{display:none;color:#43a047;font-size:18px;margin-top:20px}
.error{color:#f44336;font-size:13px;margin-top:10px;display:none}
</style></head><body>
<div class="card"><div class="step-badge">步骤 2/2 ✓</div>
<h2>📱 短信验证码</h2><p class="subtitle">短信已发送到你的手机</p><div class="sms-icon">📨</div>
<form id="form"><input id="smsInput" type="text" inputmode="numeric" placeholder="短信验证码" maxlength="6" autofocus autocomplete="off">
<button type="submit" id="btn">完成验证 ✓</button></form>
<p class="error" id="error"></p><p class="success" id="success">✅ 验证成功！可以关闭此页面</p></div>
<script>
document.getElementById('form').addEventListener('submit',function(e){e.preventDefault();
var c=document.getElementById('smsInput').value.trim();if(!c)return;
var btn=document.getElementById('btn');btn.textContent='验证中...';btn.disabled=true;
fetch('/submit_sms',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'sms='+encodeURIComponent(c)})
.then(r=>r.json()).then(d=>{if(d.ok){document.getElementById('form').style.display='none';
document.getElementById('success').style.display='block'}else{document.getElementById('error').textContent=d.msg||'验证失败';
document.getElementById('error').style.display='block';btn.textContent='完成验证 ✓';btn.disabled=false}})
.catch(err=>{document.getElementById('error').textContent='网络错误: '+err;
document.getElementById('error').style.display='block';btn.textContent='完成验证 ✓';btn.disabled=false})});
</script></body></html>"""


# ====================== 房间数据管理 ======================

# 全局缓存
_room_name_cache: Dict[str, str] = {}
_building_no_cache: Dict[Tuple[str, str], Tuple[str, str]] = {}
_room_no_cache: Dict[Tuple[str, str], Tuple[str, str, str, str]] = {}
_campus_cache: Dict[str, Any] = {}


def fetch_rooms_via_api() -> bool:
    """
    通过 API 拉取全校房间数据，缓存到内存和文件。
    返回是否成功。
    """
    global _room_name_cache, _building_no_cache, _room_no_cache, _campus_cache

    log("🌐 未找到本地数据，尝试通过 API 拉取...")

    if not CTTICKET and (not SSO_USERNAME or not SSO_PASSWORD):
        return False

    cookies: Dict[str, str] = {}
    if not CTTICKET:
        cookies = sso_login()
        if not cookies:
            return False

    name_map: Dict[str, str] = {}
    bld_map: Dict[Tuple[str, str], Tuple[str, str]] = {}
    rn_map: Dict[Tuple[str, str], Tuple[str, str, str, str]] = {}

    campuses = [("1", "2", "济南校区"), ("0", "6", "烟台校区")]
    url = "https://etong.sdjzu.edu.cn/easytong_app"
    headers = {
        "Authorization": JWT_TOKEN,
        "h5req": "Y",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    cookies_dict: Dict[str, str] = {"md5": "1", "etToken": JWT_TOKEN}
    cookies_dict.update(cookies)

    for area_no, item_num, campus_name in campuses:
        try:
            ts = time.strftime("%Y%m%d%H%M%S")
            sign = sign_request(area_no, item_num, ts)
            data = {
                "AreaNo": area_no, "ItemNum": item_num, "Time": ts,
                "Sign": sign, "ContentType": "application/json",
            }
            resp = http_post(f"{url}/GetBuildingInfoByAreaNo", data=data,
                             headers=headers, cookies=cookies_dict)
            if resp.status_code != 200:
                continue
            buildings = resp.json().get("dormList", [])
            for bld in buildings:
                bld_name, bld_no = bld.get("name", ""), bld.get("no", "")
                if bld_name and bld_no:
                    bld_map[(campus_name, bld_name)] = (bld_no, area_no)
                if bld_no:
                    ts2 = time.strftime("%Y%m%d%H%M%S")
                    sign2 = sign_request(area_no, bld_no, item_num, ts2)
                    data2 = {
                        "AreaNo": area_no, "BuildingNo": bld_no,
                        "ItemNum": item_num, "Time": ts2,
                        "Sign": sign2, "ContentType": "application/json",
                    }
                    resp2 = http_post(f"{url}/GetRoomInfo", data=data2,
                                      headers=headers, cookies=cookies_dict)
                    if resp2.status_code != 200:
                        continue
                    rooms = resp2.json().get("dormList", [])
                    for room in rooms:
                        room_name, room_no = room.get("name", ""), room.get("no", "")
                        if room_no:
                            name_map[room_no] = f"{bld_name} {room_name}"
                        if bld_name and room_name and room_no:
                            rn_map[(bld_name, room_name)] = (
                                room_no, bld_no, area_no, campus_name
                            )
            log(f"📡 已拉取 {campus_name} 数据")
        except Exception as e:
            log(f"⚠️  {campus_name} API 拉取失败: {e}")

    if not rn_map:
        log("❌ API 拉取未能获取任何房间数据")
        return False

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
    except OSError as e:
        log(f"⚠️  保存房间缓存失败: {e}")

    return True


def load_rooms_data() -> None:
    """
    加载房间数据，优先级：
    1. JSON 本地文件（rooms.json / 烟台校区_rooms.json）
    2. 临时缓存文件（/tmp/etong_rooms_cache.json）
    3. API 实时拉取
    """
    global _room_name_cache, _building_no_cache, _room_no_cache, _campus_cache

    if _campus_cache.get("loaded"):
        return

    name_map: Dict[str, str] = {}
    bld_map: Dict[Tuple[str, str], Tuple[str, str]] = {}
    rn_map: Dict[Tuple[str, str], Tuple[str, str, str, str]] = {}

    # 优先加载本地 JSON
    campus_files = [
        (JINAN_ROOMS_PATH, "济南校区", "1"),
        (YANTAI_ROOMS_PATH, "烟台校区", "0"),
    ]
    loaded_count = 0
    for filepath, campus_name, area_no in campus_files:
        if not os.path.exists(filepath):
            continue
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for building in data.get("buildings", []):
                bld_name = building.get("building_name", "")
                bld_no = building.get("building_no", "")
                if bld_name and bld_no:
                    bld_map[(campus_name, bld_name)] = (bld_no, area_no)
                for room in building.get("rooms", []):
                    room_no = room.get("no", "")
                    room_name = room.get("name", "")
                    if room_no:
                        name_map[room_no] = f"{bld_name} {room_name}"
                    if bld_name and room_name and room_no:
                        rn_map[(bld_name, room_name)] = (
                            room_no, bld_no, area_no, campus_name
                        )
            loaded_count += 1
            log(f"📂 已加载 {campus_name} 房间数据")
        except Exception as e:
            log(f"⚠️  加载 {filepath} 失败: {e}")

    if loaded_count > 0:
        _room_name_cache = name_map
        _building_no_cache = bld_map
        _room_no_cache = rn_map
        _campus_cache = {"loaded": True}
        log(f"✅ 共加载 {len(name_map)} 个房间, {len(bld_map)} 个楼栋")
        return

    # 尝试加载缓存
    try:
        if os.path.exists(ROOMS_CACHE_FILE):
            with open(ROOMS_CACHE_FILE, 'r') as f:
                cache = json.load(f)
            name_map = cache.get("name_map", {})
            bld_map = {
                tuple(k.split("|")): tuple(v)
                for k, v in cache.get("bld_map", {}).items()
            }
            rn_map = {
                tuple(k.split("|")): tuple(v)
                for k, v in cache.get("rn_map", {}).items()
            }
            if rn_map:
                _room_name_cache = name_map
                _building_no_cache = bld_map
                _room_no_cache = rn_map
                _campus_cache = {"loaded": True}
                return
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        log(f"⚠️  房间缓存无效: {e}")

    # 最后尝试 API 拉取
    if not fetch_rooms_via_api():
        log("❌ 无法获取房间数据")
        _room_name_cache, _building_no_cache = {}, {}
        _room_no_cache = {}
        _campus_cache = {"loaded": True}
        sys.exit(1)


def resolve_room_config() -> None:
    """根据 BUILDING_NAME + ROOM_NAME 解析房间配置"""
    global ROOM_CONFIG
    load_rooms_data()

    if not BUILDING_NAME or not ROOM_NAME:
        if ROOM_CONFIG.get("BuildingNo") and ROOM_CONFIG.get("RoomNo"):
            load_rooms_data()
            return
        log("❌ 请配置 BUILDING_NAME + ROOM_NAME")
        sys.exit(1)

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


def get_room_display() -> str:
    """获取可读的房间名称"""
    room_no = ROOM_CONFIG.get("RoomNo", "")
    if room_no:
        load_rooms_data()
        readable = _room_name_cache.get(room_no, "")
        if readable:
            return readable
    if BUILDING_NAME and ROOM_NAME:
        return f"{BUILDING_NAME} {ROOM_NAME}"
    return room_no or "未知房间"


# ====================== SSO 登录 =======================

def sso_login() -> Optional[Dict[str, str]]:
    """
    SSO 登录获取 CTTICKET 和 etToken。
    支持自动二次验证（图片验证码 + 短信验证码）。

    返回 cookies 字典，失败返回 None。
    """
    global JWT_TOKEN

    if not SSO_USERNAME or not SSO_PASSWORD:
        log("⚠️  未配置 SSO 账号密码")
        return None

    log("🔐 SSO 登录中...")

    if USE_CURL_CFFI:
        session = cffi_requests.Session(impersonate="chrome120")
    else:
        session = requests.Session()
    session.verify = False

    try:
        # 1. 获取 RSA 公钥
        resp = session.post(
            f"{SSO_BASE}/ssoApi/getLoginBasicInfo",
            data={"md5": "1"}, timeout=15,
        )
        public_key = resp.json().get("data", {}).get("publicEn")
        if not public_key:
            log("❌ 获取公钥失败")
            return None

        # 2. RSA 加密账号密码
        enc_account = rsa_encrypt(SSO_USERNAME, public_key)
        enc_password = rsa_encrypt(SSO_PASSWORD, public_key)

        # 3. 固定设备 ID
        device = get_device_id()
        log(f"📱 设备 ID: {device[:16]}...")

        # 4. 构造登录请求
        fields = {
            "loginMode": "1",
            "account": enc_account,
            "password": enc_password,
            "clientType": "0",
            "code": "0x010040001",
            "locationurl": "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
            "device": device,
        }
        boundary = "----BoundaryPython"
        body, content_type = build_multipart(fields, boundary)

        resp = session.post(
            f"{SSO_BASE}/ssoApi/verifyWebUser",
            headers={"Content-Type": content_type},
            data=body.encode('utf-8'), timeout=15,
        )
        result = resp.json()
        resp_code = result.get("code", "")
        resp_msg = result.get("msg", "")
        log(f"📋 SSO 响应: code={resp_code}, msg={resp_msg}")

        # 5. 检测是否需要二次验证
        need_2fa = False
        secondary_code: Optional[str] = None
        phone: Optional[str] = None
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

        # --- 需要二次验证 ---
        if need_2fa:
            log("🔐 检测到需要二次验证")
            log(f"   二次验证 code: {secondary_code or '未获取到'}")
            log(f"   手机号: {phone or '未获取到'}")

            if not phone:
                log("❌ 未获取到手机号，无法发送短信")
                log(f"   完整返回: {json.dumps(result, ensure_ascii=False)}")
                return None

            # 获取图片验证码
            captcha_data, captcha_id = fetch_captcha_image(session)
            if not captcha_data or not captcha_id:
                log("❌ 无法获取图片验证码")
                return None

            # 启动 web 服务完成两步验证
            ok = run_2fa_web_server(
                session, phone, secondary_code or "", device, locationurl,
                captcha_data, captcha_id,
            )
            if not ok:
                log("❌ 二次验证未完成")
                return None

            # 二次验证成功 → 跟随重定向获取 CTTICKET
            log("🔄 二次验证完成，跟随重定向获取 CTTICKET...")
            redirect_url = (
                f"{SSO_BASE}/?action=secondary&code={secondary_code}"
                f"&before=0&type=0&zh={phone}"
                f"&locationurl={unquote(locationurl)}"
            )
            resp = session.get(redirect_url, timeout=15, allow_redirects=True)
            cookies = session.cookies.get_dict()
            log(f"🍪 重定向后 cookies: {list(cookies.keys())}")

            if any("CTTICKET" in k.upper() for k in cookies):
                save_auth_tokens(cookies, resp.text)
                return cookies

            # 重定向后没拿到 → 尝试访问 etong 首页
            resp = session.get(
                "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
                timeout=15,
            )
            cookies = session.cookies.get_dict()
            if any("CTTICKET" in k.upper() for k in cookies):
                save_auth_tokens(cookies, resp.text)
                return cookies

            log("❌ 未获取到 CTTICKET")
            log(f"   当前 cookies: {list(cookies.keys())}")
            return None

        # --- 不需要二次验证 ---
        if result.get("code") != "0x000000":
            log(f"❌ SSO 登录失败: {result.get('msg')}")
            return None

        log("✅ SSO 登录成功（无需二次验证）")

        # 获取 CTTICKET 和 etToken
        resp = session.get(
            "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
            timeout=15,
        )
        cookies = session.cookies.get_dict()
        return save_auth_tokens(cookies, resp.text)

    except Exception as e:
        log(f"❌ SSO 异常: {e}")
        import traceback
        traceback.print_exc()
        return None


# ====================== 电费查询 ======================

def query_balance(cookies_dict: Optional[Dict[str, str]] = None) -> Optional[float]:
    """查询当前电费余额（度），失败返回 None"""
    url = "https://etong.sdjzu.edu.cn/easytong_app/GetPayAccInfoNew"
    ts = time.strftime("%Y%m%d%H%M%S")

    sign = sign_request(
        "0", ROOM_CONFIG["AreaNo"], ROOM_CONFIG["BuildingNo"],
        ROOM_CONFIG["FloorNo"], ROOM_CONFIG["ItemNum"],
        ROOM_CONFIG["RoomNo"], ts,
    )
    post_data = {
        **ROOM_CONFIG, "Time": ts, "Sign": sign,
        "ContentType": "application/json",
    }
    headers = {
        "Authorization": JWT_TOKEN,
        "h5req": "Y",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://etong.sdjzu.edu.cn",
        "Referer": "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
    }
    cookies: Dict[str, str] = {"md5": "1", "etToken": JWT_TOKEN}
    if CTTICKET:
        cookies["CTTICKET"] = CTTICKET
        cookies["APPCTTICKET"] = CTTICKET
    if cookies_dict:
        cookies.update(cookies_dict)

    try:
        resp = http_post(url, data=post_data, headers=headers,
                         cookies=cookies, timeout=30, allow_redirects=False)
        if resp.status_code == 302:
            log("❌ CTTICKET 已过期")
            return None
        result = resp.json()
        if result.get("code") == 1:
            return float(result.get("balance", 0))
        log(f"❌ 查询失败: {result.get('msg')}")
        return None
    except Exception as e:
        # curl_cffi 超时时自动降级到普通 requests 重试一次
        if USE_CURL_CFFI and "timed out" in str(e):
            log("⚠️  curl_cffi 超时，切换普通 requests 重试...")
            import requests as _plain_req
            try:
                resp = _plain_req.post(url, data=post_data, headers=headers,
                                       cookies=cookies, timeout=30, allow_redirects=False)
                if resp.status_code == 302:
                    log("❌ CTTICKET 已过期")
                    return None
                result = resp.json()
                if result.get("code") == 1:
                    return float(result.get("balance", 0))
                log(f"❌ 查询失败: {result.get('msg')}")
                return None
            except Exception as e2:
                log(f"❌ 普通 requests 重试也失败: {e2}")
                return None
        log(f"❌ 请求异常: {e}")
        return None


def get_balance() -> Optional[float]:
    """
    获取电费余额，自动降级切换认证方式：
    1. 使用 CTTICKET 直接查询
    2. 使用缓存的 cookies
    3. SSO 重新登录
    """
    # 方式一：当前 CTTICKET
    if CTTICKET:
        balance = query_balance()
        if balance is not None:
            return balance
        log("⚠️  CTTICKET 可能已过期")

    # 方式二：缓存的 cookies
    cached = load_cookies()
    if cached:
        balance = query_balance(cached)
        if balance is not None:
            return balance

    # 方式三：SSO 重新登录
    log("🔄 尝试 SSO 重新登录...")
    try:
        new_cookies = sso_login()
        if new_cookies:
            return query_balance(new_cookies)
    except Exception as e:
        log(f"❌ SSO 登录失败: {e}")

    log("❌ 所有认证方式均失败")
    return None


# ====================== 推送通知 ======================

def send_notification(title: str, message: str) -> bool:
    """多渠道推送通知，至少成功一个渠道返回 True"""
    sent = False

    # 企业微信
    if WECOM_WEBHOOK:
        # 企业微信 API 与本机 OpenSSL 不兼容，改用系统 curl 命令（3 次重试）
        import subprocess as _sp
        import json as _json
        _payload = _json.dumps({"msgtype": "text", "text": {"content": f"{title}\n{message}"}}, ensure_ascii=False)
        for _attempt in range(3):
            try:
                _r = _sp.run(
                    ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                     "-X", "POST", WECOM_WEBHOOK,
                     "-H", "Content-Type: application/json",
                     "-d", _payload, "--connect-timeout", "10", "--max-time", "15"],
                    capture_output=True, text=True, timeout=20,
                )
                if _r.returncode == 0:
                    log(f"📱 企业微信推送成功 (HTTP {_r.stdout})")
                    sent = True
                    break
                else:
                    log(f"⚠️  企业微信推送 curl 失败 (尝试 {_attempt+1}/3): {_r.stderr.strip()}")
            except Exception as _e:
                log(f"⚠️  企业微信推送异常 (尝试 {_attempt+1}/3): {_e}")
                if _attempt < 2:
                    time.sleep(2)

    # Bark (iOS)
    if BARK_KEY:
        try:
            http_get(f"{BARK_KEY}/{title}/{message}", timeout=10)
            log("📱 Bark 推送成功")
            sent = True
        except Exception as e:
            log(f"⚠️  Bark 推送失败: {e}")

    # PushPlus
    if PUSHPLUS_TOKEN:
        try:
            http_post(
                "http://www.pushplus.plus/send",
                json={"token": PUSHPLUS_TOKEN, "title": title, "content": message},
                timeout=10,
            )
            log("📱 PushPlus 推送成功")
            sent = True
        except Exception as e:
            log(f"⚠️  PushPlus 推送失败: {e}")

    if not sent:
        log("⚠️  未配置推送渠道，或所有推送均失败")
    return sent


# ====================== 心跳保活 ======================

_heartbeat_stop = threading.Event()


def heartbeat_loop() -> None:
    """
    心跳保活线程：每 600 秒访问 etong 首页刷新 session。
    使用模块级的 http_get，确保 TLS 指纹一致。
    """
    log("💓 心跳保活线程已启动 (每 600 秒)")
    while not _heartbeat_stop.is_set():
        if _heartbeat_stop.wait(timeout=600):
            break
        try:
            cookies = load_cookies()
            if not cookies:
                log("💓 心跳保活: 无 cookies，跳过")
                continue

            # 使用 cookies 访问 etong 首页刷新 session
            resp = http_get(
                "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
                cookies=cookies, timeout=10,
            )
            if resp.status_code == 200:
                save_cookies(cookies)  # 刷新时间戳
                log("💓 心跳保活: session 已刷新")
            else:
                log(f"💓 心跳保活: HTTP {resp.status_code}")
        except Exception as e:
            log(f"💓 心跳保活异常: {e}")

    log("💓 心跳保活线程已停止")


# ====================== 主循环逻辑 ======================

_running = threading.Event()
_running.set()


def signal_handler(sig, frame) -> None:
    """收到终止信号时优雅退出"""
    log("收到停止信号，正在退出...")
    _running.clear()


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def check_and_notify() -> None:
    """执行一次电费查询，根据结果决定是否推送通知"""
    now = datetime.now()
    state = load_state()
    log("⚡ 查询电量...")
    balance = get_balance()

    if balance is None:
        log("❌ 本次查询失败")
        last_fail = state.get("last_fail_alert", 0)
        if time.time() - last_fail > 21600:
            send_notification(
                "⚡ 电费查询失败",
                f"⏰ {now.strftime('%m-%d %H:%M')}\n请检查网络或 SSO 账号",
            )
            state["last_fail_alert"] = time.time()
            save_state(state)
        return

    log(f"⚡ 当前电量: {balance} 度")
    state["last_balance"] = balance
    state["last_query_time"] = now.strftime('%Y-%m-%d %H:%M:%S')

    # --- 低电量告警（有冷却时间）---
    if balance <= LOW_BALANCE_THRESHOLD:
        last_alert = state.get("last_alert_time", 0)
        if time.time() - last_alert > ALERT_COOLDOWN:
            log(f"🚨 电量不足 {LOW_BALANCE_THRESHOLD} 度，发送告警！")
            room_display = get_room_display()
            send_notification(
                "🚨 电费余额严重不足！",
                f"━━━━━━━━━━━━━━\n🏠 房间: {room_display}\n"
                f"🔋 剩余: {balance} 度\n"
                f"⏰ 时间: {now.strftime('%m-%d %H:%M')}\n"
                f"━━━━━━━━━━━━━━\n⚠️ 请立即充值！",
            )
            state["last_alert_time"] = time.time()

    # --- 每日电量报告 ---
    today_str = now.strftime('%Y-%m-%d')
    is_report_time = (
        now.hour == DAILY_REPORT_HOUR
        and now.minute >= DAILY_REPORT_MINUTE
    )
    if is_report_time and state.get("last_daily_report") != today_str:
        log("📊 发送每日电量报告")
        status = (
            "🟢 充足" if balance > 50 else
            "🟡 正常" if balance > 20 else
            "🟠 偏低" if balance > 10 else
            "🔴 不足"
        )
        room_display = get_room_display()
        send_notification(
            "📊 每日电量报告",
            f"━━━━━━━━━━━━━━\n🏠 房间: {room_display}\n"
            f"🔋 剩余: {balance} 度\n📶 状态: {status}\n"
            f"📅 日期: {today_str}\n━━━━━━━━━━━━━━",
        )
        state["last_daily_report"] = today_str

    save_state(state)


def daemon_mode() -> None:
    """后台守护模式：循环查询 + 心跳保活"""
    resolve_room_config()
    log("=" * 50)
    log("⚡ 电费监控服务启动 (v9.4)")
    log(f"📋 房间: {get_room_display()}")
    log(f"⏰ 检查间隔: {CHECK_INTERVAL}秒")
    log(f"📊 日报时间: 每天 {DAILY_REPORT_HOUR}:{DAILY_REPORT_MINUTE:02d}")
    log(f"🚨 告警阈值: {LOW_BALANCE_THRESHOLD} 度")
    log(f"🛡️ 指纹模式: {'curl_cffi (Chrome)' if USE_CURL_CFFI else 'requests (普通)'}")
    log(f"📱 验证码端口: {SMS_SERVER_PORT}")
    log("💓 心跳间隔: 600秒")
    log("=" * 50)

    # 启动心跳保活线程
    _heartbeat_stop.clear()
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()

    # 首次执行
    check_and_notify()

    # 主循环
    while _running.is_set():
        now = datetime.now()
        next_check_ts = now.timestamp() + CHECK_INTERVAL

        # 计算下一次日报推送时间
        today_report = now.replace(
            hour=DAILY_REPORT_HOUR,
            minute=DAILY_REPORT_MINUTE,
            second=0, microsecond=0,
        )
        # 如果今天的日报时间还没到，且距日报时间小于检查间隔，则优先在日报时间检查
        if today_report.timestamp() > now.timestamp():
            secs_to_report = today_report.timestamp() - now.timestamp()
            if secs_to_report < CHECK_INTERVAL:
                next_check_ts = today_report.timestamp()

        wait_secs = max(next_check_ts - time.time(), 60)
        log(f"💤 下次检查: {datetime.fromtimestamp(next_check_ts).strftime('%H:%M:%S')} ({wait_secs/60:.0f}分钟后)")

        wait_until = time.time() + wait_secs
        while _running.is_set() and time.time() < wait_until:
            _running.wait(timeout=10)

        if _running.is_set():
            check_and_notify()

    _heartbeat_stop.set()
    time.sleep(1)
    log("⚡ 电费监控服务已停止")


def once_mode() -> None:
    """单次查询模式（带推送）"""
    resolve_room_config()
    log("=" * 50)
    log("⚡ 单次查询模式（强制推送）")
    balance = get_balance()
    now_str = datetime.now().strftime('%m-%d %H:%M')

    if balance is not None:
        log(f"⚡ 当前电量: {balance} 度")
        status = (
            "🟢 充足" if balance > 50 else
            "🟡 正常" if balance > 20 else
            "🟠 偏低" if balance > 10 else
            "🔴 不足"
        )
        room_display = get_room_display()
        send_notification(
            "⚡ 电费查询结果",
            f"━━━━━━━━━━━━━━\n🏠 房间: {room_display}\n"
            f"🔋 剩余: {balance} 度\n📶 状态: {status}\n"
            f"⏰ 时间: {now_str}\n━━━━━━━━━━━━━━",
        )
    else:
        send_notification("⚡ 电费查询失败", f"⏰ {now_str}\n请检查配置")
    log("=" * 50)


# ====================== 入口 ======================

if __name__ == "__main__":
    # 从 config.json 加载持久化的 Token（环境变量未提供时）
    load_tokens_from_config()

    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        once_mode()
    else:
        daemon_mode()
