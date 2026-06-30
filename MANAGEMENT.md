# 电费监控服务管理手册

## 📍 部署路径

```
/opt/etong/
├── etong_monitor.py       # 主程序
├── .env                   # 配置（权限 600）
├── config.json            # Token 持久化
├── etong-monitor.service  # systemd 服务文件
├── rooms.json             # 济南校区房间数据
└── 烟台校区_rooms.json     # 烟台校区房间数据
```

---

## ⚡ 一、服务管理（systemd）

```bash
# 启动服务
sudo systemctl start etong-monitor

# 停止服务
sudo systemctl stop etong-monitor

# 重启服务
sudo systemctl restart etong-monitor

# 查看运行状态
sudo systemctl status etong-monitor

# 设置开机自启
sudo systemctl enable etong-monitor

# 关闭开机自启
sudo systemctl disable etong-monitor

# 重新加载服务配置（修改 .service 文件后执行）
sudo systemctl daemon-reload
sudo systemctl restart etong-monitor
```

---

## 📋 二、日志查看

```bash
# 实时跟踪日志
tail -f /var/log/etong.log

# 查看最近 N 行
tail -50 /var/log/etong.log

# 只看心跳
grep '💓' /var/log/etong.log | tail -10

# 只看电费查询
grep '电量\|查询' /var/log/etong.log | tail -10

# 只看告警和推送
grep '推送\|告警\|不足\|失败' /var/log/etong.log | tail -10

# 查看日志总大小
ls -lh /var/log/etong.log

# 清空日志（日志文件过大的时候）
sudo truncate -s 0 /var/log/etong.log

# systemd 日志（如果标准输出走 journalctl）
sudo journalctl -u etong-monitor -n 50 --no-pager
sudo journalctl -u etong-monitor -f          # 实时跟踪
```

---

## 🔧 三、日常运维

### 手动查询一次电费

```bash
cd /opt/etong && python3 etong_monitor.py --once
```

### 修改配置

```bash
vi /opt/etong/.env
# 改完后重启服务
sudo systemctl restart etong-monitor
```

### 更新脚本（从 GitHub 拉取新版本）

```bash
# 从 GitHub 下载最新版
sudo wget -O /opt/etong/etong_monitor.py \
  https://raw.githubusercontent.com/jichie/sjzu-etong-monitor/main/etong_monitor.py

# 重启服务
sudo systemctl restart etong-monitor
```

### 查看当前 Token 状态

```bash
cat /opt/etong/config.json | python3 -m json.tool
```

### 查看当前房间配置

```bash
grep -E '^SSO_|BUILDING|ROOM_NAME' /opt/etong/.env
```

---

## 🩺 四、故障排查

### 症状：推送失败 `SSL: SSLV3_ALERT_BAD_RECORD_MAC`

**原因**：本机 OpenSSL 与企业微信 API 兼容性问题。  
**解决**：脚本已内置系统 `curl` 命令推送（3 次重试），无需手动干预。

### 症状：`CTTICKET 已过期`

**原因**：CTTICKET 超时（有效期数小时到数天）。  
**解决**：脚本会自动通过 SSO 重新登录获取新 Token，无需手动操作。

### 症状：`Temporary failure in name resolution`

**原因**：DNS 解析临时失败（网络波动）。  
**解决**：心跳线程会在 10 分钟后自动重试。

### 症状：查不到电费 `用户不存在`

**原因**：济南学号查不了烟台电费，或房间配置错误。  
**解决**：检查 `/opt/etong/.env` 中的 `BUILDING_NAME` 和 `ROOM_NAME`。

### 症状：服务起不来

```bash
# 查看详细错误
sudo journalctl -u etong-monitor -n 30 --no-pager

# 手动运行看报错
cd /opt/etong && python3 etong_monitor.py --once
```

---

## 🗑️ 五、清理维护

```bash
# 清空日志
sudo truncate -s 0 /var/log/etong.log

# 清理旧的 Token 缓存（强制重新登录）
rm -f /tmp/etong_cookies.json /tmp/etong_state.json
sudo systemctl restart etong-monitor

# 删除旧版遗留文件
rm -f /opt/etong/query.py /opt/etong/query.py.bak /opt/etong/login_helper.py

# 重新爬取房间数据
pip3 install requests pycryptodome
cd /opt/etong && python3 scrape_rooms.py
```

---

## 📊 六、一键状态检查

```bash
# 一行命令查看整体运行状况
echo "=== 服务状态 ===" && \
sudo systemctl is-active etong-monitor && \
echo "" && \
echo "=== 最新日志 ===" && \
tail -6 /var/log/etong.log && \
echo "" && \
echo "=== 心跳状态 ===" && \
grep '💓 心跳保活:' /var/log/etong.log | tail -3 && \
echo "" && \
echo "=== 今日日报 ===" && \
grep "$(date +%Y-%m-%d)" /var/log/etong.log | grep '日报\|电量' | tail -3
```

输出示例：
```
=== 服务状态 ===
active

=== 最新日志 ===
[2026-06-30 10:01:44] ⚡ 当前电量: 13.59 度
[2026-06-30 10:01:44] 💤 下次检查: 11:01:44 (60分钟后)

=== 心跳状态 ===
[2026-06-30 10:01:44] 💓 心跳保活: session 已刷新

=== 今日日报 ===
[2026-06-30 19:10:05] 📊 发送每日电量报告
```
