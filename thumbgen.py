import os
from time import perf_counter
import queue
from concurrent.futures import ThreadPoolExecutor
from hashlib import md5
from PIL import Image
import threading
from imageio import get_reader
vipsbin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vips-dev-8.18", "bin")
os.environ['PATH'] = os.pathsep.join((vipsbin, os.environ['PATH']))
os.add_dll_directory(vipsbin)
if os.path.isdir(vipsbin):
    os.add_dll_directory(vipsbin)
import pyvips

class Imagefile:
    def __init__(self, name, path, ext) -> None:
        self.name = name
        self.path = path
        self.ext = ext
    
    def gen_id(self):
        file_name = self.path.replace('\\', '/').split('/')[-1]
        file_stats = os.stat(self.path)
        self.file_size = file_stats.st_size
        self.mod_time = file_stats.st_mtime
        id = f"{file_name} {file_stats.st_size} {file_stats.st_mtime}"
        self.id = md5(id.encode('utf-8')).hexdigest()

class ThumbManager:
    class DaemonThreadPoolExecutor(ThreadPoolExecutor):
        """ThreadPoolExecutor that sets all worker threads as daemon threads."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._set_daemon_threads(kwargs["thread_name_prefix"])

        def _set_daemon_threads(self, name):
            """
            Replace all non-daemon threads in the executor with daemon threads.
            Called after pool creation. Compatible with Python 3.9–3.13.
            """
            old_threads = list(self._threads)
            self._threads.clear()
            for i in range(len(old_threads), self._max_workers):
                t = threading.Thread(target=self._worker_entry, name=f"{name}_{i+1}", daemon=True)
                t.start()
                self._threads.add(t)

        def _worker_entry(self):
            """Our wrapper for the internal work queue processing loop."""
            while True:
                try:
                    work_item = self._work_queue.get(block=True)
                    if work_item is None:
                        break
                    work_item.run()
                    del work_item
                except Exception:
                    import traceback
                    traceback.print_exc()
    thumb_ext = {"png", "jpg", "jpeg", "bmp", "pcx", "tiff", "psd", "jfif", "gif", "webp", "avif"}
    anim_ext = {"gif", "webp", "webm", "mp4", "mkv", "m4v", "mov"}
    video_ext = {"webm", "mp4", "mkv", "m4v", "mov"}
    thumb_pool = None
    frame_pool = None
    def __init__(self, root, data_dir, func, status_label):
        self.root = root
        self.status_label = status_label
        self.data_dir = data_dir
        self.thumb_after_id = None
        self.frame_after_id = None
        self.func = func
        self.processed_count = 0
        self.settings = {}
        self.size = None
        self.quality = None
        self.lossless = False
        self.mode = None
        self.naming = None
        self.structure = None
        
        # Queues
        self.thumb_queue = queue.Queue()
        self.frame_queue = queue.Queue()
        
        # Worker threads
        self.thumb_worker = None
        self.frame_worker = None
        self.stop_event = threading.Event()
        self.worker_ready = threading.Event()
        self._cf_lock = threading.Lock()
        self._cf_cond = threading.Condition(self._cf_lock)
        self._left_lock = threading.Lock()

        # Thread pool sizes
        self.thumb_workers = 8

    def start_background_worker(self):
        if self.stop_event.is_set():
            self.stop_event.clear()

        # recreate executors if missing
        if not getattr(self, "thumb_pool", None):
            self.thumb_pool = ThumbManager.DaemonThreadPoolExecutor(thread_name_prefix="(Pool) T_thread", max_workers=self.thumb_workers)

        # start worker threads only if dead
        if not getattr(self, "_thumb_worker_running", False):
            self._thumb_worker_running = True
            self.thumb_after_id = self.root.after(1, self._thumb_worker)

    def stop_background_worker(self):
        """Tell workers to stop, but never block the main thread."""
        self.stop_event.set()
        if self.thumb_after_id:
            self.root.after_cancel(self.thumb_after_id)
        for pool in (self.thumb_pool, self.frame_pool):
            if pool:
                pool.shutdown(wait=False, cancel_futures=True)

        self.thumb_pool = None
        self.frame_pool = None
        for q in (self.thumb_queue, self.frame_queue):
                with q.mutex:
                    q.queue.clear()
                    
        self._thumb_worker_running = False
        self._frame_worker_running = False

    def _thumb_worker(self):
        if self.stop_event.is_set():
            self._thumb_worker_running = False
            return

        while not self.thumb_queue.empty():
            try:
                item = self.thumb_queue.get_nowait()
                self.thumb_pool.submit(self._process_thumb, item)
            except queue.Empty:
                break
            except Exception as e:
                print("Thumbnail pool submit error:", e)
                break

        self._thumb_worker_running = False
        
    def _process_thumb(self, item):
        obj = item
        try:
            self.gen_thumb(obj)
        except Exception as e:
            print("Error encountered in Thumbmanager:", e)
        finally:
            self.thumb_queue.task_done()
        
        if self.thumb_queue.empty() and self.thumb_queue.unfinished_tasks == 0:
            self.root.after(0, lambda: self.status_label.config(text=f"Done in {perf_counter()-self.start:.2f}s!"))

    def flush_all(self):
        self.stop_event.set()
        if self.thumb_after_id:
            self.root.after_cancel(self.thumb_after_id)
        if self.frame_after_id:
            self.root.after_cancel(self.frame_after_id)
        self._thumb_worker_running = False
        self._frame_worker_running = False

        for q in (self.thumb_queue, self.frame_queue):
            with q.mutex:
                q.queue.clear()
        
    def generate(self, imgfiles, settings):
        self.settings = settings
        self.size = settings["size"]
        self.quality = settings["quality"]
        self.lossless = settings["lossless"]
        self.mode = settings["mode"]
        self.ext = settings["ext"].strip(".")
        self.naming = settings["naming"]
        self.structure = settings["structure"]
        self.start = perf_counter()
        self.stop_event.clear()
        for x in imgfiles:
            self.thumb_queue.put(x)
        self.start_background_worker()   
    
    def gen_thumb(self, obj): # session just calls this for displayedlist
        obj.gen_id()
        pil_img = None
        name = obj.id if self.naming == "Hashed Name" else os.path.basename(obj.name).rsplit(".", 1)[0]
        if self.structure == "Flatten":
            folder_path = self.data_dir 
        else:
            folder_path = os.path.join(self.data_dir, os.path.basename(os.path.dirname(obj.path)))
            os.makedirs(folder_path, exist_ok=True)
        thumbnail_path = os.path.join(folder_path, f"{name}.{self.ext}")
        if os.path.exists(thumbnail_path): return
        if obj.ext.lower() in ("webm," "mp4"): #Webm, mp4
            try:
                reader = None
                reader = get_reader(obj.path)
                pil_img = Image.fromarray(reader.get_data(0))
                match self.mode:
                    case "Keep Aspect Ratio": pil_img.thumbnail((self.size, self.size))
                    case "Stretch to Dimensions": pil_img = pil_img.resize((self.size, self.size))
                    case "Pad to Dimensions":
                        pil_img.thumbnail((self.size, self.size))
                        w, h = pil_img.size
                        new_im = Image.new("RGB", (self.size, self.size), (114, 114, 114))
                        left = (self.size - w) // 2
                        top = (self.size - h) // 2
                        new_im.paste(pil_img, (left, top))
                        pil_img = new_im
                    case "Crop to Dimensions":
                        w, h = pil_img.size
                        side = min(w, h)
                        left = (w - side) // 2
                        top = (h - side) // 2
                        pil_img = pil_img.crop((left, top, left + side, top + side))
                        pil_img = pil_img.resize((self.size, self.size), Image.BILINEAR)
                if self.ext == "jpeg": pil_img = pil_img.convert("RGB")
                elif pil_img.mode not in ("RGB", "RGBA"): pil_img = pil_img.convert("RGBA")
                pil_img.save(thumbnail_path, format=self.ext, quality=self.quality, lossless=self.lossless)
                return
            except Exception as e:
                print(f"Couldn't create thumbnail for video: {os.path.basename(thumbnail_path)} : Error: {e}")
            finally: 
                if reader: reader.close()
        else:
            if self.mode == "Keep Aspect Ratio":
                try:
                    vips_img = pyvips.Image.thumbnail(obj.path, self.size)
                    buffer = vips_img.write_to_memory()
                    pformat = str(vips_img.interpretation).lower()
                    match pformat:
                        case "srgb":
                            if vips_img.bands == 3: pformat = "RGB"
                            elif vips_img.bands == 4: pformat = "RGBA"
                        case "b-w": pformat = "L"
                        case "rgb16": pformat = "I;16"
                        case"grey16": pformat = "I;16"
                    pil_img = Image.frombytes(pformat, (vips_img.width, vips_img.height), buffer, "raw")
                    if self.ext == "jpeg": pil_img = pil_img.convert("RGB")
                    pil_img.save(thumbnail_path, format=self.ext, quality=self.quality, lossless=self.lossless)
                    return
                except Exception as e: # Pillow fallback
                    print(f"Pyvips couldn't create thumbnail: {obj.name} : Error: {e}.")
            try:
                with Image.open(obj.path) as pil_img:
                    match self.mode:
                        case "Keep Aspect Ratio": pil_img.thumbnail((self.size, self.size))
                        case "Stretch to Dimensions": pil_img = pil_img.resize((self.size, self.size))
                        case "Pad to Dimensions":
                            pil_img.thumbnail((self.size, self.size))
                            w, h = pil_img.size
                            new_im = Image.new("RGB", (self.size, self.size), (114, 114, 114))
                            left = (self.size - w) // 2
                            top = (self.size - h) // 2
                            new_im.paste(pil_img, (left, top))
                            pil_img = new_im
                        case "Crop to Dimensions":
                            w, h = pil_img.size
                            side = min(w, h)
                            left = (w - side) // 2
                            top = (h - side) // 2
                            pil_img = pil_img.crop((left, top, left + side, top + side))
                            pil_img = pil_img.resize((self.size, self.size), Image.BILINEAR)
                    if self.ext == "jpeg": pil_img = pil_img.convert("RGB")
                    elif pil_img.mode not in ("RGB", "RGBA"): pil_img = pil_img.convert("RGBA")
                    pil_img.save(thumbnail_path, format=self.ext, quality=self.quality, lossless=self.lossless)
                    return
            except Exception as e:
                print(f"Pillows couldn't create thumbnail, either: {obj.name} : Error: {e}")

def walk(src):
    supported_formats = {"png", "gif", "jpg", "jpeg", "bmp", "pcx", "tiff", "webp", "psd", "jfif", "mp4", "webm"}
    imagelist = []
    for root, dirs, files in os.walk(src, topdown=True):
        dirs[:] = [d for d in dirs if d] # Filter empty dirs if needed
        for name in files:
            parts = name.rsplit(".", 1)
            if len(parts) == 2 and parts[1].lower() in supported_formats:
                imgfile = Imagefile(name, os.path.join(root, name), parts[1].lower())
                imagelist.append(imgfile)
    return imagelist