# 模板库前端截图 + DOM 检查（无头浏览器）。用法：python build/shot_library.py
import os, re, socket, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "build"))
from render_check import find_browser  # noqa: E402
PORT = 3077
OUT = os.path.join(ROOT, "build", "studio-out")
srv = subprocess.Popen([sys.executable, "build/studio_server.py", "--port", str(PORT)], cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0: break
        time.sleep(.3)
    br = find_browser()
    prof = os.path.join(OUT, ".prof-lib")
    base = [br, "--headless=new", "--disable-gpu", "--force-device-scale-factor=1", "--user-data-dir=" + prof,
            "--window-size=1440,1100", "--virtual-time-budget=8000"]
    url = "http://127.0.0.1:%d/" % PORT
    shot = os.path.join(OUT, "shot-library.png")
    subprocess.run(base + ["--screenshot=" + shot, url], capture_output=True, timeout=120)
    dom = subprocess.run(base + ["--dump-dom", url], capture_output=True, timeout=120,
                         encoding="utf-8", errors="replace").stdout
    cards = len(re.findall(r'class="card"', dom))
    tags = re.findall(r'<span class="tag">(A\d)</span>', dom)
    print("screenshot", os.path.exists(shot), shot)
    print("cards", cards, "builtin tags", tags)
    print("libv on", 'class="libv on"' in dom)
finally:
    srv.terminate()
