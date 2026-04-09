import os, queue, threading, av

from time import perf_counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import md5
from collections import Counter

from PIL import Image, ImageFile
Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

vipsbin = None
def pyvips_is_detected():
    global vipsbin
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for x in os.listdir(script_dir):
        if "vips" in x:
            vipsbin = os.path.join(script_dir, x, "bin")
            break
    return True if vipsbin else False

if pyvips_is_detected():
    os.environ['PATH'] = os.pathsep.join((vipsbin, os.environ['PATH']))
    import pyvips
    use_pyvips = True
else:
    print(f"Libvips not found in {vipsbin}.\nDownload libvips windows binaries (64, ALL or WEB). https://github.com/libvips/build-win64-mxe/releases/tag/v8.18.0\nFalling back to PIL.")
    use_pyvips = False

class ThumbManager:
    supported_formats = {"png", "jpg", "jpeg", "avif",
                         "gif", "webp",
                         "mp4", "webm", "mkv", "m4v", "mov",
                         "psd", "jfif", "tiff", "bmp", "pcx"}
    pyav_formats = {"mp4", "webm", "mkv", "m4v", "mov"}
    mem = Counter()
    
    def __init__(self, root, data_dir, func, status_label):
        self.root = root
        self.status_label = status_label
        self.data_dir = data_dir
        self.func = func
        self.processed_count = 0

        self.use_pyvips = use_pyvips
        self.settings = {}
        self.size = None
        self.quality = None
        self.lossless = False
        self.mode = None
        self.naming = None
        self.structure = None
        self.cached = set()
        
        self.thumb_worker = None
        self._thumb_worker_running = False
        self.thumb_pool = None
        self.thumb_queue = queue.Queue()
        self.thumb_workers = max(1, round(os.cpu_count()*(2/3)))

    def walk(self, src, data_dir):
        imagelist = []
        for root, dirs, files in os.walk(src, topdown=True):
            for name in files:
                parts = name.rsplit(".", 1)
                if len(parts) == 2 and parts[1].lower() in self.supported_formats:
                    imgfile = Imagefile1(name, os.path.join(root, name), parts[1].lower())
                    imagelist.append(imgfile)
        
        cached = set()
        for root, dirs, files in os.walk(data_dir, topdown=True):
            for name in files:
                parts = name.rsplit(".", 1)
                imgfile = Imagefile1(name, os.path.join(root, name), parts[1].lower())
                cached.add(name.rsplit(".",1)[0])
        
        self.cached = cached
        return imagelist[:10000]
    
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
        if self.structure == "Flatten":
            os.makedirs(self.data_dir, exist_ok=True)
        for x in imgfiles:
            self.thumb_queue.put(x)
        self._start_background_worker()   
    
    def gen_via_av(self, obj, folder_path, name):
        container = None
        pix_fmt = 'rgba' if obj.ext == "gif" else 'rgb24'
        max_size = 256
        try:
            container = av.open(obj.path, metadata_errors='ignore') # do we need replace?
            stream = container.streams.video[0]
            stream.thread_count = 0

            for frame in container.decode(stream):
                w, h = stream.width, stream.height
                scale = max_size / max(w, h)
                match self.mode:
                    case "Keep Aspect Ratio" | "Pad to Dimensions":
                        new_w, new_h = int(w * scale), int(h * scale)
                    case "Stretch to Dimensions":
                        new_w, new_h = self.size, self.size
                    case "Crop to Dimensions":
                        scale = self.size / min(w, h)
                        new_w, new_h = int(w * scale), int(h * scale)

                resized_frame = frame.reformat(width=new_w, height=new_h, interpolation=av.video.reformatter.Interpolation.AREA, format=pix_fmt)
                pil_img = resized_frame.to_image()
                self.mem[f"PYAV_{pil_img.mode}"] += 1
                
                if self.mode == "Pad to Dimensions":
                    new_im = Image.new("RGB", (self.size, self.size), (114, 114, 114)) # might not respect transparency for gif. (Fallback)
                    w, h = pil_img.size
                    left, top = (self.size - w) // 2, (self.size - h) // 2
                    new_im.paste(pil_img, (left, top))
                    pil_img = new_im
                
                if self.mode == "Crop to Dimensions":
                    curr_w, curr_h = pil_img.size
                    left = (curr_w - self.size) // 2
                    top = (curr_h - self.size) // 2
                    pil_img = pil_img.crop((left, top, left + self.size, top + self.size))

                if self.ext == "jpeg" and pil_img.mode != "RGB": pil_img = pil_img.convert("RGB")
                elif pil_img.mode not in ("RGB", "RGBA"): pil_img = pil_img.convert("RGBA")
                thumbnail_path = os.path.join(folder_path, f"{name}.{self.ext}")
                pil_img.save(thumbnail_path, format=self.ext, quality=self.quality, lossless=self.lossless)                        
                return True
        except Exception as e:
            print(f"PyAV thumbnail error for {os.path.basename(obj.path)}: {e}")
            return False
        finally:
            if container:
                container.close()

    def gen_via_pyvips(self, obj, folder_path, name):
        try:
            vips_img = pyvips.Image.new_from_file(obj.path)
            self.mem[f"PYVIPS_{str(vips_img.interpretation).lower()}"] += 1

            match self.mode:
                case "Keep Aspect Ratio":
                    vips_img = pyvips.Image.thumbnail(obj.path, self.size)
                case "Stretch to Dimensions":
                    vips_img = pyvips.Image.new_from_file(obj.path, access="sequential")
                    vips_img = vips_img.resize(self.size / vips_img.width, vscale=self.size / vips_img.height)
                case "Pad to Dimensions":
                    vips_img = pyvips.Image.thumbnail(obj.path, self.size)
                    bg = [114, 114, 114, 255] if vips_img.hasalpha() else [114, 114, 114]
                    vips_img = vips_img.embed((self.size - vips_img.width) // 2, 
                                              (self.size - vips_img.height) // 2, 
                                              self.size, self.size, 
                                              extend="background", background=bg)
                case "Crop to Dimensions":
                    vips_img = pyvips.Image.thumbnail(obj.path, self.size, crop="centre")

            pformat = str(vips_img.interpretation).lower()
            match pformat:
                case "srgb": pformat = "RGBA" if vips_img.bands == 4 else "RGB"
                case "b-w": pformat = "LA" if vips_img.bands == 2 else "L"
                case "rgb16" | "grey16": pformat = "I;16"

            pil_img = Image.frombytes(pformat, (vips_img.width, vips_img.height), vips_img.write_to_memory(), "raw")
            if self.ext == "jpeg" and pil_img.mode != "RGB": pil_img = pil_img.convert("RGB")
            elif pil_img.mode not in ("RGB", "RGBA"): pil_img = pil_img.convert("RGBA")
            thumbnail_path = os.path.join(folder_path, f"{name}.{self.ext}")
            pil_img.save(thumbnail_path, format=self.ext, quality=self.quality, lossless=self.lossless)
            return True
        except Exception as e:
            print(f"Pyvips couldn't create thumbnail: {obj.name} : Error: {e}")
            return False

    def gen_via_pil(self, obj, folder_path, name):
        try:
            with Image.open(obj.path) as pil_img:
                self.mem[f"PIL_{pil_img.mode}"] += 1
                if pil_img.mode not in ("RGB", "RGBA"): pil_img = pil_img.convert("RGBA" if self.ext != "jpeg" else "RGB")
                match self.mode:
                    case "Keep Aspect Ratio": pil_img.thumbnail((self.size, self.size), resample=Image.Resampling.LANCZOS)
                    case "Stretch to Dimensions": pil_img = pil_img.resize((self.size, self.size))
                    case "Pad to Dimensions":
                        pil_img.thumbnail((self.size, self.size))
                        w, h = pil_img.size
                        new_im = Image.new("RGB", (self.size, self.size), (114, 114, 114))
                        left = (self.size - w) // 2
                        top = (self.size - h) // 2
                        new_im.paste(pil_img, (left, top), pil_img)
                        pil_img = new_im
                    case "Crop to Dimensions":
                        w, h = pil_img.size
                        side = min(w, h)
                        left = (w - side) // 2
                        top = (h - side) // 2
                        pil_img = pil_img.crop((left, top, left + side, top + side))
                        pil_img = pil_img.resize((self.size, self.size), Image.Resampling.LANCZOS)

                thumbnail_path = os.path.join(folder_path, f"{name}.{self.ext}")
                pil_img.save(thumbnail_path, format=self.ext, quality=self.quality, lossless=self.lossless)
                
                return True
        except Exception as e:
            print(f"Pillows couldn't create thumbnail, either: {obj.name} : Error: {e}")
            return False

    def gen_thumb(self, obj): # session just calls this for displayedlist
        obj.gen_id()
        name = obj.id if self.naming == "Hashed Name" else os.path.basename(obj.name).rsplit(".", 1)[0]
        folder_path = self.data_dir if self.structure == "Flatten" else os.path.join(self.data_dir, os.path.basename(os.path.dirname(obj.path)))
        if self.structure != "Flatten": os.makedirs(folder_path, exist_ok=True)
        thumbnail_path = os.path.join(folder_path, f"{name}.{self.ext}")

        if os.path.exists(thumbnail_path) or obj.id in self.cached: return

        if obj.ext in self.pyav_formats:
            success = self.gen_via_av(obj, folder_path, name)
        
        elif obj.ext == "gif": # gif likes pil
            success = self.gen_via_pil(obj, folder_path, name)
            if not success and self.use_pyvips: success = self.gen_via_pyvips(obj, folder_path, name)
            if not success: success = self.gen_via_av(obj, folder_path, name)

        elif self.use_pyvips: # webp likes pyvips
            success = self.gen_via_pyvips(obj, folder_path, name)
            if not success: self.gen_via_pil(obj, folder_path, name) # fallback
            
        else:
            self.gen_via_pil(obj, folder_path, name)
    
    def _start_background_worker(self):
        if not self.thumb_pool:
            self.thumb_pool = DaemonThreadPoolExecutor(thread_name_prefix="(Pool) T_thread", max_workers=self.thumb_workers)

        if not self._thumb_worker_running:
            self._thumb_worker_running = True
            self.root.after(1, self._thumb_worker)
    
    def _thumb_worker(self):
        while not self.thumb_queue.empty():
            try:
                item = self.thumb_queue.get_nowait()
                self.thumb_pool.submit(self._process_thumb, item)
            except queue.Empty:
                break
            except Exception as e:
                print("Thumbnail pool submit error:", e)
                break
        
        self._check_if_done()
        
    def _process_thumb(self, item):
        obj = item
        try:
            self.gen_thumb(obj)
        except Exception as e:
            print("Error encountered in Thumbmanager:", e)
        finally:
            self.thumb_queue.task_done()
            self.processed_count += 1
            if self.thumb_queue.unfinished_tasks == 0:
                self.status_label.config(text=f"Done in {perf_counter()-self.start:.2f}s!")
                self.root.after(1, self.func, self.processed_count)
            if self.processed_count % 1 == 0:
                self.root.after(1, self.func, self.processed_count)

    def _check_if_done(self):            
        if self.thumb_queue.unfinished_tasks == 0:
            self.status_label.config(text=f"Done in {perf_counter()-self.start:.2f}s!")
            print(self.mem)
            self._thumb_worker_running = False
        else:
            self.root.after(20, self._check_if_done)
    
class Imagefile1:
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

class DaemonThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._set_daemon_threads(kwargs["thread_name_prefix"])

    def _set_daemon_threads(self, name):
        old_threads = list(self._threads)
        self._threads.clear()
        for i in range(len(old_threads), self._max_workers):
            t = threading.Thread(target=self._worker_entry, name=f"{name}_{i+1}", daemon=True)
            t.start()
            self._threads.add(t)

    def _worker_entry(self):
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
