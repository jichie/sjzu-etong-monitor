# 山东建筑大学电费监控服务

> 自动监控宿舍电费余额，低电量告警，每日推送电量报告

## ✨ 功能

- ⚡ **自动查询** - 定时查询电费余额
- 🚨 **低电量告警** - 余额低于阈值立即推送通知
- 📊 **每日报告** - 每天 19:10 推送当日电量日报
- 🔑 **无需抓包** - 签名算法已逆向，自动计算 Sign
- 🏫 **双校区** - 自动识别济南/烟台校区
- 📦 **零配置启动** - 无本地数据时自动通过 API 拉取房间列表
- 📱 **多渠道推送** - 企业微信、Bark、PushPlus

> ⚠️ **2026年6月19日更新**：学校 SSO 系统升级，启用了二次验证（短信/扫码），脚本已无法通过账号密码自动登录。现在必须**手动抓取一次 CTTICKET**（有效期数月），后续可正常自动运行。

## 📦 快速开始

### 1. 安装依赖

```bash
pip3 install requests pycryptodome
```

### 2. 下载脚本

```bash
mkdir -p /opt/etong
wget -O /opt/etong/etong_monitor.py https://raw.githubusercontent.com/jichie/sjzu-etong-monitor/main/etong_monitor.py
```

### 3. 获取 CTTICKET（关键步骤！）

> 学校 SSO 系统现在需要二次验证，脚本无法自动登录。
> **需要从浏览器手动获取一次 CTTICKET，有效期数月。**

<details>
<summary><b>📖 点击展开详细图文教程</b></summary>

#### 第一步：打开电费查询页面

用 Chrome/Edge 打开以下链接：

```
https://etong.sdjzu.edu.cn/easytong_webapp/#/payIndex?itemNum=2&itemType=2
```

如果提示登录，输入 **学号** 和 **电费系统支付密码**（不是 SSO 密码）。

#### 第二步：查询一次电费

选择你的楼栋和房间，点击查询，确认能正常显示电量。

#### 第三步：抓到 CTTICKET

按 **F12** → 点击 **Network（网络）** 标签 → 在筛选框输入 `GetPayAccInfoNew`：

![Network](docs/network.jpg)

点击那条请求 → 找到 **Request Headers（请求头）** → 找到 `Cookie:` 字段 → 找到 `CTTICKET=...` 这串值，复制下来。

![Cookie](docs/cookie.jpg)

> 也可以用 Console 方式：点击 Console 标签，粘贴 `document.cookie.match(/CTTICKET=([^;]+)/)[1]` 回车，直接输出 CTTICKET。

CTTICKET 长这样：

```
web_96a5ac43425cd38e41117c3b5e6e4450d51b7601_webreq
```

</details>

### 4. 配置

```bash
vi /opt/etong/etong_monitor.py
```

修改以下内容：

```python
# --- 登录账号（SSO 备用，建议也填上）---
SSO_USERNAME = "STUDENT_ID_PLACEHOLDER"          # 你的学号
SSO_PASSWORD = "你的密码"

# --- 房间配置 ---
BUILDING_NAME = "梅二-照明"            # 楼栋名称
ROOM_NAME = "413"                      # 房间名称

# --- CTTICKET（从浏览器获取）---
CTTICKET = "web_96a5ac43425cd38e41117c3b5e6e4450d51b7601_webreq"

# --- 推送配置（至少配一个）---
WECOM_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的key"
```

### 5. 测试

```bash
python3 /opt/etong/etong_monitor.py --once
```

如果 CTTICKET 有效，输出应该是这样，**不需要 SSO 登录**：

```
📂 已加载 济南校区 房间数据
📂 已加载 烟台校区 房间数据
✅ 共加载 13033 个房间, 41 个楼栋
🏠 房间: 梅二-照明 413 (济南校区, room_no=10624)
⚡ 当前电量: 92.52 度
```

### 6. 后台运行（可选）

```bash
# 复制服务文件
cp etong-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl start etong-monitor
systemctl enable etong-monitor
```

## ⚙️ 完整配置说明

| 配置项 | 说明 | 必填 |
|--------|------|:----:|
| `SSO_USERNAME` | 学号 | 推荐 |
| `SSO_PASSWORD` | SSO 密码 | 推荐 |
| `BUILDING_NAME` | 楼栋名称，如 "梅二-照明" / "1号楼" | **是** |
| `ROOM_NAME` | 房间名称，如 "413" / "101" | **是** |
| `CTTICKET` | 从浏览器获取的认证 Cookie | **是** |
| `WECOM_WEBHOOK` | 企业微信机器人 Webhook | 选填 |
| `BARK_KEY` | Bark 推送地址 | 选填 |
| `PUSHPLUS_TOKEN` | PushPlus Token | 选填 |

> SSO 账号已不是必需（因为二次验证问题），但建议填上作为备用。
> 当 CTTICKET 过期时，脚本会尝试通过 SSO 重新登录，可能触发短信验证。

## 🔧 房间配置

程序自动在两个校区的数据中搜索房间，无需手动区分济南/烟台。

```python
BUILDING_NAME = "梅二-照明"     # 济南校区
ROOM_NAME = "413"

# 或烟台校区：
# BUILDING_NAME = "1号楼"
# ROOM_NAME = "101"
```

有本地 JSON 文件秒加载，没有则自动通过 API 拉取。

## 🔄 更新 CTTICKET

CTTICKET 通常有效 **2～6 个月**，过期后重复获取流程：

1. 用浏览器打开 etong 并登录
2. 按 F12 → Console
3. 执行 `document.cookie.match(/CTTICKET=([^;]+)/)[1]`
4. 复制输出值，替换脚本中的 `CTTICKET = "..."`

## 🐛 常见问题

**Q: 提示"签名不匹配"？**
A: 检查脚本中的 `MD5_KEY` 是否为 `ok15we1@oid8x5afd@`，学校可能更新了密钥。

**Q: 查不到电费，提示"用户不存在"？**
A: 账号没权限查该校区，济南学号查不了烟台电费。

**Q: CTTICKET 过期了，SSO 登录要短信验证？**
A: 重新从浏览器获取 CTTICKET 即可，不需要手机验证。

**Q: 如何修改房间？**
A: 修改 `BUILDING_NAME` 和 `ROOM_NAME`，程序自动查找对应编号。

**Q: 推送收不到？**
A: 检查推送渠道配置是否正确。企业微信机器人需要先在群聊中添加。

## 📝 更新日志

### v8.3

- 🔑 **CTTICKET 认证**：SSO 升级二次验证后，改为手动抓取 CTTICKET 方式
- 📖 更新 CTTICKET 获取教程（电费直连页 + Network 抓包）
- 🚫 移除 SSO 自动登录方案（2026-06-19 起失效）

### v8.2

- 📡 **无 JSON 也能跑**：调用 `GetBuildingInfoByAreaNo` + `GetRoomInfo` API 动态拉取
- ⚡ JSON 文件变为可选加速缓存

### v8.1

- 📂 **双文件自动识别**：同时加载 `rooms.json` + `烟台校区_rooms.json`

### v8.0

- 🔑 **动态签名**：逆向签名算法，无需抓包 Time/Sign
- 🎉 配置从 6 项减到 4 项

### v7.0

- 首个正式版本

## 🛠️ 维护工具

仓库中的 `scrape_rooms.py` 用于重新爬取全校房间号：

```bash
pip3 install requests pycryptodome
python3 scrape_rooms.py          # 爬取济南+烟台
python3 scrape_rooms.py 济南     # 只爬济南
python3 scrape_rooms.py 烟台     # 只爬烟台
```

## 📜 许可证

MIT License
