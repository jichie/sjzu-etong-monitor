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
SSO_USERNAME = "你的学号"
SSO_PASSWORD = "你的密码"

# --- 房间配置 ---
ROOM_CONFIG = {
    "AccNum": "0",
    "AreaNo": "1",
    "BuildingNo": "2",
    "FloorNo": "0",
    "ItemNum": "2",
    "RoomNo": "你的房间号",
}

# --- 推送配置 ---
WECOM_WEBHOOK = "你的企业微信 Webhook"  # 至少配置一个
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
| `ROOM_CONFIG` | 房间配置 | 需要抓包获取 |
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

项目已提供 `rooms.json` 文件，包含全校所有房间号数据。

1. 打开 `rooms.json`，找到你的楼栋
2. 找到你的房间号对应的 `no` 值
3. 修改脚本中的 `ROOM_CONFIG`

示例（梅一 101 宿舍）：

```python
ROOM_CONFIG = {
    "AccNum": "0",
    "AreaNo": "1",
    "BuildingNo": "1",   # 楼栋编号
    "FloorNo": "0",
    "ItemNum": "2",
    "RoomNo": "10031",   # 房间号（从 rooms.json 查找）
}
```

### 楼栋编号对照

| 编号 | 楼栋 | 编号 | 楼栋 |
|------|------|------|------|
| 1 | 梅一 | 2 | 梅二 |
| 3 | 梅三 | 4 | 梅四 |
| 5 | 梅五 | 6 | 梅六 |
| ... | ... | ... | ... |

完整数据请查看 `rooms.json`

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
A: 重新抓包获取新的 `ROOM_CONFIG`，替换脚本中的配置。

**Q: 推送收不到？**
A: 检查推送渠道配置是否正确，至少配置一个渠道。

**Q: CTTICKET 频繁过期？**
A: 正常现象，脚本会自动重新登录。

## 📜 许可证

MIT License
