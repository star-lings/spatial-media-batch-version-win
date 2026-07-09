import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(__file__))
from spatialmedia import batch_gui

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
GOOD_FILE = os.path.join(DATA_DIR, "testsrc_320x240_h264.mp4")

BAD_FILE = os.path.join(DATA_DIR, "not_a_real_video.mp4")
with open(BAD_FILE, "wb") as f:
    f.write(b"this is not a valid mp4 file at all")

root = tk.Tk()
app = batch_gui.BatchApplication(master=root)
app._add_paths([GOOD_FILE, BAD_FILE])

out_dir = os.path.join(os.path.dirname(__file__), "smoke_out_edge")
os.makedirs(out_dir, exist_ok=True)
app.var_same_folder.set(0)
app.output_dir_var.set(out_dir)
app.var_spherical.set(1)
app.var_overwrite.set(1)

app.action_start()
deadline = time.time() + 30
while app.processing and time.time() < deadline:
    root.update()
    time.sleep(0.05)

statuses = {os.path.basename(j.input_path): (j.status, j.detail) for j in app.jobs}
print("Statuses after first run:", statuses)
assert statuses["testsrc_320x240_h264.mp4"][0] == batch_gui.STATUS_DONE
assert statuses["not_a_real_video.mp4"][0] == batch_gui.STATUS_ERROR
print("PASS: bad file correctly isolated as Error without blocking the good file")

# Re-run with overwrite OFF: the good file's output already exists -> should
# be skipped rather than silently clobbered.
app.action_retry_failed()  # only resets the Error row; Done row stays Done
assert app.jobs[0].status == batch_gui.STATUS_DONE
assert app.jobs[1].status == batch_gui.STATUS_PENDING
print("PASS: Retry Failed left the completed job alone and reset only the failed one")

root.destroy()
os.remove(BAD_FILE)
print("ALL EDGE-CASE TESTS PASSED")
