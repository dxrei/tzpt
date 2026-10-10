# 堂主跑腿 · GameGuide Overlay

> 一个浮在游戏画面上的攻略视频控制器 —— 用鼠标侧键就能暂停 / 播放 B 站、抖音里的攻略视频，不用切窗口。

<p align="center">
  <img src="docs/screenshot-main.png" width="480" alt="主界面">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-blue" alt="Platform">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/version-v1.0-orange" alt="Version">
</p>

---

## 这是什么

打游戏的时候看攻略视频，最烦的就是要不断切窗口 —— 视频一暂停，鼠标一晃，游戏里就死了。

**堂主跑腿** 用一个悬浮小窗 + 鼠标侧键，把这件事变成：

- 开着游戏，视频放在另一个显示器 / 半屏。
- 想看细节 → **按一下鼠标侧键**，视频暂停。
- 看完了 → **再按一下**，继续播放。
- 全程不切窗口、不动鼠标。

它是怎么做到的？用你自己电脑上的 Chrome 打开攻略网页，然后通过 Chrome DevTools Protocol 远程控制网页里的 `<video>` 元素。**没有任何外挂、没有注入游戏进程**，只是"帮你点点网页"。

---

## 功能

### 核心

- 🎬 **全局鼠标侧键控制**：Mouse 4 / Mouse 5 一键播放 / 暂停
- 🌐 **任意网页视频**：B 站、抖音、YouTube、HTML5 视频网站通吃
- 🪟 **窗口置顶 + 鼠标穿透**：不挡游戏、不影响鼠标操作
- 🔥 **F8 / F9 快捷键**：一键切穿透、一键显隐窗口
- 💾 **设置自动记忆**：Chrome 路径、侧键选择下次启动自动恢复
- 🖥️ **系统托盘**：常驻后台，随时调出

### 稳定性

- 视频被切到小窗 / 画中画时仍然识别（遍历 Shadow DOM + iframe + PiP）
- CDP 长连接复用，不会因按键太快而丢命令
- pynput 监听器看门狗，长时间运行不会"按键失效"
- 退出程序时自动关掉由它启动的浏览器

---

## 快速开始

### 方式一：下载 exe（推荐普通用户）

1. 到 [Releases](../../releases) 页面下载最新的 `堂主跑腿.exe`
2. 双击运行
3. 首次启动会自动找 Chrome，找到后点「打开输入网址」，输入 B 站 / 抖音链接
4. 视频加载完后，按鼠标侧键就能控制了

> ⚠️ **不要**用日常用的 Chrome 打开网页。本软件需要专用调试端口，必须通过它自己的「打开输入网址」按钮启动浏览器。

### 方式二：从源码运行（开发者）

```bash
git clone https://github.com/yourname/game-guide-overlay.git
cd game-guide-overlay
pip install -r requirements.txt
python main.py
