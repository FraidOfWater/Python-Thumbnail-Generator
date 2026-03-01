import os
import json
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk
from threading import Thread
from thumbgen import ThumbManager, walk

def select_folder():
    prompt = filedialog.askdirectory()
    if prompt:
        folder_path_var.set(prompt)

def open_source():
    path = folder_path_var.get()
    if os.path.exists(path):
        subprocess.Popen(f'explorer "{os.path.normpath(path)}"')

def open_dest():
    os.makedirs(data_dir, exist_ok=True)
    subprocess.Popen(f'explorer "{os.path.normpath(data_dir)}"')

length = 0
def generate_thumbnails():
    os.makedirs(data_dir, exist_ok=True)
    if not folder_path_var.get(): return
    
    settings = {
        "size": int(size_var.get()),
        "ext": ext_var.get(),
        "quality": int(quality_var.get()),
        "lossless": lossless_var.get(),
        "mode": mode_var.get(),
        "naming": naming_var.get(),
        "structure": structure_var.get()
    }
    
    def run():
        global length
        imagelist = [(x) for x in walk(folder_path_var.get())]
        length = len(imagelist)
        generated_so_far.set(f"0/{length}")
        Thumbnail_generator.generate(imagelist, settings)
        root.after(0, check)

    Thread(target=run, daemon=True).start()
    

last = []
def check():
    global last
    new = len(os.listdir(data_dir))
    last.append(new)
    if len(last) == 4: last.pop(0)
    generated_so_far.set(f"{new}/{length}")
    if len(last) == 3 and last[0] == last[-1]: return
    root.after(500, check)

def on_close():
    save_data = {
        "preferences": {
            "path": folder_path_var.get(),
            "thumbnailsize": size_var.get(),
            "extension": ext_var.get(),
            "quality": quality_var.get(),
            "lossless": lossless_var.get(),
            "resize_mode": mode_var.get(),
            "naming": naming_var.get(),
            "structure": structure_var.get()
        }
    }
    with open(prefs_path, "w") as f:
        json.dump(save_data, f, indent=4)
    root.destroy()

# GUI Setup
root = tk.Tk()
root.title("Thumbnail Generator")
root.config(bg="#202041")
root.protocol("WM_DELETE_WINDOW", on_close)

root.columnconfigure(0, weight=1)

script_dir = os.path.dirname(os.path.abspath(__file__))
data_dir = os.path.join(script_dir, "data")
prefs_path = os.path.join(script_dir, "prefs.json")
os.makedirs(data_dir, exist_ok=True)

style = ttk.Style()
style.theme_use('clam')
style.configure("TLabel", background="#202041", foreground="white")
style.configure("TButton", background="#404060", foreground="white")
style.configure("TCheckbutton", background="#202041", foreground="white")

# Variables
folder_path_var = tk.StringVar()
size_var = tk.StringVar(value="640")
ext_var = tk.StringVar(value=".webp")
quality_var = tk.IntVar(value=90)
lossless_var = tk.BooleanVar(value=False)
mode_var = tk.StringVar(value="Keep Aspect Ratio")
naming_var = tk.StringVar(value="Original Name")
structure_var = tk.StringVar(value="Preserve Structure")
generated_so_far = tk.StringVar(value="0/0")

# 1. Folder Selection Frame
frame_top = tk.Frame(root, bg="#202041")
frame_top.grid(row=0, column=0, sticky="ew", padx=15, pady=5)
frame_top.columnconfigure(0, weight=2)
frame_top.columnconfigure(1, weight=1)

tk.Label(frame_top, text="Input Folder:", bg="#202041", fg="white").grid(row=0, column=0, sticky="w")
tk.Entry(frame_top, textvariable=folder_path_var, width=37).grid(row=1, column=0, pady=5, sticky="w")
tk.Button(frame_top, text="Browse", command=select_folder, bg="#404060", fg="white").grid(row=1, column=1, sticky="ew")

# 2. Image Settings (Size, Quality, Ext)
frame_settings = tk.LabelFrame(root, text="Image Settings", bg="#202041", fg="white", padx=10, pady=10)
frame_settings.grid(row=2, column=0, sticky="ew", padx=10, pady=5)

tk.Label(frame_settings, text="Size (px):", bg="#202041", fg="white").grid(row=0, column=0, sticky="w")
tk.Entry(frame_settings, textvariable=size_var, width=10).grid(row=0, column=1, sticky="w", padx=5)

tk.Label(frame_settings, text="Extension:", bg="#202041", fg="white").grid(row=0, column=2, sticky="w")
ttk.Combobox(frame_settings, textvariable=ext_var, values=[".webp", ".jpeg", ".png", ".bmp"], width=8).grid(row=0, column=3, sticky="w", padx=5)

tk.Label(frame_settings, text="Quality:", bg="#202041", fg="white").grid(row=1, column=0, sticky="w", pady=10)

quality_slider = tk.Scale(frame_settings, variable=quality_var, from_=1, to=100, orient="horizontal", bg="#202041", fg="white", highlightthickness=0, showvalue=False)
quality_slider.grid(row=1, column=1, columnspan=2, sticky="ew")

frame_settings1 = tk.LabelFrame(frame_settings, bg="#202041", fg="white")
frame_settings1.grid(row=1, column=3, sticky="w")

tk.Label(frame_settings1, textvariable=quality_var, bg="#202041", fg="white", width=5, anchor="center").grid(row=0, column=0, sticky="ew")

tk.Checkbutton(frame_settings, text="Lossless (WebP/PNG only)", variable=lossless_var, command=lambda: (quality_var.set(100), quality_slider.config(state="disabled")) if lossless_var.get() else quality_slider.config(state="normal"), bg="#202041", fg="white", selectcolor="#404060").grid(row=2, column=0, columnspan=4, sticky="w")

# 3. Processing Logic
frame_logic = tk.LabelFrame(root, text="Processing Logic", bg="#202041", fg="white", padx=10, pady=10)
frame_logic.grid(row=3, column=0, sticky="ew", padx=10, pady=5)

tk.Label(frame_logic, text="Resize Mode:", bg="#202041", fg="white").grid(row=0, column=0, sticky="w", pady=2)

ttk.OptionMenu(frame_logic, mode_var, "Keep Aspect Ratio", "Keep Aspect Ratio", "Pad to Dimensions", "Crop to Dimensions", "Stretch to Dimensions").grid(row=0, column=1, sticky="ew", padx=5)

tk.Label(frame_logic, text="Naming:", bg="#202041", fg="white").grid(row=1, column=0, sticky="w", pady=2)
ttk.OptionMenu(frame_logic, naming_var, "Original Name", "Original Name", "Hashed Name").grid(row=1, column=1, sticky="ew", padx=5)

tk.Label(frame_logic, text="Structure:", bg="#202041", fg="white").grid(row=2, column=0, sticky="w", pady=2)
ttk.OptionMenu(frame_logic, structure_var, "Preserve Structure", "Preserve Structure", "Flatten").grid(row=2, column=1, sticky="ew", padx=5)

# 4. Action Area (Buttons and Status)
frame_actions = tk.Frame(root, bg="#202041")
frame_actions.grid(row=1, column=0, sticky="ew", padx=15)
frame_actions.columnconfigure(0, weight=1)
frame_actions.columnconfigure(1, weight=1)

tk.Button(frame_actions, text="Source", command=open_source, bg="#404060", fg="white").grid(row=0, column=0, sticky="ew")
tk.Button(frame_actions, text="Dest", command=open_dest, bg="#404060", fg="white").grid(row=0, column=1, sticky="ew")
tk.Button(frame_actions, text="Generate Thumbs", command=generate_thumbnails, bg="#404060", fg="white").grid(row=0, column=3, sticky="ew")

status_label = tk.Label(root, text="Ready", bg="#202041", fg="white")
status_label.grid(row=4, column=0, pady=(5, 0))
Thumbnail_generator = ThumbManager(root, data_dir, None, status_label)

tk.Label(root, textvariable=generated_so_far, bg="#202041", fg="white").grid(row=5, column=0, pady=(0, 10))

# Load Prefs
if os.path.exists(prefs_path):
    try:
        with open(prefs_path, "r") as f:
            p = json.load(f).get("preferences", {})
            folder_path_var.set(p.get("path", ""))
            size_var.set(str(p.get("thumbnailsize", "640")))
            ext_var.set(p.get("extension", ".webp"))
            quality_var.set(p.get("quality", 90))
            lossless_var.set(p.get("lossless", False))
            mode_var.set(p.get("resize_mode", "Keep Aspect Ratio"))
            naming_var.set(p.get("naming", "Original Name"))
            structure_var.set(p.get("structure", "Preserve Structure"))
    except Exception as e:
        print(f"Error loading prefs: {e}")

root.mainloop()
