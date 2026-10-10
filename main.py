"""
堂主跑腿
========

一个 Windows 桌面悬浮控制器：
- 自动查找 / 手动选择 Chrome 并记住路径
- 用带远程调试端口的独立 Chrome 实例打开攻略网页
- 通过 Chrome DevTools Protocol 控制网页里的 HTML5 视频播放 / 暂停 / 快进
- 全局鼠标侧键 / 键盘快捷键，全部可在界面重新绑定
- 任务进度记录，关软件自动保存
- 置顶、鼠标穿透、系统托盘
- 退出程序时一并关闭由本软件打开的浏览器
"""

import os
import sys
import json
import shutil
import itertools
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import websocket
from pynput import keyboard, mouse

from PySide6.QtCore import Qt, Signal, QObject, QSettings, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QLabel,
    QCheckBox,
    QSystemTrayIcon,
    QMenu,
    QMessageBox,
    QFileDialog,
    QPlainTextEdit,
    QSpinBox,
)


# =====================================================================
# 常量
# =====================================================================

APP_TITLE = "堂主跑腿"

DEBUG_PORT = 9228
DEBUG_URL = f"http://127.0.0.1:{DEBUG_PORT}"

PROFILE_DIR = Path.home() / "GameGuideChromeProfile"

SETTINGS = QSettings("GameGuideOverlay", "ChromeOverlay")

CHROME_CANDIDATES = [
    shutil.which("chrome"),
    shutil.which("chrome.exe"),
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome Beta\Application\chrome.exe"),
]

CHROME_EXTRA_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=CalculateNativeWinOcclusion",
]

DEBOUNCE_MOUSE = 0.28
DEBOUNCE_HOTKEY = 0.30

FORBIDDEN_KEY_NAMES = {
    "esc",
    "ctrl", "ctrl_l", "ctrl_r",
    "alt", "alt_l", "alt_r", "alt_gr",
    "shift", "shift_l", "shift_r",
    "cmd", "cmd_l", "cmd_r",
}

DEFAULT_BIND_PLAY = "Mouse 5"
DEFAULT_BIND_FORWARD = "F10"
DEFAULT_FORWARD_SECONDS = 3


# =====================================================================
# 图标加载
# =====================================================================

def load_app_icon():
    if getattr(sys, "frozen", False):
        icon = QIcon(sys.executable)
        if not icon.isNull():
            return icon

    for name in ("icon.ico", "icon.png"):
        path = Path(__file__).resolve().parent / name
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon

    return QIcon()


# =====================================================================
# 通用工具
# =====================================================================

def find_chrome_candidates():
    found, seen = [], set()
    for candidate in CHROME_CANDIDATES:
        if not candidate:
            continue
        candidate = os.path.abspath(os.path.expandvars(candidate.strip().strip('"')))
        if not os.path.isfile(candidate):
            continue
        if not candidate.lower().endswith("chrome.exe"):
            continue
        key = os.path.normcase(candidate)
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    return found


def validate_chrome_path(path):
    if not path:
        return False
    path = os.path.expandvars(path.strip().strip('"'))
    return os.path.isfile(path) and os.path.basename(path).lower() == "chrome.exe"


def fetch_debug_targets(retries=4, delay=0.25):
    last_exc = None
    for _ in range(retries):
        try:
            request = urllib.request.Request(
                f"{DEBUG_URL}/json/list",
                headers={"User-Agent": "GameGuideOverlay"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            time.sleep(delay)
    raise last_exc


# =====================================================================
# 按键工具
# =====================================================================

def key_to_string(key):
    try:
        if isinstance(key, keyboard.Key):
            name = getattr(key, "name", None)
            if not name:
                return None
            if name.lower() in FORBIDDEN_KEY_NAMES:
                return None
            return name.upper()

        if isinstance(key, keyboard.KeyCode):
            ch = key.char
            if ch:
                return ch.upper()
            vk = getattr(key, "vk", None)
            if vk is not None and vk >= 0:
                return f"VK{vk}"
            return None

        if isinstance(key, mouse.Button):
            name = getattr(key, "name", None) or str(key)
            name = name.lower()
            if name in ("x1", "x2"):
                return f"Mouse {name.upper()}"
            if name in ("left", "right", "middle"):
                return f"Mouse {name.capitalize()}"
            return f"Mouse {name}"

        return None
    except Exception:
        return None


def format_key_display(s):
    if not s:
        return "未绑定"
    if s.startswith("Mouse "):
        return s
    if s.startswith("VK"):
        return "特殊键 " + s
    return s


# =====================================================================
# CDP 异常
# =====================================================================

class CDPError(Exception):
    pass


class CDPConnectionError(Exception):
    pass


# =====================================================================
# CDP 会话
# =====================================================================

class CDPSession:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = None
        self.running = False
        self.reader = None

        self.lock = threading.RLock()
        self._id_counter = itertools.count(1)
        self._waiters = {}
        self._responses = {}
        self._contexts = {}

    def _ensure_connected(self):
        if self.ws is not None and self.running:
            try:
                if getattr(self.ws, "connected", True):
                    return
            except Exception:
                pass

        self.close()

        self.ws = websocket.create_connection(
            self.ws_url,
            timeout=8,
            suppress_origin=True,
            enable_multithread=True,
        )
        self.running = True
        self.reader = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader.start()

        for method in ("Page.enable", "Runtime.enable"):
            try:
                self.call(method, timeout=2)
            except Exception:
                pass

    def close(self):
        self.running = False
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        with self.lock:
            self._contexts.clear()

    def _reader_loop(self):
        while self.running:
            ws = self.ws
            if ws is None:
                break
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except Exception:
                continue

            msg_id = message.get("id")
            if msg_id is not None:
                waiter = self._waiters.get(msg_id)
                if waiter is not None:
                    self._responses[msg_id] = message
                    waiter.set()

        self.running = False
        with self.lock:
            waiters = list(self._waiters.values())
        for waiter in waiters:
            waiter.set()

    def call(self, method, params=None, timeout=8.0):
        with self.lock:
            try:
                self._ensure_connected()
            except Exception as exc:
                raise CDPConnectionError(f"无法建立调试连接：{exc}") from exc

            msg_id = next(self._id_counter)
            waiter = threading.Event()
            self._waiters[msg_id] = waiter

            payload = {
                "id": msg_id,
                "method": method,
                "params": params or {},
            }

            try:
                self.ws.send(json.dumps(payload))
            except Exception as exc:
                self._waiters.pop(msg_id, None)
                self.close()
                raise CDPConnectionError(f"发送消息失败：{exc}") from exc

        if not waiter.wait(timeout):
            with self.lock:
                self._waiters.pop(msg_id, None)
                self._responses.pop(msg_id, None)
            raise TimeoutError(f"等待 {method} 响应超时")

        with self.lock:
            response = self._responses.pop(msg_id, None)
            self._waiters.pop(msg_id, None)

        if response is None:
            raise CDPConnectionError("连接已断开")

        if "error" in response:
            raise CDPError(str(response["error"]))

        return response.get("result", {})

    def evaluate(self, expression, context_id=None, timeout=8.0):
        params = {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": True,
        }
        if context_id is not None:
            params["contextId"] = context_id

        try:
            result = self.call("Runtime.evaluate", params, timeout=timeout)
        except CDPError:
            params.pop("userGesture", None)
            result = self.call("Runtime.evaluate", params, timeout=timeout)

        inner = result.get("result", {})

        if isinstance(inner, dict) and inner.get("subtype") == "error":
            raise CDPError(inner.get("description", "脚本执行失败"))

        if isinstance(inner, dict):
            return inner.get("value")
        return None

    def list_frames(self, timeout=5.0):
        tree = self.call("Page.getFrameTree", timeout=timeout)
        frames = []

        def walk(node):
            frame = node.get("frame", {})
            fid = frame.get("id")
            if fid:
                frames.append(fid)
            for child in node.get("childFrames", []) or []:
                walk(child)

        walk(tree.get("frameTree", {}))
        return frames

    def _get_context(self, frame_id, timeout=5.0):
        cached = self._contexts.get(frame_id)
        if cached is not None:
            return cached

        world = self.call(
            "Page.createIsolatedWorld",
            {
                "frameId": frame_id,
                "worldName": "GameGuideOverlay",
                "grantUniveralAccess": True,
            },
            timeout=timeout,
        )
        cid = world.get("executionContextId")
        if cid is None:
            return None
        self._contexts[frame_id] = cid
        return cid

    def evaluate_all_frames(self, expression, timeout=8.0):
        """先主上下文，再遍历所有 frame。不再静默吞异常。"""
        last_error = None

        # 1) 主 frame 主上下文
        try:
            value = self.evaluate(expression, timeout=timeout)
            if isinstance(value, dict) and value.get("found"):
                return value
        except Exception as exc:
            last_error = exc

        # 2) 遍历所有 frame
        try:
            frame_ids = self.list_frames()
        except Exception:
            frame_ids = []

        current = set(frame_ids)
        for fid in list(self._contexts.keys()):
            if fid not in current:
                self._contexts.pop(fid, None)

        for fid in frame_ids:
            try:
                cid = self._get_context(fid, timeout=timeout)
                if cid is None:
                    continue

                value = self.evaluate(expression, context_id=cid, timeout=timeout)
                if isinstance(value, dict) and value.get("found"):
                    return value
            except Exception as exc:
                last_error = exc
                self._contexts.pop(fid, None)
                continue

        if last_error is not None:
            raise last_error

        return None


# =====================================================================
# JS 脚本（关键修复：用 (function() { ... })() 包裹）
# =====================================================================

_JS_FIND_VIDEO = r"""
    var collected = [];
    var seen = new Set();

    function gather(root, depth) {
        if (!root || depth > 8) return;

        try {
            var vids = root.querySelectorAll('video');
            for (var i = 0; i < vids.length; i++) {
                var v = vids[i];
                if (!seen.has(v)) {
                    seen.add(v);
                    collected.push(v);
                }
            }
        } catch (e) {}

        try {
            var all = root.querySelectorAll('*');
            for (var j = 0; j < all.length; j++) {
                var el = all[j];
                if (el && el.shadowRoot) {
                    gather(el.shadowRoot, depth + 1);
                }
            }
        } catch (e) {}
    }

    gather(document, 0);

    if (collected.length === 0) {
        return { found: false };
    }

    var video = null;

    try {
        var pip = document.pictureInPictureElement;
        if (pip && pip.tagName === 'VIDEO' && collected.indexOf(pip) >= 0) {
            video = pip;
        }
    } catch (e) {}

    if (!video) {
        for (var k = 0; k < collected.length; k++) {
            var vv = collected[k];
            if (!vv.paused && vv.readyState >= 2) {
                video = vv;
                break;
            }
        }
    }

    if (!video) {
        var bestArea = -1;
        for (var m = 0; m < collected.length; m++) {
            var vm = collected[m];
            var area = 0;
            try {
                var r = vm.getBoundingClientRect();
                area = (r.width || 0) * (r.height || 0);
            } catch (e) {}
            if (area > bestArea) {
                bestArea = area;
                video = vm;
            }
        }
    }

    if (!video) {
        video = collected[0];
    }
"""


PLAY_PAUSE_JS = "(function() {" + _JS_FIND_VIDEO + r"""
    var action;
    var detail = '';
    var wasPaused = video.paused;

    if (wasPaused) {
        try {
            var p = video.play();
            if (p && typeof p.catch === 'function') {
                p.catch(function () {});
            }
            action = 'play';
        } catch (e) {
            action = 'error';
            detail = (e && e.message) || '播放失败';
        }
    } else {
        try {
            video.pause();
            action = 'pause';
        } catch (e) {
            action = 'error';
            detail = (e && e.message) || '暂停失败';
        }
    }

    return {
        found: true,
        action: action,
        detail: detail,
        total: collected.length,
        paused: video.paused
    };
})()
"""


FORWARD_JS_TEMPLATE = "(function() {" + _JS_FIND_VIDEO + r"""
    var seconds = __SECONDS__;
    var oldTime = video.currentTime;
    var newTime = Math.min(oldTime + seconds, video.duration || (oldTime + seconds));
    video.currentTime = newTime;

    return {
        found: true,
        action: 'forward',
        detail: '',
        total: collected.length,
        oldTime: oldTime,
        newTime: newTime,
        seconds: seconds,
        duration: video.duration || 0
    };
})()
"""


# =====================================================================
# Qt 信号
# =====================================================================

class Signals(QObject):
    status = Signal(str)
    toggle_passthrough = Signal()
    toggle_visible = Signal()
    toggle_play = Signal()
    toggle_forward = Signal()
    bind_captured = Signal(str)


# =====================================================================
# 悬浮控制器
# =====================================================================

class Overlay(QWidget):

    def __init__(self):
        super().__init__()

        self.signals = Signals()
        self.signals.status.connect(self.set_status)
        self.signals.toggle_passthrough.connect(self.toggle_passthrough)
        self.signals.toggle_visible.connect(self.toggle_visible)
        self.signals.toggle_play.connect(self.play_pause)
        self.signals.toggle_forward.connect(self.forward)
        self.signals.bind_captured.connect(self._on_bind_captured)

        self.passthrough = False
        self.chrome_process = None
        self.target_url = ""

        self.bind_play = SETTINGS.value("bind_play", "")
        if not self.bind_play:
            old_side = SETTINGS.value("side_button", DEFAULT_BIND_PLAY)
            self.bind_play = old_side if old_side else DEFAULT_BIND_PLAY
            SETTINGS.setValue("bind_play", self.bind_play)

        self.bind_forward = SETTINGS.value("bind_forward", DEFAULT_BIND_FORWARD)

        try:
            self.forward_seconds = int(SETTINGS.value("forward_seconds", DEFAULT_FORWARD_SECONDS))
        except Exception:
            self.forward_seconds = DEFAULT_FORWARD_SECONDS

        self._binding_action = None

        self._cdp_sessions = {}
        self._cdp_lock = threading.RLock()

        self._last_play_time = 0.0
        self._last_forward_time = 0.0
        self._last_f8_time = 0.0
        self._last_f9_time = 0.0

        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowIcon(load_app_icon())
        self.resize(560, 680)
        self.setMinimumWidth(500)

        self.build_ui()
        self.create_tray()
        self.apply_window_style()

        self._start_listeners()
        self._start_watchdog()

    # -----------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------

    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel(APP_TITLE)
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        layout.addWidget(QLabel("Chrome 浏览器程序路径："))

        path_row = QHBoxLayout()
        saved_path = SETTINGS.value("chrome_path", "")
        if not saved_path:
            candidates = find_chrome_candidates()
            saved_path = candidates[0] if candidates else ""

        self.chrome_path_input = QLineEdit(saved_path)
        self.chrome_path_input.setPlaceholderText(
            r"请选择 chrome.exe，例如 C:\...\Chrome\Application\chrome.exe"
        )
        self.chrome_path_input.editingFinished.connect(self.save_chrome_path)
        path_row.addWidget(self.chrome_path_input, 1)

        browse_button = QPushButton("选择浏览器")
        browse_button.clicked.connect(self.browse_chrome)
        path_row.addWidget(browse_button)

        layout.addLayout(path_row)

        browser_actions = QHBoxLayout()
        detect_button = QPushButton("自动查找 Chrome")
        detect_button.clicked.connect(self.auto_find_chrome)
        browser_actions.addWidget(detect_button)

        test_button = QPushButton("检查浏览器路径")
        test_button.clicked.connect(self.test_chrome_path)
        browser_actions.addWidget(test_button)

        layout.addLayout(browser_actions)

        layout.addWidget(QLabel("攻略网页地址："))

        self.url_input = QLineEdit(self.target_url)
        self.url_input.setPlaceholderText("输入网址，例如 https://www.bilibili.com/")
        layout.addWidget(self.url_input)

        site_row = QHBoxLayout()
        custom_button = QPushButton("打开输入网址")
        custom_button.clicked.connect(self.open_custom_url)
        site_row.addWidget(custom_button)
        layout.addLayout(site_row)

        layout.addWidget(QLabel("视频控制："))

        play_row = QHBoxLayout()
        play_button = QPushButton("播放 / 暂停")
        play_button.clicked.connect(self.play_pause)
        play_row.addWidget(play_button)

        forward_button = QPushButton("快进")
        forward_button.clicked.connect(self.forward)
        play_row.addWidget(forward_button)

        refresh_button = QPushButton("检查网页连接")
        refresh_button.clicked.connect(self.refresh_status)
        play_row.addWidget(refresh_button)

        layout.addLayout(play_row)

        sec_row = QHBoxLayout()
        sec_row.addWidget(QLabel("快进秒数："))

        self.forward_seconds_spin = QSpinBox()
        self.forward_seconds_spin.setRange(1, 600)
        self.forward_seconds_spin.setValue(self.forward_seconds)
        self.forward_seconds_spin.setSuffix(" 秒")
        self.forward_seconds_spin.valueChanged.connect(self.change_forward_seconds)
        sec_row.addWidget(self.forward_seconds_spin)

        sec_row.addStretch()
        layout.addLayout(sec_row)

        layout.addWidget(QLabel("按键绑定（点击「重新绑定」后按你想用的键）："))

        row_play = QHBoxLayout()
        row_play.addWidget(QLabel("播放 / 暂停："))

        self.play_bind_label = QLabel(format_key_display(self.bind_play))
        self.play_bind_label.setStyleSheet(
            "background:#30343c;border:1px solid #555b66;border-radius:4px;"
            "padding:6px;min-width:120px;"
        )
        row_play.addWidget(self.play_bind_label)

        self.play_rebind_button = QPushButton("重新绑定")
        self.play_rebind_button.clicked.connect(lambda: self.start_bind("play"))
        row_play.addWidget(self.play_rebind_button)
        row_play.addStretch()
        layout.addLayout(row_play)

        row_fwd = QHBoxLayout()
        row_fwd.addWidget(QLabel("快进："))

        self.fwd_bind_label = QLabel(format_key_display(self.bind_forward))
        self.fwd_bind_label.setStyleSheet(
            "background:#30343c;border:1px solid #555b66;border-radius:4px;"
            "padding:6px;min-width:120px;"
        )
        row_fwd.addWidget(self.fwd_bind_label)

        self.fwd_rebind_button = QPushButton("重新绑定")
        self.fwd_rebind_button.clicked.connect(lambda: self.start_bind("forward"))
        row_fwd.addWidget(self.fwd_rebind_button)
        row_fwd.addStretch()
        layout.addLayout(row_fwd)

        options_row = QHBoxLayout()

        self.topmost_check = QCheckBox("窗口置顶")
        self.topmost_check.setChecked(True)
        self.topmost_check.toggled.connect(self.set_topmost)
        options_row.addWidget(self.topmost_check)

        self.passthrough_check = QCheckBox("控制器鼠标穿透（F8）")
        self.passthrough_check.toggled.connect(self.set_passthrough_from_checkbox)
        options_row.addWidget(self.passthrough_check)

        options_row.addStretch()
        layout.addLayout(options_row)

        hint = QLabel("快捷键：F8 切换穿透，F9 显示 / 隐藏控制器")
        layout.addWidget(hint)

        layout.addWidget(QLabel("任务进度记录："))

        self.progress_edit = QPlainTextEdit()
        self.progress_edit.setPlaceholderText(
            "在这里记录攻略进度，例如：\n"
            "第三章 已打完 Boss 1\n"
            "下个宝箱在钟离神像附近"
        )
        saved_progress = SETTINGS.value("progress_text", "")
        if saved_progress:
            self.progress_edit.setPlainText(saved_progress)
        layout.addWidget(self.progress_edit, 1)

        progress_btn_row = QHBoxLayout()
        progress_btn_row.addStretch()

        save_progress_button = QPushButton("保存进度")
        save_progress_button.clicked.connect(self.save_progress)
        progress_btn_row.addWidget(save_progress_button)

        layout.addLayout(progress_btn_row)

        self.status_label = QLabel("状态：请选择或检查 Chrome 路径。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.setStyleSheet("""
            QWidget {
                background-color: #20232a;
                color: #f2f2f2;
                font-size: 12px;
            }
            QPushButton {
                background-color: #343943;
                border: 1px solid #555b66;
                border-radius: 5px;
                padding: 7px;
            }
            QPushButton:hover {
                background-color: #454c58;
            }
            QPushButton:disabled {
                background-color: #2a2d33;
                color: #777;
            }
            QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
                background-color: #30343c;
                border: 1px solid #555b66;
                border-radius: 4px;
                padding: 6px;
            }
            QCheckBox {
                spacing: 6px;
            }
        """)

    # -----------------------------------------------------------------
    # Chrome 路径管理
    # -----------------------------------------------------------------

    def save_chrome_path(self):
        path = self.chrome_path_input.text().strip().strip('"')
        self.chrome_path_input.setText(path)
        SETTINGS.setValue("chrome_path", path)
        SETTINGS.sync()

    def browse_chrome(self):
        current = self.chrome_path_input.text().strip().strip('"')

        if validate_chrome_path(current):
            start_dir = os.path.dirname(current)
        else:
            start_dir = os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application")
            if not os.path.isdir(start_dir):
                start_dir = os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application")
            if not os.path.isdir(start_dir):
                start_dir = str(Path.home())

        path, _ = QFileDialog.getOpenFileName(
            self,
            "请选择 Google Chrome 的 chrome.exe",
            start_dir,
            "Chrome 程序 (chrome.exe);;所有文件 (*.*)",
        )

        if not path:
            self.set_status("已取消选择浏览器。")
            return

        if not validate_chrome_path(path):
            QMessageBox.warning(self, "所选文件无效", "请选择名为 chrome.exe 的浏览器程序。")
            return

        self.chrome_path_input.setText(path)
        self.save_chrome_path()
        self.set_status(f"已保存 Chrome 路径：{path}")

    def auto_find_chrome(self):
        candidates = find_chrome_candidates()
        if not candidates:
            QMessageBox.information(
                self,
                "未自动找到 Chrome",
                "没有在常见目录找到 Chrome。\n\n"
                "请点击「选择浏览器」，手动选择 chrome.exe。",
            )
            self.set_status("自动查找未找到 Chrome，请手动选择。")
            return

        path = candidates[0]
        self.chrome_path_input.setText(path)
        self.save_chrome_path()
        self.set_status(f"自动找到 Chrome：{path}")

    def test_chrome_path(self):
        path = self.chrome_path_input.text().strip().strip('"')

        if not validate_chrome_path(path):
            QMessageBox.warning(
                self,
                "浏览器路径无效",
                "当前路径无效。\n\n请点击「选择浏览器」，找到真正的 chrome.exe。",
            )
            return

        self.chrome_path_input.setText(path)
        self.save_chrome_path()
        self.set_status(f"路径有效：{path}")
        QMessageBox.information(
            self, "检查通过",
            "已找到 chrome.exe！\n\n现在可以输入攻略网页地址并打开。",
        )

    def get_chrome_path(self):
        path = self.chrome_path_input.text().strip().strip('"')
        if validate_chrome_path(path):
            self.save_chrome_path()
            return path

        candidates = find_chrome_candidates()
        if candidates:
            path = candidates[0]
            self.chrome_path_input.setText(path)
            self.save_chrome_path()
            return path

        return None

    # -----------------------------------------------------------------
    # 系统托盘
    # -----------------------------------------------------------------

    def create_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip(APP_TITLE)
        self.tray.setIcon(load_app_icon())

        menu = QMenu()

        show_action = QAction("显示控制器", self)
        show_action.triggered.connect(self.show_normal)
        menu.addAction(show_action)

        hide_action = QAction("隐藏控制器", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)

        menu.addSeparator()

        exit_action = QAction("退出程序", self)
        exit_action.triggered.connect(self.quit_app)
        menu.addAction(exit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)

        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_normal()

    # -----------------------------------------------------------------
    # 窗口样式 / 置顶 / 穿透
    # -----------------------------------------------------------------

    def apply_window_style(self):
        if sys.platform != "win32":
            return

        import ctypes

        hwnd = int(self.winId())
        user32 = ctypes.windll.user32

        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000

        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

        if self.passthrough:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT

        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def set_passthrough(self, enabled):
        self.passthrough = enabled
        self.apply_window_style()

        self.passthrough_check.blockSignals(True)
        self.passthrough_check.setChecked(enabled)
        self.passthrough_check.blockSignals(False)

        if enabled:
            self.set_status("鼠标穿透已开启。按 F8 可以恢复交互。")
        else:
            self.set_status("鼠标交互已恢复。")

    def set_passthrough_from_checkbox(self, enabled):
        self.set_passthrough(enabled)

    def toggle_passthrough(self):
        self.set_passthrough(not self.passthrough)

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_normal()

    def show_normal(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def set_topmost(self, enabled):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        self.show()
        if enabled:
            self.raise_()

    # -----------------------------------------------------------------
    # 全局输入监听 + 看门狗
    # -----------------------------------------------------------------

    def _start_listeners(self):
        self.mouse_listener = mouse.Listener(on_click=self.on_global_mouse_click)
        self.mouse_listener.daemon = True
        self.mouse_listener.start()

        self.keyboard_listener = keyboard.Listener(on_press=self.on_global_key)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()

    def _start_watchdog(self):
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self._check_listeners)
        self._watchdog.start(5000)

    def _check_listeners(self):
        try:
            if not self.mouse_listener.is_alive():
                try:
                    self.mouse_listener.stop()
                except Exception:
                    pass
                self.mouse_listener = mouse.Listener(on_click=self.on_global_mouse_click)
                self.mouse_listener.daemon = True
                self.mouse_listener.start()
        except Exception:
            pass

        try:
            if not self.keyboard_listener.is_alive():
                try:
                    self.keyboard_listener.stop()
                except Exception:
                    pass
                self.keyboard_listener = keyboard.Listener(on_press=self.on_global_key)
                self.keyboard_listener.daemon = True
                self.keyboard_listener.start()
        except Exception:
            pass

    # -----------------------------------------------------------------
    # 按键匹配
    # -----------------------------------------------------------------

    def _key_matches_binding(self, key, binding):
        if not binding:
            return False
        try:
            if binding.startswith("Mouse "):
                return False

            if isinstance(key, keyboard.Key):
                name = getattr(key, "name", None)
                if not name:
                    return False
                return name.upper() == binding.upper()

            if isinstance(key, keyboard.KeyCode):
                if binding.startswith("VK"):
                    try:
                        return int(binding[2:]) == getattr(key, "vk", None)
                    except Exception:
                        return False

                ch = key.char
                if not ch:
                    return False
                return ch.upper() == binding.upper()

            return False
        except Exception:
            return False

    def _mouse_matches_binding(self, button, binding):
        if not binding or not binding.startswith("Mouse "):
            return False
        try:
            name = getattr(button, "name", None)
            if name is None:
                name = str(button)
            target = binding[6:]
            return name.lower() == target.lower()
        except Exception:
            return False

    def on_global_mouse_click(self, x, y, button, pressed):
        try:
            if not pressed:
                return

            if self._binding_action is not None:
                result = key_to_string(button)
                if result is None:
                    return
                self.signals.bind_captured.emit(result)
                return

            now = time.time()

            if self._mouse_matches_binding(button, self.bind_play):
                if now - self._last_play_time >= DEBOUNCE_MOUSE:
                    self._last_play_time = now
                    self.signals.toggle_play.emit()
                    return

            if self._mouse_matches_binding(button, self.bind_forward):
                if now - self._last_forward_time >= DEBOUNCE_MOUSE:
                    self._last_forward_time = now
                    self.signals.toggle_forward.emit()
                    return
        except Exception:
            pass

    def on_global_key(self, key):
        try:
            if self._binding_action is not None:
                try:
                    if key == keyboard.Key.esc:
                        self.signals.bind_captured.emit("")
                        return
                except Exception:
                    pass

                result = key_to_string(key)
                if result is None:
                    return
                self.signals.bind_captured.emit(result)
                return

            now = time.time()

            if self._key_matches_binding(key, self.bind_play):
                if now - self._last_play_time >= DEBOUNCE_HOTKEY:
                    self._last_play_time = now
                    self.signals.toggle_play.emit()
                    return

            if self._key_matches_binding(key, self.bind_forward):
                if now - self._last_forward_time >= DEBOUNCE_HOTKEY:
                    self._last_forward_time = now
                    self.signals.toggle_forward.emit()
                    return

            if key == keyboard.Key.f8:
                if now - self._last_f8_time >= DEBOUNCE_HOTKEY:
                    self._last_f8_time = now
                    self.signals.toggle_passthrough.emit()
                return

            if key == keyboard.Key.f9:
                if now - self._last_f9_time >= DEBOUNCE_HOTKEY:
                    self._last_f9_time = now
                    self.signals.toggle_visible.emit()
                return
        except Exception:
            pass

    # -----------------------------------------------------------------
    # 绑定 UI 交互
    # -----------------------------------------------------------------

    def start_bind(self, action):
        if self._binding_action is not None:
            return

        self._binding_action = action

        if action == "play":
            self.play_rebind_button.setText("请按键...")
            self.play_rebind_button.setEnabled(False)
            self.play_bind_label.setText("等待输入（按 Esc 取消）")
        else:
            self.fwd_rebind_button.setText("请按键...")
            self.fwd_rebind_button.setEnabled(False)
            self.fwd_bind_label.setText("等待输入（按 Esc 取消）")

        self.set_status("请按下你想绑定的按键（Esc 取消）。")

    def _on_bind_captured(self, result):
        action = self._binding_action
        self._binding_action = None

        if action == "play":
            self.play_rebind_button.setText("重新绑定")
            self.play_rebind_button.setEnabled(True)
        elif action == "forward":
            self.fwd_rebind_button.setText("重新绑定")
            self.fwd_rebind_button.setEnabled(True)

        if not result:
            if action == "play":
                self.play_bind_label.setText(format_key_display(self.bind_play))
            elif action == "forward":
                self.fwd_bind_label.setText(format_key_display(self.bind_forward))
            self.set_status("已取消绑定。")
            return

        other = self.bind_forward if action == "play" else self.bind_play
        if result == other:
            QMessageBox.warning(
                self,
                "按键冲突",
                f"该按键已被另一个动作使用：{format_key_display(result)}\n\n"
                "请换一个按键。",
            )
            if action == "play":
                self.play_bind_label.setText(format_key_display(self.bind_play))
            else:
                self.fwd_bind_label.setText(format_key_display(self.bind_forward))
            self.set_status("绑定失败：按键冲突。")
            return

        if action == "play":
            self.bind_play = result
            SETTINGS.setValue("bind_play", result)
            self.play_bind_label.setText(format_key_display(result))
        elif action == "forward":
            self.bind_forward = result
            SETTINGS.setValue("bind_forward", result)
            self.fwd_bind_label.setText(format_key_display(result))

        SETTINGS.sync()
        self.set_status(
            f"已绑定 {('播放 / 暂停' if action == 'play' else '快进')} → "
            f"{format_key_display(result)}"
        )

    # -----------------------------------------------------------------
    # 快进秒数
    # -----------------------------------------------------------------

    def change_forward_seconds(self, value):
        self.forward_seconds = int(value)
        SETTINGS.setValue("forward_seconds", self.forward_seconds)
        SETTINGS.sync()
        self.set_status(f"快进秒数已设为 {self.forward_seconds} 秒。")

    # -----------------------------------------------------------------
    # 启动 Chrome
    # -----------------------------------------------------------------

    def open_custom_url(self):
        url = self.url_input.text().strip()

        if not url:
            self.set_status("请先输入网址。")
            return

        if not url.startswith(("https://", "http://")):
            url = "https://" + url

        self.open_site(url)

    def open_site(self, url):
        chrome = self.get_chrome_path()

        if not chrome:
            QMessageBox.warning(
                self,
                "找不到 Chrome",
                "软件没有找到有效的 Chrome 路径。\n\n"
                "请点击「选择浏览器」，手动选择 chrome.exe。",
            )
            return

        self.target_url = url
        self.url_input.setText(url)

        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)

            command = [
                chrome,
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={PROFILE_DIR}",
            ] + CHROME_EXTRA_ARGS + [
                "--new-window",
                url,
            ]

            self.chrome_process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            self.set_status(
                "已请求启动 Chrome。等待网页加载后，"
                "可使用播放 / 暂停按钮或鼠标侧键。"
            )

        except Exception as exc:
            self.set_status(f"启动 Chrome 失败：{exc}")

    def _kill_chrome(self):
        proc = self.chrome_process
        if proc is None:
            return

        if proc.poll() is not None:
            self.chrome_process = None
            return

        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

        self.chrome_process = None

    # -----------------------------------------------------------------
    # 目标页 & CDP 调用
    # -----------------------------------------------------------------

    def find_target(self):
        pages = [
            item for item in fetch_debug_targets()
            if item.get("type") == "page"
            and item.get("webSocketDebuggerUrl")
        ]

        if not pages:
            raise RuntimeError("没有找到可控制的 Chrome 网页。")

        filtered = []
        for page in pages:
            url = (page.get("url") or "").lower()
            if url.startswith(("chrome://", "devtools://", "chrome-extension://", "edge://")):
                continue
            filtered.append(page)

        if filtered:
            pages = filtered

        wanted_host = (urlparse(self.target_url).hostname or "").lower()

        if wanted_host:
            for page in reversed(pages):
                page_host = (urlparse(page.get("url", "")).hostname or "").lower()
                if not page_host:
                    continue
                if (
                    page_host == wanted_host
                    or page_host.endswith("." + wanted_host)
                    or wanted_host.endswith("." + page_host)
                ):
                    return page

        return pages[-1]

    def _get_session(self, ws_url):
        with self._cdp_lock:
            session = self._cdp_sessions.get(ws_url)
            if session is None or not session.running:
                if session is not None:
                    session.close()
                session = CDPSession(ws_url)
                self._cdp_sessions[ws_url] = session
            return session

    def _close_all_sessions(self):
        with self._cdp_lock:
            sessions = list(self._cdp_sessions.values())
            self._cdp_sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass

    def _run_cdp_worker(self, expression, success_message, formatter):
        def worker():
            try:
                targets = fetch_debug_targets()
                pages = [
                    t for t in targets
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
                ]

                pages = [
                    p for p in pages
                    if not (p.get("url") or "").lower().startswith((
                        "chrome://", "devtools://",
                        "chrome-extension://", "edge://",
                    ))
                ]

                if not pages:
                    raise RuntimeError("没有找到可控制的 Chrome 网页。")

                try:
                    primary = self.find_target()
                except Exception:
                    primary = None

                ordered = []
                if primary is not None:
                    ordered.append(primary)
                for p in pages:
                    if p is primary:
                        continue
                    ordered.append(p)

                last_result = None
                last_error = None

                for target in ordered:
                    ws_url = target.get("webSocketDebuggerUrl")
                    if not ws_url:
                        continue

                    session = self._get_session(ws_url)
                    try:
                        result = session.evaluate_all_frames(expression)
                    except Exception as exc:
                        last_error = exc
                        result = None

                    if isinstance(result, dict) and result.get("found"):
                        last_result = result
                        break

                if last_result is None:
                    if last_error is not None:
                        self.signals.status.emit(
                            f"{success_message}：失败 {type(last_error).__name__}: "
                            f"{str(last_error)[:160]}"
                        )
                    else:
                        self.signals.status.emit(
                            f"{success_message}：没有找到 HTML5 视频元素。"
                        )
                    return

                self.signals.status.emit(f"{success_message}：{formatter(last_result)}")

            except Exception as exc:
                self.signals.status.emit(
                    f"控制失败：{type(exc).__name__}: {str(exc)[:160]}"
                )

        threading.Thread(target=worker, daemon=True).start()

    def play_pause(self):
        def fmt(r):
            action = r.get("action")
            total = r.get("total", 1)
            if action == "play":
                return f"已请求播放（共 {total} 个视频）"
            if action == "pause":
                return f"已请求暂停（共 {total} 个视频）"
            if action == "error":
                return f"操作失败：{r.get('detail')}"
            return "命令已发送"

        self._run_cdp_worker(PLAY_PAUSE_JS, "播放 / 暂停命令", fmt)

    def forward(self):
        seconds = int(self.forward_seconds)
        js = FORWARD_JS_TEMPLATE.replace("__SECONDS__", str(seconds))

        def fmt(r):
            old_t = r.get("oldTime", 0)
            new_t = r.get("newTime", 0)
            total = r.get("total", 1)

            def t(x):
                try:
                    x = float(x)
                except Exception:
                    return "?"
                m = int(x // 60)
                s = int(x % 60)
                return f"{m}:{s:02d}"

            return f"已快进 {seconds} 秒（{t(old_t)} → {t(new_t)}，共 {total} 个视频）"

        self._run_cdp_worker(js, "快进命令", fmt)

    def refresh_status(self):
        def worker():
            try:
                pages = fetch_debug_targets()
                count = sum(1 for p in pages if p.get("type") == "page")
                self.signals.status.emit(
                    f"Chrome 调试连接正常，发现 {count} 个网页标签页。"
                )
            except Exception as exc:
                self.signals.status.emit(f"连接失败：{exc}。请先通过本软件打开网页。")

        threading.Thread(target=worker, daemon=True).start()

    # -----------------------------------------------------------------
    # 任务进度
    # -----------------------------------------------------------------

    def save_progress(self):
        text = self.progress_edit.toPlainText()
        SETTINGS.setValue("progress_text", text)
        SETTINGS.sync()
        self.set_status("任务进度已保存。")

    def _save_progress_silent(self):
        try:
            SETTINGS.setValue("progress_text", self.progress_edit.toPlainText())
            SETTINGS.sync()
        except Exception:
            pass

    # -----------------------------------------------------------------
    # 状态、关闭、退出
    # -----------------------------------------------------------------

    def set_status(self, message):
        self.status_label.setText("状态：" + message)

    def quit_app(self):
        try:
            self.mouse_listener.stop()
        except Exception:
            pass

        try:
            self.keyboard_listener.stop()
        except Exception:
            pass

        try:
            self._watchdog.stop()
        except Exception:
            pass

        self._close_all_sessions()
        self._kill_chrome()
        self._save_progress_silent()

        try:
            self.tray.hide()
        except Exception:
            pass

        QApplication.quit()

    def closeEvent(self, event):
        self.quit_app()


# =====================================================================
# 入口
# =====================================================================

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)

    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = Overlay()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
