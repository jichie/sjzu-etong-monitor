#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
山东建筑大学 easytong 系统 - 爬取全校房间号
通过 GetBuildingInfoByAreaNo + GetRoomInfo API 动态拉取

用法:
  python3 scrape_rooms.py           # 爬取济南+烟台全部校区
  python3 scrape_rooms.py 济南      # 只爬济南
  python3 scrape_rooms.py 烟台      # 只爬烟台

输出:
  rooms.json              # 济南校区房间数据
  烟台校区_rooms.json      # 烟台校区房间数据

依赖: pip3 install requests pycryptodome
"""

import hashlib, json, time, base64, random, string, sys, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import requests
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

# ============ 填写你的账号密码 ============
SSO_USERNAME = ""
SSO_PASSWORD = ""
# =========================================

MD5_KEY = "ok15we1@oid8x5afd@"

EASYTONG_APP = "https://etong.sdjzu.edu.cn/easytong_app"

# 校区配置: (名称, AreaNo, ItemNum, 输出文件名)
CAMPUSES = [
    ("济南", "1", "2", "rooms.json"),
    ("烟台", "0", "6", "烟台校区_rooms.json"),
]


def rsa_encrypt(text, pk_pem):
    pk_pem = pk_pem.strip()
    pk_pem = pk_pem.replace("-----BEGIN RSA Public Key-----", "-----BEGIN PUBLIC KEY-----")
    pk_pem = pk_pem.replace("-----END RSA Public Key-----", "-----END PUBLIC KEY-----")
    key = RSA.import_key(pk_pem)
    cipher = PKCS1_v1_5.new(key)
    ml = key.size_in_bytes() - 11
    data = text.encode()
    enc = b""
    for i in range(0, len(data), ml):
        enc += cipher.encrypt(data[i : i + ml])
    return base64.b64encode(enc).decode()


def sso_login():
    """SSO 登录，获取 CTTICKET"""
    s = requests.Session()
    s.verify = False
    r = s.post("https://sso.sdjzu.edu.cn/ssoApi/getLoginBasicInfo", data={"md5": "1"}, timeout=15)
    pk = r.json()["data"]["publicEn"]
    eu = rsa_encrypt(SSO_USERNAME, pk)
    ep = rsa_encrypt(SSO_PASSWORD, pk)
    dev = hashlib.md5(
        f"Crawl_{int(time.time())}_{''.join(random.choices(string.ascii_letters, k=30))}".encode()
    ).hexdigest()
    b = "----BP"
    body = (
        f'--{b}\r\nContent-Disposition: form-data; name="loginMode"\r\n\r\n1\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="account"\r\n\r\n{eu}\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="password"\r\n\r\n{ep}\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="clientType"\r\n\r\n0\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="code"\r\n\r\n0x010040001\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="locationurl"\r\n\r\nhttps://etong.sdjzu.edu.cn/easytong_webapp/index.html\r\n'
        f'--{b}\r\nContent-Disposition: form-data; name="device"\r\n\r\n{dev}\r\n'
        f'--{b}--\r\n'
    )
    r = s.post(
        "https://sso.sdjzu.edu.cn/ssoApi/verifyWebUser",
        headers={"Content-Type": f"multipart/form-data; boundary={b}"},
        data=body.encode(),
        timeout=15,
    )
    if r.json().get("code") != "0x000000":
        raise Exception(f"登录失败: {r.json().get('msg')}")
    # 访问首页获取 CTTICKET 和 etToken
    resp = s.get("https://etong.sdjzu.edu.cn/easytong_webapp/index.html", timeout=15)
    cookies = s.cookies.get_dict()
    # 从页面提取 etToken
    import re
    match = re.search(r"setCookie\('etToken',\s*'([^']+)'", resp.text)
    if match:
        global JWT_TOKEN
        JWT_TOKEN = match.group(1)
    return cookies


def sign_building(area_no, item_num, ts):
    """GetBuildingInfoByAreaNo 签名: MD5(AreaNo|ItemNum|Time|Key)"""
    return hashlib.md5(f"{area_no}|{item_num}|{ts}|{MD5_KEY}".encode()).hexdigest()


def sign_room(area_no, building_no, item_num, ts):
    """GetRoomInfo 签名: MD5(AreaNo|BuildingNo|ItemNum|Time|Key)"""
    return hashlib.md5(f"{area_no}|{building_no}|{item_num}|{ts}|{MD5_KEY}".encode()).hexdigest()


def api_post(url, data, cookies):
    """发送 easytong API 请求"""
    h = {
        "Authorization": JWT_TOKEN,
        "h5req": "Y",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://etong.sdjzu.edu.cn",
        "Referer": "https://etong.sdjzu.edu.cn/easytong_webapp/index.html",
        "User-Agent": "Mozilla/5.0",
    }
    c = {"md5": "1", "etToken": JWT_TOKEN}
    c.update(cookies)
    resp = requests.post(url, headers=h, cookies=c, data=data, verify=False, timeout=30)
    return resp.json()


def get_buildings(area_no, item_num, cookies):
    """获取楼栋列表"""
    ts = time.strftime("%Y%m%d%H%M%S")
    sign = sign_building(area_no, item_num, ts)
    data = f"AreaNo={area_no}&ItemNum={item_num}&Time={ts}&Sign={sign}&ContentType=application%2Fjson"
    r = api_post(f"{EASYTONG_APP}/GetBuildingInfoByAreaNo", data, cookies)
    return r.get("dormList", [])


def get_rooms(area_no, building_no, item_num, cookies):
    """获取某楼栋的房间列表"""
    ts = time.strftime("%Y%m%d%H%M%S")
    sign = sign_room(area_no, building_no, item_num, ts)
    data = f"AreaNo={area_no}&BuildingNo={building_no}&ItemNum={item_num}&Time={ts}&Sign={sign}&ContentType=application%2Fjson"
    r = api_post(f"{EASYTONG_APP}/GetRoomInfo", data, cookies)
    if r.get("code") == 1:
        return r.get("dormList", [])
    return None


def crawl_campus(name, area_no, item_num, cookies):
    """爬取一个校区的全部房间"""
    print(f"\n{'=' * 50}")
    print(f"🏫 {name}校区 (AreaNo={area_no}, ItemNum={item_num})")
    print(f"{'=' * 50}")

    # 1. 获取楼栋列表
    print("获取楼栋列表...")
    buildings = get_buildings(area_no, item_num, cookies)
    if not buildings:
        print("  未获取到楼栋数据")
        return None

    print(f"  找到 {len(buildings)} 个楼栋")
    for b in buildings:
        print(f"    {b['name']} (no={b['no']})")

    # 2. 遍历爬取每个楼栋的房间
    all_data = {
        "campus": f"{name}校区",
        "area_no": area_no,
        "item_num": item_num,
        "scrape_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_buildings": len(buildings),
        "buildings": [],
    }
    total_rooms = 0

    for i, bld in enumerate(buildings, 1):
        bno = bld["no"]
        bname = bld["name"]
        print(f"  [{i}/{len(buildings)}] {bname}...", end=" ", flush=True)

        rooms = get_rooms(area_no, bno, item_num, cookies)
        if rooms is None:
            print("✗ 失败")
            continue

        print(f"✓ {len(rooms)}个房间")
        total_rooms += len(rooms)
        all_data["buildings"].append(
            {
                "building_no": bno,
                "building_name": bname,
                "room_count": len(rooms),
                "rooms": rooms,
            }
        )
        time.sleep(0.3)

    all_data["total_rooms"] = total_rooms
    return all_data


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "全部"

    print("🔐 SSO登录...")
    try:
        cookies = sso_login()
        print("  ✅ 登录成功")
    except Exception as e:
        print(f"  ❌ {e}")
        return

    for name, area_no, item_num, filename in CAMPUSES:
        if target != "全部" and target != name:
            continue

        data = crawl_campus(name, area_no, item_num, cookies)
        if data:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"\n✅ 已保存: {filename} ({data['total_rooms']}个房间)")

    print("\n完成!")


if __name__ == "__main__":
    main()
