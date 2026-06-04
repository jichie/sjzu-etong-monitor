#!/usr/bin/env python3
"""
山东建筑大学 电费监控服务 v7.3
- 每小时检查电量，低于阈值立即告警
- 每天 19:10 推送当日电量日报
- 支持 systemd 开机自启
- 支持楼栋名+房间名配置，自动查询 rooms.json
- 支持济南校区 + 烟台校区，自动识别

GitHub: https://github.com/jichie/sjzu-etong-monitor
"""

import hashlib
import json
import sys
import time
import os
import signal
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import requests
except ImportError:
    print("pip3 install requests"); sys.exit(1)

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
# SSO 统一认证账号（学号）和密码
# 首次使用需要填写，脚本会自动登录获取 token
SSO_USERNAME = ""              # 你的学号，如 "202207101230"
SSO_PASSWORD = ""              # SSO 密码

# --- 房间配置 ---
# 只需要填写楼栋名称和房间名称，程序会自动从 rooms.json 查找对应编号
# 例如：BUILDING_NAME = "梅二-照明"  ROOM_NAME = "413"
BUILDING_NAME = ""             # 楼栋名称（与 rooms.json 中 building_name 一致）
ROOM_NAME = ""                 # 房间名称（与 rooms.json 中 name 一致）

# 以下参数一般不需要修改，BuildingNo 和 RoomNo 由程序自动填充
ROOM_CONFIG = {
    "AccNum": "0",             # 账户类型（"0" = 济南校区，"1" = 烟台校区）
    "AreaNo": "1",             # 校区编号（"1" = 济南校区）
    "BuildingNo": "",          # 楼栋编号（自动从 rooms.json 查找）
    "FloorNo": "0",            # 楼层编号（一般为 "0"）
    "ItemNum": "2",            # 缴费项目（"2" = 济南校区电控缴费）
    "RoomNo": "",              # 房间号（自动从 rooms.json 查找）
}

# --- 签名参数（必须抓包获取！）---
# Time 和 Sign 与房间绑定，更换房间需重新抓包
# 抓包方法：浏览器 F12 → Network → 搜索 GetPayAccInfoNew → Payload → 复制 Time 和 Sign
# 济南校区（填写你自己房间的抓包值）
JINAN_FIXED_TIME = "20260326085915"
JINAN_FIXED_SIGN = "9466192480bb36aee07b22ee0bff8398"
# 烟台校区（填写你自己房间的抓包值）
YANTAI_FIXED_TIME = "20260604201012"
YANTAI_FIXED_SIGN = "48602f9988d01f8616153f979b9b9e45"

# 运行时签名（程序自动选择，无需修改）
FIXED_TIME = JINAN_FIXED_TIME
FIXED_SIGN = JINAN_FIXED_SIGN

# --- 认证 Token ---
# JWT Token，首次登录后脚本会自动获取，一般留空即可
JWT_TOKEN = ""

# --- 监控设置 ---
LOW_BALANCE_THRESHOLD = 10.0   # 低电量告警阈值（度）
CHECK_INTERVAL = 3600          # 检查间隔（秒），默认 1 小时
DAILY_REPORT_HOUR = 19         # 日报发送时间（小时）
DAILY_REPORT_MINUTE = 10       # 日报发送时间（分钟）
ALERT_COOLDOWN = 21600         # 低电量告警冷却时间（秒），6 小时内不重复告警

# --- 房间名称映射 ---
# rooms.json 文件路径，用于将房间号映射为可读名称
# 济南校区：使用项目自带的 rooms.json
# 烟台校区：将烟台校区房间号.json 重命名为 rooms.json 放在脚本同目录
ROOMS_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rooms.json")

# --- 推送配置 ---
# 至少配置一个推送渠道，否则告警无法发送
# 企业微信机器人 Webhook
WECOM_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=52ce281a-3dea-4927-885a-2c8747a548b9"
# Bark 推送地址（iOS）
BARK_KEY = ""
# PushPlus Token
PUSHPLUS_TOKEN = ""

# ====================== 配置结束 ======================

COOKIE_FILE = "/tmp/etong_cookies.json"
STATE_FILE = "/tmp/etong_state.json"
LOG_FILE = "/var/log/etong.log"

running = True


def signal_handler(sig, frame):
    global running
    log("收到停止信号，正在退出...")
    running = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except:
        pass


def load_state():
    """加载运行状态"""
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"last_alert_time": 0, "last_daily_report": "", "last_balance": None}


def save_state(state):
    """保存运行状态"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except:
        pass


# --- 房间名称映射缓存 ---
_room_name_cache = None        # room_no → "梅二-照明 413"
_building_no_cache = None      # building_name → building_no ("梅二-照明" → "2")
_room_no_cache = None          # (building_name, room_name) → room_no
_campus_cache = None           # {"campus": "烟台校区", "area_no": "0"} 或 None=济南


def load_rooms_data():
    """从 rooms.json 加载并构建所有映射（带缓存，自动检测校区）"""
    global _room_name_cache, _building_no_cache, _room_no_cache, _campus_cache
    if _room_name_cache is not None:
        return

    if not os.path.exists(ROOMS_JSON_PATH):
        log(f"⚠️  rooms.json 未找到: {ROOMS_JSON_PATH}")
        _room_name_cache = {}
        _building_no_cache = {}
        _room_no_cache = {}
        _campus_cache = {}
        return

    try:
        with open(ROOMS_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 自动检测校区：area_no="0" 为烟台校区，否则为济南校区
        area_no = data.get("area_no", "1")
        campus = data.get("campus", "")
        if area_no == "0" or "烟台" in campus:
            _campus_cache = {"campus": "烟台校区", "area_no": "0"}
        else:
            _campus_cache = {"campus": "济南校区", "area_no": "1"}

        name_map = {}
        bld_map = {}
        rn_map = {}

        for building in data.get("buildings", []):
            bld_name = building.get("building_name", "")
            bld_no = building.get("building_no", "")
            if bld_name and bld_no:
                bld_map[bld_name] = bld_no

            for room in building.get("rooms", []):
                room_no = room.get("no", "")
                room_name = room.get("name", "")
                if room_no:
                    name_map[room_no] = f"{bld_name} {room_name}"
                if bld_name and room_name and room_no:
                    rn_map[(bld_name, room_name)] = room_no

        _room_name_cache = name_map
        _building_no_cache = bld_map
        _room_no_cache = rn_map
        log(f"✅ 已加载 {len(name_map)} 个房间, {len(bld_map)} 个楼栋 ({_campus_cache['campus']})")
    except Exception as e:
        log(f"⚠️  加载 rooms.json 失败: {e}")
        _room_name_cache = {}
        _building_no_cache = {}
        _room_no_cache = {}
        _campus_cache = {}


def resolve_room_config():
    """根据 BUILDING_NAME + ROOM_NAME 自动查找 BuildingNo 和 RoomNo，并自动设置校区参数"""
    global FIXED_TIME, FIXED_SIGN
    load_rooms_data()

    # 自动设置校区参数
    if _campus_cache and _campus_cache.get("area_no") == "0":
        # 烟台校区
        ROOM_CONFIG["AccNum"] = "1"
        ROOM_CONFIG["AreaNo"] = "0"
        ROOM_CONFIG["ItemNum"] = "6"
        FIXED_TIME = YANTAI_FIXED_TIME
        FIXED_SIGN = YANTAI_FIXED_SIGN
    else:
        # 济南校区（默认）
        ROOM_CONFIG["AccNum"] = "0"
        ROOM_CONFIG["AreaNo"] = "1"
        ROOM_CONFIG["ItemNum"] = "2"
        FIXED_TIME = JINAN_FIXED_TIME
        FIXED_SIGN = JINAN_FIXED_SIGN

    if BUILDING_NAME and ROOM_NAME:
        # 查找楼栋编号
        bld_no = _building_no_cache.get(BUILDING_NAME, "")
        if not bld_no:
            log(f"❌ 在 rooms.json 中未找到楼栋: {BUILDING_NAME}")
            log("   请检查 BUILDING_NAME 是否与 rooms.json 中完全一致")
            sys.exit(1)

        # 查找房间编号
        room_no = _room_no_cache.get((BUILDING_NAME, ROOM_NAME), "")
        if not room_no:
            log(f"❌ 在 rooms.json 中未找到房间: {BUILDING_NAME} {ROOM_NAME}")
            log("   请检查 ROOM_NAME 是否与 rooms.json 中完全一致")
            sys.exit(1)

        ROOM_CONFIG["BuildingNo"] = bld_no
        ROOM_CONFIG["RoomNo"] = room_no
        log(f"🏠 房间: {BUILDING_NAME} {ROOM_NAME} (building_no={bld_no}, room_no={room_no})")
    elif ROOM_CONFIG.get("BuildingNo") and ROOM_CONFIG.get("RoomNo"):
        # 兼容旧方式：手动填写了 BuildingNo 和 RoomNo
        load_rooms_data()
        log(f"🏠 房间: {get_room_display()}")
    else:
        log("❌ 请配置 BUILDING_NAME + ROOM_NAME，或手动填写 ROOM_CONFIG 中的 BuildingNo 和 RoomNo")
        sys.exit(1)


def get_room_display():
    """获取当前配置房间的可读名称，如 '梅二-照明 413'"""
    room_no = ROOM_CONFIG.get("RoomNo", "")
    if room_no:
        load_rooms_data()
        readable = _room_name_cache.get(room_no, "")
        if readable:
            return readable
    # 回退：显示 BUILDING_NAME + ROOM_NAME
    if BUILDING_NAME and ROOM_NAME:
        return f"{BUILDING_NAME} {ROOM_NAME}"
    return room_no or "未知房间"


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
    """SSO 登录获取 CTTICKET 和 etToken"""
    global JWT_TOKEN
    if not SSO_USERNAME or not SSO_PASSWORD:
        log("⚠️  未配置 SSO 账号密码")
        return None

    log("🔐 SSO 登录中...")
    session = requests.Session()
    session.verify = False

    try:
        resp = session.post(
            "https://sso.sdjzu.edu.cn/ssoApi/getLoginBasicInfo",
            data={"md5": "1"}, timeout=15
        )
        public_key = resp.json().get("data", {}).get("publicEn")
        if not public_key:
            log("❌ 获取公钥失败")
            return None

        enc_account = rsa_encrypt(SSO_USERNAME, public_key)
        enc_password = rsa_encrypt(SSO_PASSWORD, public_key)

        import random, string
        device = hashlib.md5(
            f"Script_{int(time.time())}_{''.join(random.choices(string.ascii_letters, k=30))}".encode()
        ).hexdigest()

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
            "https://sso.sdjzu.edu.cn/ssoApi/verifyWebUser",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            data=body.encode('utf-8'), timeout=15
        )
        result = resp.json()
        if result.get("code") != "0x000000":
            log(f"❌ SSO 登录失败: {result.get('msg')}")
            return None
        log("✅ SSO 登录成功")

        # 访问 etong 首页，获取 CTTICKET 和 etToken
        resp = session.get("https://etong.sdjzu.edu.cn/easytong_webapp/index.html", timeout=15)
        cookies = session.cookies.get_dict()

        # 从页面中提取 etToken
        import re
        match = re.search(r"setCookie\('etToken',\s*'([^']+)'", resp.text)
        if match:
            JWT_TOKEN = match.group(1)
            log("✅ 已获取 etToken")
        else:
            log("⚠️  未能从页面提取 etToken")

        if any("CTTICKET" in k.upper() for k in cookies):
            save_cookies(cookies)
            return cookies
        log("❌ 未获取到 CTTICKET")
        return None
    except Exception as e:
        log(f"❌ SSO 异常: {e}")
        return None


def query_balance(cookies_dict=None):
    """查询电费余额"""
    url = "https://etong.sdjzu.edu.cn/easytong_app/GetPayAccInfoNew"

    post_data = {
        **ROOM_CONFIG,
        "Time": FIXED_TIME,
        "Sign": FIXED_SIGN,
        "ContentType": "application/json",
    }

    headers = {
        "Authorization": JWT_TOKEN,
        "h5req": "Y",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://etong.sdjzu.edu.cn",
        "Referer": "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    cookies = {"md5": "1", "etToken": JWT_TOKEN}
    if cookies_dict:
        cookies.update(cookies_dict)

    try:
        resp = requests.post(
            url, data=post_data, headers=headers,
            cookies=cookies, timeout=30, verify=False,
            allow_redirects=False
        )
        if resp.status_code == 302:
            log("❌ CTTICKET 已过期")
            return None
        result = resp.json()
        if result.get("code") == 1:
            balance = float(result.get("balance", 0))
            return balance
        log(f"❌ 查询失败: {result.get('msg')}")
        return None
    except Exception as e:
        log(f"❌ 请求异常: {e}")
        return None


def get_balance():
    """完整的查询流程（含自动登录）"""
    # 先用缓存
    cached = load_cookies()
    if cached:
        balance = query_balance(cached)
        if balance is not None:
            return balance

    # 缓存失效，重新登录
    log("CTTICKET 失效，重新登录...")
    new_cookies = sso_login()
    if new_cookies:
        return query_balance(new_cookies)
    return None


def send_notification(title, message):
    """发送推送"""
    sent = False

    if WECOM_WEBHOOK:
        try:
            requests.post(WECOM_WEBHOOK, json={
                "msgtype": "text",
                "text": {"content": f"{title}\n{message}"}
            }, timeout=10)
            log("📱 企业微信推送成功")
            sent = True
        except:
            pass

    if BARK_KEY:
        try:
            requests.get(f"{BARK_KEY}/{title}/{message}", timeout=10, verify=False)
            log("📱 Bark 推送成功")
            sent = True
        except:
            pass

    if PUSHPLUS_TOKEN:
        try:
            requests.post("http://www.pushplus.plus/send", json={
                "token": PUSHPLUS_TOKEN, "title": title, "content": message
            }, timeout=10)
            log("📱 PushPlus 推送成功")
            sent = True
        except:
            pass

    if not sent:
        log("⚠️  未配置推送渠道")

    return sent


def check_and_notify():
    """检查电量并根据情况发送通知"""
    now = datetime.now()
    state = load_state()

    log(f"⚡ 查询电量...")
    balance = get_balance()

    if balance is None:
        log("❌ 本次查询失败")
        # 连续失败时每6小时提醒一次
        if time.time() - state.get("last_fail_alert", 0) > 21600:
            send_notification("⚡ 电费查询失败", f"⏰ {now.strftime('%m-%d %H:%M')}\n请检查网络或 SSO 账号")
            state["last_fail_alert"] = time.time()
            save_state(state)
        return

    log(f"⚡ 当前电量: {balance} 度")
    state["last_balance"] = balance
    state["last_query_time"] = now.strftime('%Y-%m-%d %H:%M:%S')

    # ---- 低电量告警 ----
    if balance <= LOW_BALANCE_THRESHOLD:
        last_alert = state.get("last_alert_time", 0)
        if time.time() - last_alert > ALERT_COOLDOWN:
            log(f"🚨 电量不足 {LOW_BALANCE_THRESHOLD} 度，发送告警！")
            room_display = get_room_display()
            send_notification(
                "🚨 电费余额严重不足！",
                f"━━━━━━━━━━━━━━\n"
                f"🏠 房间: {room_display}\n"
                f"🔋 剩余: {balance} 度\n"
                f"⏰ 时间: {now.strftime('%m-%d %H:%M')}\n"
                f"━━━━━━━━━━━━━━\n"
                f"⚠️ 请立即充值！"
            )
            state["last_alert_time"] = time.time()
        else:
            remaining = ALERT_COOLDOWN - (time.time() - last_alert)
            log(f"⚠️ 电量不足但在冷却期内（{remaining/60:.0f}分钟后可再次告警）")

    # ---- 每日19:10日报 ----
    today_str = now.strftime('%Y-%m-%d')
    is_report_time = (now.hour == DAILY_REPORT_HOUR and
                      DAILY_REPORT_MINUTE <= now.minute < DAILY_REPORT_MINUTE + 59)

    if is_report_time and state.get("last_daily_report") != today_str:
        log("📊 发送每日电量报告")

        # 判断电量状态
        if balance > 50:
            status = "🟢 充足"
        elif balance > 20:
            status = "🟡 正常"
        elif balance > 10:
            status = "🟠 偏低"
        else:
            status = "🔴 不足"

        room_display = get_room_display()
        send_notification(
            "📊 每日电量报告",
            f"━━━━━━━━━━━━━━\n"
            f"🏠 房间: {room_display}\n"
            f"🔋 剩余: {balance} 度\n"
            f"📶 状态: {status}\n"
            f"📅 日期: {today_str}\n"
            f"━━━━━━━━━━━━━━"
        )
        state["last_daily_report"] = today_str

    save_state(state)


def daemon_mode():
    """守护进程模式：持续监控"""
    resolve_room_config()
    log("=" * 50)
    log("⚡ 电费监控服务启动")
    log(f"📋 房间: {get_room_display()}")
    log(f"⏰ 检查间隔: {CHECK_INTERVAL}秒")
    log(f"📊 日报时间: 每天 {DAILY_REPORT_HOUR}:{DAILY_REPORT_MINUTE:02d}")
    log(f"🚨 告警阈值: {LOW_BALANCE_THRESHOLD} 度")
    log("=" * 50)

    # 启动时立即查一次
    check_and_notify()

    while running:
        # 计算下次检查时间
        now = datetime.now()
        next_check = now.timestamp() + CHECK_INTERVAL

        # 如果距离日报时间不到 CHECK_INTERVAL，调整等待时间
        today_report = now.replace(
            hour=DAILY_REPORT_HOUR,
            minute=DAILY_REPORT_MINUTE,
            second=0, microsecond=0
        )
        if today_report.timestamp() > now.timestamp():
            secs_to_report = today_report.timestamp() - now.timestamp()
            if secs_to_report < CHECK_INTERVAL:
                next_check = today_report.timestamp()

        wait_secs = max(next_check - time.time(), 60)
        log(f"💤 下次检查: {datetime.fromtimestamp(next_check).strftime('%H:%M:%S')} ({wait_secs/60:.0f}分钟后)")

        # 等待，但每10秒检查一次是否需要退出
        wait_until = time.time() + wait_secs
        while running and time.time() < wait_until:
            time.sleep(10)

        if running:
            check_and_notify()

    log("⚡ 电费监控服务已停止")


def once_mode():
    """单次查询模式 - 始终推送结果"""
    resolve_room_config()
    log("=" * 50)
    log("⚡ 单次查询模式（强制推送）")

    balance = get_balance()
    now = datetime.now().strftime('%m-%d %H:%M')

    if balance is not None:
        log(f"⚡ 当前电量: {balance} 度")

        if balance > 50:
            status = "🟢 充足"
        elif balance > 20:
            status = "🟡 正常"
        elif balance > 10:
            status = "🟠 偏低"
        else:
            status = "🔴 不足"

        room_display = get_room_display()
        send_notification(
            "⚡ 电费查询结果",
            f"━━━━━━━━━━━━━━\n"
            f"🏠 房间: {room_display}\n"
            f"🔋 剩余: {balance} 度\n"
            f"📶 状态: {status}\n"
            f"⏰ 时间: {now}\n"
            f"━━━━━━━━━━━━━━"
        )
    else:
        send_notification("⚡ 电费查询失败", f"⏰ {now}\n请检查配置")

    log("=" * 50)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        once_mode()
    else:
        daemon_mode()
