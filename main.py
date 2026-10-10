import tkinter as tk
import random
import time
import threading
import ctypes
import sys
import os
import json
import winsound
from pynput import keyboard as pkb
from pynput import mouse as pms
from pynput.keyboard import Key, Controller as KeyboardController
from pynput.mouse import Button, Controller as MouseController

# 全局状态
running = False
hotkey = "<f8>"
mouse_side_key = None
keyboard_controller = KeyboardController()
mouse_controller = MouseController()
current_mouse_pos = (0, 0)

CONFIG_FILE = "config.json"

def get_resource_path(relative_path):
    """获取资源文件的绝对路径（兼容PyInstaller打包后的exe）"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def play_start_sound():
    try:
        winsound.Beep(600, 100)
        time.sleep(0.05)
        winsound.Beep(1200, 150)
    except:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)

def play_stop_sound():
    try:
        winsound.Beep(1200, 100)
        time.sleep(0.05)
        winsound.Beep(600, 150)
    except:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)

def load_config():
    global hotkey, mouse_side_key
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if 'hotkey' in config and config['hotkey']:
                    hotkey = config['hotkey']
                if 'mouse_side' in config and config['mouse_side']:
                    if config['mouse_side'] == 'x1':
                        mouse_side_key = Button.x1
                    elif config['mouse_side'] == 'x2':
                        mouse_side_key = Button.x2
    except:
        pass

def save_config():
    global hotkey, mouse_side_key
    try:
        config = {
            'hotkey': hotkey,
            'mouse_side': 'x1' if mouse_side_key == Button.x1 else 'x2' if mouse_side_key == Button.x2 else None
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f)
    except:
        pass

class AutoClickerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("堂主代练")
        self.root.geometry("420x520")
        self.root.resizable(False, False)
        self.root.attributes('-topmost', True)
        
        # 设置窗口图标
        try:
            icon_path = get_resource_path("icon.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception as e:
            print(f"图标加载失败: {e}")
        
        # 状态显示
        self.status_label = tk.Label(root, text="状态: 已停止", font=("微软雅黑", 14, "bold"), fg="red")
        self.status_label.pack(pady=10)
        
        # 按键选项
        options_frame = tk.LabelFrame(root, text="按键选项", font=("微软雅黑", 10), padx=10, pady=5)
        options_frame.pack(fill="x", padx=20, pady=5)
        
        self.space_var = tk.BooleanVar(value=True)
        self.f_var = tk.BooleanVar(value=True)
        self.click_var = tk.BooleanVar(value=True)
        
        tk.Checkbutton(options_frame, text="空格键", variable=self.space_var, font=("微软雅黑", 10)).pack(anchor="w")
        tk.Checkbutton(options_frame, text="F键", variable=self.f_var, font=("微软雅黑", 10)).pack(anchor="w")
        tk.Checkbutton(options_frame, text="鼠标左键", variable=self.click_var, font=("微软雅黑", 10)).pack(anchor="w")
        
        # 鼠标偏移设置
        offset_frame = tk.LabelFrame(root, text="鼠标偏移设置", font=("微软雅黑", 10), padx=10, pady=5)
        offset_frame.pack(fill="x", padx=20, pady=5)
        
        tk.Label(offset_frame, text="最小偏移:", font=("微软雅黑", 9)).grid(row=0, column=0, sticky="w", pady=2)
        self.min_offset = tk.Entry(offset_frame, width=10)
        self.min_offset.insert(0, "3")
        self.min_offset.grid(row=0, column=1, padx=5)
        tk.Label(offset_frame, text="像素", font=("微软雅黑", 9)).grid(row=0, column=2, sticky="w")
        
        tk.Label(offset_frame, text="最大偏移:", font=("微软雅黑", 9)).grid(row=1, column=0, sticky="w", pady=2)
        self.max_offset = tk.Entry(offset_frame, width=10)
        self.max_offset.insert(0, "14")
        self.max_offset.grid(row=1, column=1, padx=5)
        tk.Label(offset_frame, text="像素", font=("微软雅黑", 9)).grid(row=1, column=2, sticky="w")
        
        # 暂停时间设置
        pause_frame = tk.LabelFrame(root, text="自动暂停设置", font=("微软雅黑", 10), padx=10, pady=5)
        pause_frame.pack(fill="x", padx=20, pady=5)
        
        tk.Label(pause_frame, text="运行时长:", font=("微软雅黑", 9)).grid(row=0, column=0, sticky="w", pady=2)
        self.run_min = tk.Entry(pause_frame, width=8)
        self.run_min.insert(0, "30")
        self.run_min.grid(row=0, column=1, padx=2)
        tk.Label(pause_frame, text="到", font=("微软雅黑", 9)).grid(row=0, column=2)
        self.run_max = tk.Entry(pause_frame, width=8)
        self.run_max.insert(0, "40")
        self.run_max.grid(row=0, column=3, padx=2)
        tk.Label(pause_frame, text="秒", font=("微软雅黑", 9)).grid(row=0, column=4, sticky="w")
        
        tk.Label(pause_frame, text="暂停时长:", font=("微软雅黑", 9)).grid(row=1, column=0, sticky="w", pady=2)
        self.pause_min = tk.Entry(pause_frame, width=8)
        self.pause_min.insert(0, "2")
        self.pause_min.grid(row=1, column=1, padx=2)
        tk.Label(pause_frame, text="到", font=("微软雅黑", 9)).grid(row=1, column=2)
        self.pause_max = tk.Entry(pause_frame, width=8)
        self.pause_max.insert(0, "4")
        self.pause_max.grid(row=1, column=3, padx=2)
        tk.Label(pause_frame, text="秒", font=("微软雅黑", 9)).grid(row=1, column=4, sticky="w")
        
        # 热键设置
        hotkey_frame = tk.LabelFrame(root, text="热键设置", font=("微软雅黑", 10), padx=10, pady=5)
        hotkey_frame.pack(fill="x", padx=20, pady=5)
        
        tk.Label(hotkey_frame, text="键盘热键:", font=("微软雅黑", 9)).pack(side="left")
        self.hotkey_label = tk.Label(hotkey_frame, text=hotkey.upper(), font=("微软雅黑", 10, "bold"), fg="blue")
        self.hotkey_label.pack(side="left", padx=5)
        tk.Button(hotkey_frame, text="设置", command=self.set_hotkey, font=("微软雅黑", 9)).pack(side="right")
        
        # 鼠标侧键设置
        mouse_frame = tk.LabelFrame(root, text="鼠标侧键设置", font=("微软雅黑", 10), padx=10, pady=5)
        mouse_frame.pack(fill="x", padx=20, pady=5)
        
        if mouse_side_key == Button.x1:
            self.mouse_label = tk.Label(mouse_frame, text="已绑定: 侧键1", font=("微软雅黑", 9), fg="blue")
        elif mouse_side_key == Button.x2:
            self.mouse_label = tk.Label(mouse_frame, text="已绑定: 侧键2", font=("微软雅黑", 9), fg="blue")
        else:
            self.mouse_label = tk.Label(mouse_frame, text="未绑定", font=("微软雅黑", 9), fg="gray")
        self.mouse_label.pack(side="left")
        
        tk.Button(mouse_frame, text="侧键1", command=lambda: self.set_mouse_side("x1"), font=("微软雅黑", 9)).pack(side="right", padx=2)
        tk.Button(mouse_frame, text="侧键2", command=lambda: self.set_mouse_side("x2"), font=("微软雅黑", 9)).pack(side="right", padx=2)
        
        # 启动/停止按钮
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=15)
        
        self.start_btn = tk.Button(btn_frame, text="▶ 启动", command=self.start, font=("微软雅黑", 12, "bold"), 
                                    bg="#4CAF50", fg="white", width=10, height=1)
        self.start_btn.pack(side="left", padx=10)
        
        self.stop_btn = tk.Button(btn_frame, text="■ 停止", command=self.stop, font=("微软雅黑", 12, "bold"),
                                   bg="#f44336", fg="white", width=10, height=1, state="disabled")
        self.stop_btn.pack(side="left", padx=10)
        
        # 鼠标位置相关
        self.anchor_pos = None
        self.click_count = 0
        
        self.mouse_listener = None
        self.keyboard_listener = None
        self.setup_listeners()
    
    def get_random_offset(self):
        try:
            min_off = int(self.min_offset.get())
            max_off = int(self.max_offset.get())
            if min_off < 0:
                min_off = 0
            if max_off < min_off:
                max_off = min_off
        except:
            min_off = 3
            max_off = 14
        
        x_offset = random.randint(min_off, max_off)
        y_offset = random.randint(min_off, max_off)
        
        if random.random() < 0.5:
            x_offset = -x_offset
        if random.random() < 0.5:
            y_offset = -y_offset
        
        return x_offset, y_offset
    
    def get_pause_settings(self):
        try:
            run_min = float(self.run_min.get())
            run_max = float(self.run_max.get())
            pause_min = float(self.pause_min.get())
            pause_max = float(self.pause_max.get())
            
            if run_min < 1:
                run_min = 1
            if run_max < run_min:
                run_max = run_min
            if pause_min < 0.5:
                pause_min = 0.5
            if pause_max < pause_min:
                pause_max = pause_min
        except:
            run_min, run_max = 30, 40
            pause_min, pause_max = 2, 4
        
        return run_min, run_max, pause_min, pause_max
    
    def setup_listeners(self):
        global hotkey, mouse_side_key, current_mouse_pos
        
        if self.mouse_listener:
            self.mouse_listener.stop()
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        
        key_to_listen = None
        try:
            clean_hotkey = hotkey.replace('<', '').replace('>', '')
            if clean_hotkey.startswith('f') and len(clean_hotkey) > 1 and clean_hotkey[1:].isdigit():
                f_key = getattr(pkb.Key, clean_hotkey, None)
                if f_key:
                    key_to_listen = f_key
            elif hasattr(pkb.Key, clean_hotkey):
                key_to_listen = getattr(pkb.Key, clean_hotkey)
            elif len(clean_hotkey) == 1:
                key_to_listen = pkb.KeyCode.from_char(clean_hotkey.lower())
        except:
            key_to_listen = pkb.Key.f8
        
        def on_press(key):
            global running
            try:
                if key_to_listen and key == key_to_listen:
                    self.toggle()
            except:
                pass
        
        self.keyboard_listener = pkb.Listener(on_press=on_press)
        self.keyboard_listener.start()
        
        def on_move(x, y):
            global current_mouse_pos
            current_mouse_pos = (x, y)
        
        def on_click(x, y, button, pressed):
            global running, mouse_side_key
            if pressed and mouse_side_key and button == mouse_side_key:
                self.toggle()
        
        self.mouse_listener = pms.Listener(on_move=on_move, on_click=on_click)
        self.mouse_listener.start()
    
    def set_hotkey(self):
        global hotkey
        self.status_label.config(text="请按下新的热键...", fg="orange")
        self.root.update()
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        temp_listener = pkb.Listener(on_press=self._capture_hotkey)
        temp_listener.start()
        self.root.after(5000, lambda: self._timeout_hotkey(temp_listener))
    
    def _capture_hotkey(self, key):
        global hotkey
        try:
            if hasattr(key, 'name') and key.name:
                hotkey = f"<{key.name}>"
            elif hasattr(key, 'char') and key.char:
                hotkey = f"<{key.char}>"
            else:
                hotkey = f"<{key}>"
            self.hotkey_label.config(text=hotkey.upper())
            self.status_label.config(text=f"热键已设置: {hotkey.upper()}", fg="green")
            winsound.Beep(800, 100)
            save_config()
            self.root.after(100, self.setup_listeners)
            return False
        except:
            return True
    
    def _timeout_hotkey(self, listener):
        listener.stop()
        self.status_label.config(text="热键设置超时", fg="red")
        self.setup_listeners()
    
    def set_mouse_side(self, side):
        global mouse_side_key
        if side == "x1":
            mouse_side_key = Button.x1
            self.mouse_label.config(text="已绑定: 侧键1", fg="blue")
        else:
            mouse_side_key = Button.x2
            self.mouse_label.config(text="已绑定: 侧键2", fg="blue")
        self.status_label.config(text="鼠标侧键绑定成功！", fg="green")
        winsound.Beep(800, 100)
        save_config()
        self.setup_listeners()
    
    def toggle(self):
        if running:
            self.stop()
        else:
            self.start()
    
    def start(self):
        global running, current_mouse_pos
        if not running:
            running = True
            self.status_label.config(text="状态: 运行中", fg="green")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            
            self.anchor_pos = current_mouse_pos
            self.click_count = 0
            
            threading.Thread(target=play_start_sound, daemon=True).start()
            threading.Thread(target=self.worker, daemon=True).start()
    
    def stop(self):
        global running
        running = False
        self.status_label.config(text="状态: 已停止", fg="red")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        threading.Thread(target=play_stop_sound, daemon=True).start()
    
    def click_with_offset(self):
        global current_mouse_pos
        
        if self.anchor_pos:
            base_x, base_y = self.anchor_pos
        else:
            base_x, base_y = current_mouse_pos
        
        x_offset, y_offset = self.get_random_offset()
        
        new_x = base_x + x_offset
        new_y = base_y + y_offset
        
        mouse_controller.position = (new_x, new_y)
        time.sleep(0.01)
        mouse_controller.click(Button.left, 1)
        time.sleep(0.01)
        
        self.anchor_pos = (new_x, new_y)
        self.click_count += 1
        
        return new_x, new_y
    
    def worker(self):
        global running
        
        while running:
            run_min, run_max, pause_min, pause_max = self.get_pause_settings()
            run_duration = random.uniform(run_min, run_max)
            run_start_time = time.time()
            
            self.root.after(0, lambda: self.status_label.config(text="状态: 运行中", fg="green"))
            
            while running and (time.time() - run_start_time) < run_duration:
                wait_time = random.uniform(0.05, 1.0)
                time.sleep(wait_time)
                if not running:
                    break
                
                actions = []
                if self.space_var.get():
                    actions.append('space')
                if self.f_var.get():
                    actions.append('f')
                if self.click_var.get():
                    actions.append('left_click')
                
                if not actions:
                    continue
                
                num_actions = random.randint(1, len(actions))
                selected_actions = random.sample(actions, num_actions)
                
                try:
                    if 'left_click' in selected_actions:
                        self.click_with_offset()
                        selected_actions.remove('left_click')
                        if not running:
                            break
                    
                    for action in selected_actions:
                        if action == 'space':
                            keyboard_controller.press(Key.space)
                        elif action == 'f':
                            keyboard_controller.press('f')
                    
                    time.sleep(random.uniform(0.02, 0.08))
                    
                    for action in selected_actions:
                        if action == 'space':
                            keyboard_controller.release(Key.space)
                        elif action == 'f':
                            keyboard_controller.release('f')
                    
                except Exception as e:
                    print(f"执行动作时出错: {e}")
            
            if not running:
                break
            
            pause_duration = random.uniform(pause_min, pause_max)
            self.root.after(0, lambda: self.status_label.config(text=f"状态: 暂停中 ({pause_duration:.1f}秒)", fg="orange"))
            
            pause_start_time = time.time()
            while running and (time.time() - pause_start_time) < pause_duration:
                time.sleep(0.1)
            
            if not running:
                break
            
            self.root.after(0, lambda: self.status_label.config(text="状态: 运行中", fg="green"))
    
    def on_closing(self):
        global running
        running = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        self.root.destroy()
        os._exit(0)

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
    sys.exit()

if __name__ == "__main__":
    if not is_admin():
        run_as_admin()
    
    load_config()
    
    root = tk.Tk()
    app = AutoClickerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()