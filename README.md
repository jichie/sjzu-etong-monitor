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
# 创建目录
mkdir -p /opt/etong

# 下载脚本
wget -O /opt/etong/etong_monitor.py https://raw.githubusercontent.com/jichie/sjzu-etong-monitor/main/etong_monitor.py
```

### 3. 配置

编辑脚本配置区域：

```bash
vi /opt/etong/etong_monitor.py
```

修改以下内容：

```python
# --- 登录账号 ---
SSO_USERNAME = "你的学号"                # SSO 统一认证学号
SSO_PASSWORD = "你的密码"                # SSO 密码

# --- 房间配置 ---
BUILDING_NAME = "梅二-照明"               # 楼栋名称（与 rooms.json 中一致）
ROOM_NAME = "413"                        # 房间名称（与 rooms.json 中一致）

# --- 推送配置 ---
WECOM_WEBHOOK = "你的企业微信 Webhook"   # 至少配置一个推送渠道
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
| `BUILDING_NAME` | 楼栋名称（如"梅二-照明"） | 必填 |
| `ROOM_NAME` | 房间名称（如"413"） | 必填 |
| `LOW_BALANCE_THRESHOLD` | 低电量阈值（度） | `10.0` |
| `CHECK_INTERVAL` | 检查间隔（秒） | `3600` |
| `DAILY_REPORT_HOUR` | 日报时间（小时） | `19` |
| `DAILY_REPORT_MINUTE` | 日报时间（分钟） | `10` |
| `ALERT_COOLDOWN` | 告警冷却时间（秒） | `21600` |
| `WECOM_WEBHOOK` | 企业微信 Webhook | 可选 |
| `BARK_KEY` | Bark 推送地址 | 可选 |
| `PUSHPLUS_TOKEN` | PushPlus Token | 可选 |

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

只需填写楼栋名称和房间名称，程序会自动从 `rooms.json` 查找对应编号，并自动识别校区。

### 济南校区

示例（梅二 413 宿舍）：

```python
BUILDING_NAME = "梅二-照明"     # 楼栋名称（与 rooms.json 中 building_name 一致）
ROOM_NAME = "413"              # 房间名称（与 rooms.json 中 name 一致）
```

### 烟台校区

1. 将仓库中的 `烟台校区_rooms.json` 重命名为 `rooms.json`（替换原文件）
2. 填写楼栋名和房间名：

```python
BUILDING_NAME = "1号楼"        # 楼栋名称（与 rooms.json 中 building_name 一致）
ROOM_NAME = "101"             # 房间名称（与 rooms.json 中 name 一致）
```

程序会根据 `rooms.json` 中的 `area_no` 自动识别校区，设置正确的查询参数。

> 如果之前使用手动填写编号的方式，仍然兼容。只需留空 `BUILDING_NAME` 和 `ROOM_NAME`，在 `ROOM_CONFIG` 中填写编号即可。

### ⚠️ 获取签名（Time / Sign）

`FIXED_TIME` 和 `FIXED_SIGN` 与房间绑定，**更换房间后需要重新抓包**。抓包方法：

1. 浏览器打开 [etong 电费页面](https://etong.sdjzu.edu.cn/easytong_webapp/index.html) 并登录
2. 按 `F12` 打开开发者工具 → `Network`（网络）标签
3. 在页面中选择你的楼栋和房间，点击查询
4. 在 Network 中找到 `GetPayAccInfoNew` 请求
5. 点击该请求 → `Payload`（请求载荷）→ 复制 `Time` 和 `Sign` 的值
6. 替换脚本中对应校区的 `JINAN_FIXED_TIME`/`JINAN_FIXED_SIGN` 或 `YANTAI_FIXED_TIME`/`YANTAI_FIXED_SIGN`



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
A: 在 `rooms.json` 中找到新房间的编号，替换脚本中的 `ROOM_CONFIG`。

**Q: 推送收不到？**
A: 检查推送渠道配置是否正确，至少配置一个渠道。

**Q: CTTICKET 频繁过期？**
A: 正常现象，脚本会自动重新登录。

## 📝 更新日志

### v7.3 (2026-06-04)

- 🏫 **烟台校区支持**：自动识别校区（济南/烟台），设置正确的查询参数和签名
- 📦 新增 `烟台校区_rooms.json`，烟台用户只需重命名为 `rooms.json` 即可使用
- 🔍 校区自动检测：根据 `rooms.json` 中的 `area_no` 自动切换 `AccNum`/`AreaNo`/`ItemNum`/签名
- ⚠️ **重要**：`FIXED_TIME`/`FIXED_SIGN` 与房间绑定，更换房间需重新抓包

### v7.2 (2026-06-04)

- 🏠 **配置简化**：只需填 `BUILDING_NAME` + `ROOM_NAME`，程序自动从 `rooms.json` 查找编号
- 📱 **推送可读**：推送消息显示楼栋名+房间名（如"梅二-照明 413"），不再只显示数字编号
- 🔑 **自动获取 etToken**：修复 SSO 登录后无法自动获取 token 导致查询失败的问题
- 🔄 **向后兼容**：仍支持手动填写 `BuildingNo` + `RoomNo` 的旧配置方式

### v7.0

- 首个正式版本
- SSO 自动登录
- 低电量告警 + 每日报告
- 支持企业微信 / Bark / PushPlus 推送
- systemd 开机自启

## 📜 许可证

MIT License
