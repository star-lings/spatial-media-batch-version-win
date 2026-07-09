"""Headless smoke test: launch the batch GUI, add the repo's sample files,
run a real injection pass, and verify output files + statuses, then quit.
Run under xvfb-run since there's no real display in this sandbox.
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(__file__))
from spatialmedia import batch_gui

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SAMPLE_FILES = [
    os.path.join(DATA_DIR, "testsrc_320x240_h264.mp4"),
    os.path.join(DATA_DIR, "testsrc_320x240_vp9.mp4"),
]

root = tk.Tk()
app = batch_gui.BatchApplication(master=root)

# 1. Add files directly (bypassing the file dialog, which needs a human).
app._add_paths(SAMPLE_FILES)
assert len(app.jobs) == 2, f"expected 2 jobs, got {len(app.jobs)}"
print("PASS: files added to queue ->", [os.path.basename(j.input_path) for j in app.jobs])

# 2. Configure: spherical only, output to a temp dir, default suffix.
out_dir = os.path.join(os.path.dirname(__file__), "smoke_out")
os.makedirs(out_dir, exist_ok=True)
app.var_same_folder.set(0)
app.output_dir_var.set(out_dir)
app.var_spherical.set(1)
app.var_3d.set(0)
app.var_spatial_audio.set(0)
app.var_overwrite.set(1)

# 3. Kick off processing (spawns the worker thread) and pump the Tk loop
#    until it reports finished, same as a real user watching the window.
app.action_start()

deadline = time.time() + 30
while app.processing and time.time() < deadline:
    root.update()
    time.sleep(0.05)

assert not app.processing, "worker did not finish in time"

statuses = {os.path.basename(j.input_path): j.status for j in app.jobs}
print("Final statuses:", statuses)
assert all(s == batch_gui.STATUS_DONE for s in statuses.values()), statuses

for j in app.jobs:
    assert os.path.exists(j.output_path), f"missing output {j.output_path}"
    assert os.path.getsize(j.output_path) > 0

print("PASS: batch injection completed, output files exist and are non-empty")

# 4. Sanity-check the injected metadata round-trips via the same parser
#    the manual tool uses.
from spatialmedia import metadata_utils


def _console(msg):
    pass


for j in app.jobs:
    parsed = metadata_utils.parse_metadata(j.output_path, _console)
    assert parsed and parsed.video, f"no spherical metadata found in {j.output_path}"
    print("PASS: verified spherical metadata present in", os.path.basename(j.output_path))

root.destroy()
print("ALL SMOKE TESTS PASSED")
