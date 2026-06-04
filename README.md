# 山东建筑大学电费监控服务

> 自动监控宿舍电费余额，低电量告警，每日推送电量报告

## ✨ 功能

- ⚡ **自动查询** - 定时查询电费余额
- 🚨 **低电量告警** - 余额低于阈值立即推送通知
- 📊 **每日报告** - 每天 19:10 推送当日电量日报
- 🔄 **自动登录** - SSO 认证过期后自动重新登录
- 🛡️ **告警冷却** - 避免重复告警打扰
- 📱 **多渠道推送** - 支持企业微信、Bark、PushPlus

## 📦 安装方法

### 1. 安装依赖

```bash
pip3 install requests pycryptodome
```

### 2. 下载脚本

```bash
mkdir -p /opt/etong

# 下载脚本和两个校区的房间数据（三个文件缺一不可）
wget -O /opt/etong/etong_monitor.py https://raw.githubusercontent.com/jichie/sjzu-etong-monitor/main/etong_monitor.py
wget -O /opt/etong/rooms.json https://raw.githubusercontent.com/jichie/sjzu-etong-monitor/main/rooms.json
wget -O /opt/etong/烟台校区_rooms.json https://raw.githubusercontent.com/jichie/sjzu-etong-monitor/main/烟台校区_rooms.json
```

### 3. 配置

编辑脚本配置区域：

```bash
vi /opt/etong/etong_monitor.py
```

```python
# --- 登录账号 ---
SSO_USERNAME = "你的学号"
SSO_PASSWORD = "你的密码"

# --- 房间配置 ---
BUILDING_NAME = "梅二-照明"               # 楼栋名称，如 "梅二-照明" 或 "1号楼"
ROOM_NAME = "413"                        # 房间名称，如 "413" 或 "101"

# --- 推送配置 ---
WECOM_WEBHOOK = "你的企业微信 Webhook"   # 至少配置一个
```


### 4. 测试运行

```bash
# 单次查询（测试配置是否正确）
python3 /opt/etong/etong_monitor.py --once
```

### 5. 设置开机自启（可选）

```bash
# 复制服务文件
cp etong-monitor.service /etc/systemd/system/

# 重载配置
systemctl daemon-reload

# 启动服务
systemctl start etong-monitor

# 设置开机自启
systemctl enable etong-monitor
```

## 🚀 使用方法

### 单次查询

```bash
python3 /opt/etong/etong_monitor.py --once
```

### 后台运行

```bash
# 直接运行
python3 /opt/etong/etong_monitor.py

# 或使用 systemd
systemctl start etong-monitor
```

### 管理命令

```bash
# 查看实时日志
journalctl -u etong-monitor -f

# 查看服务状态
systemctl status etong-monitor

# 重启服务（修改配置后）
systemctl restart etong-monitor

# 停止服务
systemctl stop etong-monitor

# 禁用开机自启
systemctl disable etong-monitor
```

## ⚙️ 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `SSO_USERNAME` | SSO 学号 | 必填 |
| `SSO_PASSWORD` | SSO 密码 | 必填 |
| `BUILDING_NAME` | 楼栋名称（如"梅二-照明" 或 "1号楼"） | 必填 |
| `ROOM_NAME` | 房间名称（如"413" 或 "101"） | 必填 |
| `WECOM_WEBHOOK` | 企业微信 Webhook | 选填 |
| `BARK_KEY` | Bark 推送地址 | 选填 |
| `PUSHPLUS_TOKEN` | PushPlus Token | 选填 |
| `LOW_BALANCE_THRESHOLD` | 低电量告警阈值（度） | `10.0` |
| `CHECK_INTERVAL` | 检查间隔（秒） | `3600` |
| `DAILY_REPORT_HOUR` | 日报推送时间（时） | `19` |
| `DAILY_REPORT_MINUTE` | 日报推送时间（分） | `10` |
| `MD5_KEY` | 签名密钥 | 无需修改 |
| `JWT_TOKEN` | 认证 Token | 自动获取 |

## 📱 推送渠道配置

### 企业微信机器人

1. 在企业微信群聊中添加机器人
2. 复制 Webhook 地址
3. 填入 `WECOM_WEBHOOK`

### Bark（iOS）

1. 在 App Store 下载 Bark
2. 打开 App 获取推送地址
3. 填入 `BARK_KEY`

### PushPlus

1. 访问 [pushplus.plus](https://www.pushplus.plus)
2. 微信登录获取 Token
3. 填入 `PUSHPLUS_TOKEN`

## 🔧 如何配置房间

只需填写楼栋名称和房间名称，程序自动在两个校区的数据中搜索。

```python
BUILDING_NAME = "梅二-照明"     # 济南校区示例
ROOM_NAME = "413"

# 或烟台校区：
# BUILDING_NAME = "1号楼"
# ROOM_NAME = "101"
```

程序会自动识别校区并设置正确的查询参数，无需手动区分。

> 如果之前使用手动填写编号的方式，仍然兼容。只需留空 `BUILDING_NAME` 和 `ROOM_NAME`，在 `ROOM_CONFIG` 中填写编号即可。



## 📝 运行示例

```
[2026-03-26 18:30:01] ⚡ 查询电量...
[2026-03-26 18:30:02] ⚡ 当前电量: 80.5 度
[2026-03-26 18:30:02] 💤 下次检查: 19:10:00 (40分钟后)
[2026-03-26 19:10:01] ⚡ 查询电量...
[2026-03-26 19:10:02] ⚡ 当前电量: 80.5 度
[2026-03-26 19:10:02] 📊 发送每日电量报告
[2026-03-26 19:10:02] 📱 企业微信推送成功
```

## 🐛 常见问题

**Q: 查询失败怎么办？**
A: 检查 SSO 账号密码是否正确，网络是否正常。

**Q: 如何修改房间？**
A: 修改脚本中的 `BUILDING_NAME` 和 `ROOM_NAME`，程序自动查找对应编号。

**Q: 推送收不到？**
A: 检查推送渠道配置是否正确，至少配置一个渠道。

**Q: CTTICKET 频繁过期？**
A: 正常现象，脚本会自动重新登录。

## 📝 更新日志

### v8.1

- 📂 **双文件自动识别**：同时加载 `rooms.json` + `烟台校区_rooms.json`，无需重命名
- 🏠 填楼栋名+房间名即可，自动在双校区数据中搜索匹配
- 📦 部署只需下载 3 个文件，无需手动区分校区

### v8.0

- 🔑 **动态签名**：逆向签名算法 `MD5(AccNum|AreaNo|BuildingNo|FloorNo|ItemNum|RoomNo|Time|MD5_KEY)`，无需抓包
- 🎉 配置从 6 项减少到 4 项：学号、密码、楼栋名、房间名
- 🗑️ 移除 `FIXED_TIME`/`FIXED_SIGN`
- 🐛 修复烟台校区 `AccNum` 固定为 `0`

### v7.3

- 🏫 烟台校区支持：自动识别校区，设置正确的查询参数
- 📦 新增 `烟台校区_rooms.json`

### v7.2

- 🏠 配置简化：填 `BUILDING_NAME` + `ROOM_NAME`，自动查 rooms.json
- 📱 推送显示可读名称（如"梅二-照明 413"）
- 🔑 自动获取 etToken：修复 SSO 登录后查询失败
- 🔄 向后兼容手动填写编号的旧方式

### v7.0

- 首个正式版本：SSO 登录 / 低电量告警 / 每日报告 / 多渠道推送 / systemd 自启

## 📜 许可证

MIT License
