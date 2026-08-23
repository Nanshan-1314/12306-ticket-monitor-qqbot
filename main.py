#!/usr/bin/env python3
import asyncio
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from mcp_client import MCPClient
import qqbot

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

SANDBOX = "https://sandbox.api.sgroup.qq.com"
PROD = "https://api.sgroup.qq.com"

SEAT_NAMES = {
    "business": "商务座",
    "first_class": "一等座",
    "second_class": "二等座",
    "soft_sleeper": "软卧",
    "hard_sleeper": "硬卧",
    "soft_seat": "软座",
    "hard_seat": "硬座",
    "no_seat": "无座",
    "standing": "无座",
}

REQUIRED = ["QQ_APPID", "QQ_APPSECRET", "QQ_OPENID", "FROM_STATION", "TO_STATION",
            "TRAIN_NO", "TRAIN_DATE", "SEAT_KEY", "INTERVAL_MIN"]
SECRET_KEYS = ("QQ_APPID", "QQ_APPSECRET")
MIN_INTERVAL = 10


# ---- 交互辅助 ----

def ask(prompt, default=None):
    if default:
        return input(f"{prompt} [{default}]: ").strip() or default
    return input(f"{prompt}: ").strip()


def confirm(prompt):
    return input(f"{prompt} (y/n): ").strip().lower() in ("y", "yes")


def normalize_date(raw):
    parts = re.split(r"[/\-.年]", (raw or "").strip())
    if len(parts) != 3:
        return ""
    y, m, d = parts
    if not (y.isdigit() and m.isdigit() and d.isdigit()):
        return ""
    try:
        return datetime.date(int(y), int(m), int(d)).strftime("%Y-%m-%d")
    except ValueError:
        return ""


# ---- .env ----

def load_env():
    cfg = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()
    return cfg


def write_env(cfg):
    persist = cfg.get("_persist_secrets") == "1"
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        for k, v in cfg.items():
            if k.startswith("_"):
                continue
            if k in SECRET_KEYS and not persist:
                continue
            f.write(f"{k}={v}\n")


# ---- mcp-server-12306 ----

def server_ready():
    try:
        c = MCPClient()
        try:
            c.initialize()
            return True
        finally:
            c.close()
    except Exception:
        return False


def ensure_server(is_first_run):
    if not shutil.which("uvx"):
        print("未检测到 uvx，请先安装 uv：https://docs.astral.sh/uv/")
        sys.exit(1)
    if is_first_run and not confirm("是否调用 mcp-server-12306（依赖uvx安装 首次运行会自动下载）若已安装 请直接输入y"):
        print("您是打算靠信念启动项目吗？请安装依赖")
        sys.exit(1)
    subprocess.run(["uvx", "mcp-server-12306"], input="", text=True, timeout=300)
    if not server_ready():
        print("mcp-server-12306 启动失败，请在powershell中手动执行：uvx mcp-server-12306(此命令依赖uv)")
        sys.exit(1)


def query_tickets(cfg):
    c = MCPClient()
    try:
        c.initialize()
        return c.call_tool("query-tickets", {
            "from_station": cfg["FROM_STATION"],
            "to_station": cfg["TO_STATION"],
            "train_date": cfg["TRAIN_DATE"],
        })
    finally:
        c.close()


def find_train(data, train_no):
    if not isinstance(data, dict) or not data.get("success"):
        raise RuntimeError(f"查询失败: {data}")
    for t in data.get("trains", []):
        if t.get("train_no") == train_no:
            return t
    return None


def fetch_seat_keys(cfg):
    data = query_tickets(cfg)
    t = find_train(data, cfg["TRAIN_NO"])
    if t is None:
        raise RuntimeError(f"未找到车次 {cfg['TRAIN_NO']}（{cfg['TRAIN_DATE']}）")
    return list(t.get("seats", {}).keys())


def check_ticket(cfg):
    data = query_tickets(cfg)
    t = find_train(data, cfg["TRAIN_NO"])
    if t is None:
        raise RuntimeError(f"未找到车次 {cfg['TRAIN_NO']}")
    return t.get("seats", {}).get(cfg["SEAT_KEY"])


def is_available(value):
    if value is None:
        return False
    return str(value).strip() not in ("", "无", "--", "候补", "0", "null", "None")


# ---- 配置流程 ----

def setup_qq(cfg):
    print("\n—— QQ 机器人配置 ——")
    cfg["QQ_APPID"] = ask("AppID", cfg.get("QQ_APPID"))
    cfg["QQ_APPSECRET"] = ask("AppSecret", cfg.get("QQ_APPSECRET"))
    cfg["QQ_ENV"] = cfg.get("QQ_ENV") or "sandbox"
    cfg["_persist_secrets"] = "1" if confirm("是否将 AppID/AppSecret 写入 .env") else "0"
    return cfg


def setup_query(cfg):
    print("\n—— 车票查询配置 ——")
    cfg["FROM_STATION"] = ask("出发站", cfg.get("FROM_STATION"))
    cfg["TO_STATION"] = ask("到达站", cfg.get("TO_STATION"))
    cfg["TRAIN_NO"] = ask("车次", cfg.get("TRAIN_NO")).upper()
    cfg["TRAIN_DATE"] = ask_date(cfg.get("TRAIN_DATE"))
    print("正在获取该车次可选席别…")
    try:
        keys = fetch_seat_keys(cfg)
    except RuntimeError as e:
        print(f"错误: {e}")
        sys.exit(1)
    cfg["SEAT_KEY"] = choose_seat(keys)
    cfg["SEAT_NAME"] = SEAT_NAMES.get(cfg["SEAT_KEY"], cfg["SEAT_KEY"])
    cfg["INTERVAL_MIN"] = ask_interval(cfg.get("INTERVAL_MIN"))
    return cfg


def ask_date(default):
    while True:
        s = ask("出发日期（格式 2026/5/20）", default)
        d = normalize_date(s)
        if d:
            return d
        print("日期格式无效，请用 yyyy/m/d。")


def _read_key():
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
        return ch
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            return {"[A": "up", "[B": "down"}.get(sys.stdin.read(2), "")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, old)


def _select_number(items):
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it}")
    while True:
        s = input("选择席别（序号）: ").strip()
        if s.isdigit():
            i = int(s) - 1
            if 0 <= i < len(items):
                return i
        print("无效选择。")


def select_option(items):
    """方向键或 w/s 选择，回车确认；非终端使用将自动回退为序号输入。""" #说实话这个应该不需要人手把手教
    if not sys.stdin.isatty():
        return _select_number(items)
    idx = 0
    n = len(items)

    def draw():
        sys.stdout.write("\x1b[?25l")
        for i, it in enumerate(items):
            mark = " > " if i == idx else "   "
            sys.stdout.write(f"\r{mark}{it}\x1b[K\n")
        sys.stdout.write(f"\x1b[{n}A")
        sys.stdout.flush()

    draw()
    while True:
        k = _read_key()
        if k in ("up", "w", "W") and idx > 0:
            idx -= 1
            draw()
        elif k in ("down", "s", "S") and idx < n - 1:
            idx += 1
            draw()
        elif k in ("\r", "\n", " "):
            sys.stdout.write(f"\x1b[{n}B\x1b[?25h\n")
            sys.stdout.flush()
            return idx
        elif k == "\x03":
            sys.stdout.write("\x1b[?25h\n")
            raise KeyboardInterrupt


def choose_seat(keys):
    if not keys:
        print("该车次暂无席别信息。")
        return ""
    items = [SEAT_NAMES.get(k, k) for k in keys]
    print("可选席别：")
    idx = select_option(items)
    print(f"已选择: {items[idx]}")
    return keys[idx]


def ask_interval(default):
    while True:
        s = ask(f"查询间隔（最低 {MIN_INTERVAL}min一次）", default)
        # 【法律与安全警告】修改查询间隔常量存在导致IP/账号被12306风控封禁的风险。
        # 作者已设定最低10分钟的安全阈值。若您强行修改此值，即表示：
        # 1. 您已完全阅读并理解本项目的免责声明（https://github.com/Nanshan-1314/12306-ticket-monitor-qqbot/blob/main/DISCLAIMER.md）；
        # 2. 您明确知晓该行为可能违反第三方平台服务协议；
        # 3. 您自愿承担由此导致的一切账号封禁、IP屏蔽及潜在和连带的法律后果。
        # 4. 请善待12306公共资源
        if s.isdigit() and int(s) >= MIN_INTERVAL:
            return s
        print(f"间隔需为不小于 {MIN_INTERVAL} 的整数。")


def setup_openid(cfg):
    print("\n—— 获取 openid ——")
    try:
        token = qqbot.get_access_token(cfg["QQ_APPID"], cfg["QQ_APPSECRET"])
    except RuntimeError as e:
        print(f"获取 token 失败（AppID/AppSecret 可能错误）: {e}")
        sys.exit(1)
    print("请使用给机器人发任意一条消息以获取OpenID......") #手机电脑均可
    try:
        openid = asyncio.run(qqbot.capture_openid(token, api_base_for(cfg)))
    except Exception as e:
        print(f"捕获失败: {e}")
        sys.exit(1)
    if not openid:
        print("超时未收到消息，请重新运行。")
        sys.exit(1)
    print(f"\n你的 openid为: {openid}")
    cfg["QQ_OPENID"] = openid
    return cfg


def run_setup(cfg):
    is_first_run = not os.path.exists(ENV_FILE)
    print("=" * 40)
    print("首次配置" if is_first_run else "重新配置")
    print("=" * 40)
    ensure_server(is_first_run)
    cfg = setup_qq(cfg)
    mode = choose_mode()
    cfg["MODE"] = mode
    cfg = setup_openid(cfg)
    if mode == "A":
        cfg = setup_query(cfg)
        print("3 秒后发送测试消息…")
        time.sleep(3)
        try:
            send_test(cfg)
        except RuntimeError as e:
            print(f"测试消息发送失败: {e}")
    write_env(cfg)
    return cfg


# ---- 运行 ----

def api_base_for(cfg):
    return PROD if cfg.get("QQ_ENV") == "prod" else SANDBOX


def notify_text(cfg):
    return f"查询到{cfg['TRAIN_NO']}次列车需求席位发生变化 请尽快前往12306处理"


def send_test(cfg):
    token = qqbot.get_access_token(cfg["QQ_APPID"], cfg["QQ_APPSECRET"])
    r = qqbot.send_c2c(token, cfg["QQ_OPENID"], "这是一条测试消息 如果你看到了这条消息 则证明QQBot配置正常", api_base_for(cfg))
    print("测试消息已发送:", json.dumps(r, ensure_ascii=False))


def send_notify(cfg):
    token = qqbot.get_access_token(cfg["QQ_APPID"], cfg["QQ_APPSECRET"])
    return qqbot.send_c2c(token, cfg["QQ_OPENID"], notify_text(cfg), api_base_for(cfg))


def run_once(cfg):
    v = check_ticket(cfg)
    print(f"[{time.strftime('%H:%M:%S')}] {cfg['TRAIN_NO']} {cfg['SEAT_NAME']}: {v}")
    if is_available(v):
        try:
            print("已发送通知:", json.dumps(send_notify(cfg), ensure_ascii=False))
        except Exception as e:
            print(f"通知发送失败: {e}")


def run_loop(cfg):
    interval = int(cfg["INTERVAL_MIN"]) * 60
    print(f"\n开始监控 {cfg['TRAIN_NO']} {cfg['FROM_STATION']}->{cfg['TO_STATION']} "
          f"{cfg['TRAIN_DATE']} {cfg['SEAT_NAME']}，每 {interval // 60} 分钟一次（Ctrl+C 退出）")
    notified = False
    while True:
        try:
            v = check_ticket(cfg)
            cur = is_available(v)
            print(f"[{time.strftime('%H:%M:%S')}] {cfg['SEAT_NAME']}: {v}", flush=True)
            if cur and not notified:
                try:
                    print("已发送通知:", json.dumps(send_notify(cfg), ensure_ascii=False), flush=True)
                    notified = True
                except Exception as e:
                    print(f"通知发送失败，下轮重试: {e}", flush=True)
            if not cur:
                notified = False
        except KeyboardInterrupt:
            print("\n已退出")
            break
        except Exception as e:
            print(f"错误: {e}", flush=True)
        time.sleep(interval)


# ---- QQbot 模式 ----

def choose_mode():
    print("\n—— 选择运行模式 ——")
    print("  A. 单次使用模式（终端定时监控，现有功能）")
    print("  B. QQbot 模式（通过 bot 指令远程控制）")
    while True:
        s = input("请选择 A/B: ").strip().upper()
        if s in ("A", "B"):
            return s
        print("无效选择，请输入 A 或 B。")


def query_configured(cfg):
    return all(cfg.get(k) for k in ("FROM_STATION", "TO_STATION", "TRAIN_NO",
                                    "TRAIN_DATE", "SEAT_KEY", "INTERVAL_MIN"))


class QQBotController:
    """QQ 指令控制：/start /Refresh now /Rate limit [int] /stop。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.owner = cfg.get("QQ_OPENID", "")
        self.lock = threading.Lock()
        self.interval_min = int(cfg.get("INTERVAL_MIN", MIN_INTERVAL))
        self.stop_event = threading.Event()
        self.monitor_thread = None
        self.start_step = None
        self.tmp = {}
        self.refresh_times = []
        self.refresh_limit = 2
        self.refresh_window = 10 * 60

    async def handle(self, openid, content):
        if openid != self.owner:
            return None
        c = content.strip()
        low = c.lower()
        if low == "/start":
            return self.cmd_start()
        if low == "/stop":
            return self.cmd_stop()
        if low.startswith("/refresh"):
            return await self.cmd_refresh()
        if low.startswith("/rate"):
            return self.cmd_rate(c)
        if self.start_step is not None:
            return await self.cmd_start_step(c)
        return "未知命令。可用：/start、/Refresh now、/Rate limit [int]、/stop"

    # ---- /start ----
    def cmd_start(self):
        self._stop_monitor()
        self.tmp = {}
        self.start_step = "FROM_STATION"
        return "开始初始化车次信息。\n请输入出发站："

    async def cmd_start_step(self, text):
        step = self.start_step
        if step == "FROM_STATION":
            self.tmp["FROM_STATION"] = text.strip()
            self.start_step = "TO_STATION"
            return "请输入到达站："
        if step == "TO_STATION":
            self.tmp["TO_STATION"] = text.strip()
            self.start_step = "TRAIN_NO"
            return "请输入车次（如 T520）："
        if step == "TRAIN_NO":
            self.tmp["TRAIN_NO"] = text.strip().upper()
            if self.tmp.get("TRAIN_DATE"):
                return await self._fetch_seats()
            self.start_step = "TRAIN_DATE"
            return "请输入出发日期（格式 2026/5/20）："
        if step == "TRAIN_DATE":
            d = normalize_date(text)
            if not d:
                return "日期格式无效，请用 yyyy/m/d，重新输入："
            self.tmp["TRAIN_DATE"] = d
            return await self._fetch_seats()
        if step == "SEAT":
            if not text.strip().isdigit():
                return "请回复席别序号："
            keys = self.tmp.get("seat_keys", [])
            idx = int(text.strip()) - 1
            if not (0 <= idx < len(keys)):
                return "序号无效，请重新回复："
            self.tmp["SEAT_KEY"] = keys[idx]
            self.tmp["SEAT_NAME"] = SEAT_NAMES.get(keys[idx], keys[idx])
            self.start_step = "INTERVAL"
            return f"已选择 {self.tmp['SEAT_NAME']}。请输入查询间隔（分钟，≥{MIN_INTERVAL}）："
        if step == "INTERVAL":
            s = text.strip()
            if not (s.isdigit() and int(s) >= MIN_INTERVAL):
                return f"间隔需为不小于 {MIN_INTERVAL} 的整数，重新输入："
            self.tmp["INTERVAL_MIN"] = s
            self.start_step = None
            with self.lock:
                for k in ("FROM_STATION", "TO_STATION", "TRAIN_NO", "TRAIN_DATE",
                          "SEAT_KEY", "SEAT_NAME", "INTERVAL_MIN"):
                    self.cfg[k] = self.tmp[k]
                self.interval_min = int(s)
            self._start_monitor()
            return (f"配置完成：{self.cfg['TRAIN_NO']} {self.cfg['FROM_STATION']}->{self.cfg['TO_STATION']} "
                    f"{self.cfg['TRAIN_DATE']} {self.cfg['SEAT_NAME']}，每 {self.interval_min} 分钟查询一次。\n监控已启动。")
        return "输入无效。"

    async def _fetch_seats(self):
        try:
            keys = await asyncio.to_thread(fetch_seat_keys, self.tmp)
        except RuntimeError as e:
            self.start_step = "TRAIN_NO"
            return f"未找到车次，请重新输入车次（{e}）："
        if not keys:
            self.start_step = None
            self.tmp = {}
            return "该车次暂无席别信息，已取消。"
        self.tmp["seat_keys"] = keys
        self.start_step = "SEAT"
        lines = "\n".join(f"{i + 1}. {SEAT_NAMES.get(k, k)}" for i, k in enumerate(keys))
        return f"该车次可选席别：\n{lines}\n请回复序号："

    # ---- /Refresh now ----
    async def cmd_refresh(self):
        if not query_configured(self.cfg):
            return "尚未初始化车次信息，请先发送 /start。"
        now = time.time()
        self.refresh_times = [t for t in self.refresh_times if now - t < self.refresh_window]
        if len(self.refresh_times) >= self.refresh_limit:
            return "操作过于频繁，/Refresh now 每 10 分钟最多 2 次，请稍后再试。"
        self.refresh_times.append(now)
        with self.lock:
            snapshot = dict(self.cfg)
        try:
            v = await asyncio.to_thread(check_ticket, snapshot)
        except Exception as e:
            return f"查询失败：{e}"
        seat = snapshot.get("SEAT_NAME") or snapshot.get("SEAT_KEY")
        if is_available(v):
            try:
                await asyncio.to_thread(send_notify, snapshot)
            except Exception as e:
                return f"已查到有票，但通知发送失败：{e}"
            self._stop_monitor()
            return f"{snapshot['TRAIN_NO']} {seat} 当前有票（{v}），已发送通知并停止监控。"
        return f"{snapshot['TRAIN_NO']} {seat} 当前：{v}（暂无余票）"

    # ---- /Rate limit ----
    def cmd_rate(self, text):
        num = None
        for p in text.split()[1:]:
            if p.isdigit():
                num = int(p)
                break
        if num is None:
            return f"用法：/Rate limit [int]，例如 /Rate limit 15（不得低于 {MIN_INTERVAL}）"
        if num < MIN_INTERVAL:
            return f"间隔不得低于 {MIN_INTERVAL} 分钟。"
        with self.lock:
            self.interval_min = num
            self.cfg["INTERVAL_MIN"] = str(num)
        return f"查询间隔已调整为 {num} 分钟。"

    # ---- /stop ----
    def cmd_stop(self):
        self._stop_monitor()
        return "监控已停止。可随时 /start 重新启动。"

    # ---- 后台监控 ----
    def _start_monitor(self):
        with self.lock:
            if self.monitor_thread and self.monitor_thread.is_alive():
                return
            self.stop_event.clear()
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitor_thread.start()

    def _stop_monitor(self):
        self.stop_event.set()
        with self.lock:
            t = self.monitor_thread
            self.monitor_thread = None
        if t:
            t.join(timeout=1)

    def _monitor_loop(self):
        notified = False
        while not self.stop_event.is_set():
            with self.lock:
                snapshot = dict(self.cfg)
                interval = self.interval_min
            try:
                v = check_ticket(snapshot)
                cur = is_available(v)
                if cur and not notified:
                    send_notify(snapshot)
                    notified = True
                if not cur:
                    notified = False
            except Exception as e:
                print(f"[监控] 错误: {e}", flush=True)
            self.stop_event.wait(interval * 60)


def qqbot_mode(cfg):
    token = qqbot.get_access_token(cfg["QQ_APPID"], cfg["QQ_APPSECRET"])
    controller = QQBotController(cfg)
    print("QQbot 模式已启动，等待指令…（/start /Refresh now /Rate limit [int] /stop）")
    try:
        asyncio.run(qqbot.listen_c2c(token, api_base_for(cfg), controller.handle))
    except KeyboardInterrupt:
        print("\n已退出")


def _start(cfg):
    if cfg.get("MODE") == "B":
        qqbot_mode(cfg)
    else:
        run_loop(cfg)


def configured(cfg):
    return all(cfg.get(k) for k in REQUIRED)


def qq_configured(cfg):
    return bool(cfg.get("QQ_APPID") and cfg.get("QQ_APPSECRET") and cfg.get("QQ_OPENID"))


def main():
    args = sys.argv[1:]
    cfg = load_env()
    if "--prod" in args:
        cfg["QQ_ENV"] = "prod"

    if "--setup" in args:
        cfg = run_setup(cfg)
        _start(cfg)
        return

    if "--test-send" in args:
        if not qq_configured(cfg):
            cfg = run_setup(cfg)
        send_test(cfg)
        return

    if "--once" in args:
        if not query_configured(cfg):
            cfg = run_setup(cfg)
        run_once(cfg)
        return

    if not qq_configured(cfg):
        cfg = run_setup(cfg)

    _start(cfg)


if __name__ == "__main__":
    main()
