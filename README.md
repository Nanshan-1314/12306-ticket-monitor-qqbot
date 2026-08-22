> **特别声明：** 12306 平台及其所提供的各项服务，系国家公共交通基础服务体系的重要组成部分，承担全国铁路客运调度之中枢职能，具有重大的公共性与战略性意义。
>
> 鉴于此，本源码及其一切衍生作品（包括但不限于修改版本、二次开发成果及集成应用）仅限用于学习研究、合法合规的内部测试或经官方授权的非商业用途。使用者应当严格遵守国家相关法律法规及铁路运输管理规范，不得将本源码用于任何可能影响 12306 平台正常运行、威胁数据安全或损害公共利益之行为。
>
> 使用者应当合理、审慎地使用本源码及其衍生品，并自行承担因不当使用所引发的一切法律及安全责任。

# 12306 余票监控 + QQ 机器人通知

定时查询 12306 指定车次、指定席别的余票，发现有余票时通过 QQbot主动私聊通知。
> 如果项目反复报错 丢给任意Agent排查启动是最高效的解决方案
- **查询**：mcp-server-12306（经 `uvx` 运行，官方 12306 实时数据）
- **通知**：QQ 官方开放平台机器人

## 目录结构

```
├── main.py            # 入口：交互式配置 + 监控循环
├── qqbot.py           # QQ 机器人：token / 私聊 / 网关捕获 openid
├── mcp_client.py      # mcp-server-12306 的 MCP stdio 客户端
├── requirements.txt   # Python 依赖（aiohttp）
├── .env.example       # 配置模板
├── .gitignore         # 排除 .env / __pycache__
└── README.md
```

## 数据流

```
首次运行 ─▶ 交互式配置 ─▶ 写入 .env
     │
每 N 分钟 ─▶ mcp-server-12306 查余票 ─▶ 目标车次/席别有票？
     │
     └─ 有票 ─▶ QQ 机器人主动私聊 ─▶ 通知你
```

## 一、准备

前置：Python 3.10+，[uv](https://docs.astral.sh/uv/)（提供 `uvx`），以及一个 QQ 机器人（AppID / AppSecret）。

```bash
pip install -r requirements.txt
```

## 二、配置并运行

首次运行进入交互式配置：

```bash
python main.py
```

或者

```bash
momitor.exe
```


1. 选择是否调用 mcp-server-12306（`uvx` 首次运行会自动下载）。
2. 填写 QQ 机器人 AppID / AppSecret，选择是否写入 `.env`。
3. 填写出发站、到达站、车次、日期，自动获取该车次可选席别并选择，设置查询间隔（需≥10 分钟）。
4. 给机器人发一条消息，自动捕获你的 openid 并打印。
5. 3 秒后发送测试消息，然后开始监控。

示例：出发站 `北京`、到达站 `漠河`、车次 `T520`、日期 `2026/5/20`。

其它命令：

```bash
python main.py --setup     # 重新配置
python main.py --once      # 只查一次
python main.py --test-send # 发送一条测试消息
python main.py --prod      # 正式环境（默认沙箱）
```

## 三、配置项说明（.env）

| 变量 | 说明 |
|------|------|
| `QQ_APPID` | QQ 机器人 AppID（敏感） |
| `QQ_APPSECRET` | QQ 机器人 AppSecret（敏感） |
| `QQ_OPENID` | 通知接收人 openid（敏感 配置时自动捕获） |
| `QQ_ENV` | `sandbox`（默认）/ `prod` |
| `FROM_STATION` | 出发站 |
| `TO_STATION` | 到达站 |
| `TRAIN_NO` | 车次 |
| `TRAIN_DATE` | 出发日期 |
| `SEAT_KEY` | 席别键（如 `hard_sleeper`） |
| `SEAT_NAME` | 席别中文名（如 `硬卧`） |
| `INTERVAL_MIN` | 查询间隔（分钟，≥10） |

以上均由首次配置自动写入 `.env`

## 四、说明

- QQ 机器人需先在[开放平台](https://q.qq.com)创建，并把你的 QQ 号加入沙箱。
>此处QQbot无需经过审核上线 沙箱环境即可运行
- 私聊主动消息需要用户先与机器人有过消息交互（添加为好友 发送过任意消息）。

## 参考

- [mcp-server-12306](https://github.com/drfccv/mcp-server-12306)
- [QQ 机器人开放平台](https://q.qq.com)
- [QQ 机器人 WebSocket 接入](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/websocket.html)

## 特别鸣谢

- 查询能力来自 [mcp-server-12306](https://github.com/drfccv/mcp-server-12306)
- 在此致敬每一个无私奉献的开源开发者

## 许可证

Copyright (c) 2026 Nanshan-1314

本项目采用 GNU Affero General Public License v3.0（AGPL-3.0）许可证。

完整许可证文本请参见项目根目录的 `LICENSE` 文件。
