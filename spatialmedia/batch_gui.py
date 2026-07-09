#! /usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2016 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Spatial Media Metadata Injector - Batch Queue GUI

A queue-based batch front end for metadata_utils. Unlike gui.py (which
applies one setting pass to whatever files happen to be selected at Inject
time and blocks the UI thread while it works), this tool keeps a persistent,
editable queue: files can be added and removed at any time, processing runs
on a background thread so the window stays responsive, each row shows live
per-file status, and failed files can be retried without re-queuing
everything else.

It also fixes a correctness gap in the stock GUI's batch path: spatial-audio
metadata depends on each file's own channel count (mono/stereo/4ch/9ch
ambisonics all need different <GSpherical:...> parameters), so this tool
parses every file individually before injecting rather than computing the
spatial-audio description once from the first selected file and reusing it
for the whole batch.
"""

import ntpath
import os
import queue
import sys
import platform
import ctypes
import threading
import traceback

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    print("Tkinter library is not available.")
    sys.exit(0)

path = os.path.dirname(sys.modules[__name__].__file__)
path = os.path.join(path, "..")
sys.path.insert(0, path)
from spatialmedia import metadata_utils

VIDEO_EXTENSIONS = (".mp4", ".mov")

STATUS_PENDING = "Pending"
STATUS_PROCESSING = "Processing..."
STATUS_DONE = "Done"
STATUS_ERROR = "Error"
STATUS_SKIPPED = "Skipped"


def make_dpi_aware():
    if platform.system() == "Windows":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(True)
        except AttributeError:
            pass


make_dpi_aware()


class Job(object):
    """A single queued file and its processing outcome."""

    __slots__ = ("input_path", "output_path", "status", "detail", "row_id")

    def __init__(self, input_path):
        self.input_path = input_path
        self.output_path = ""
        self.status = STATUS_PENDING
        self.detail = ""
        self.row_id = None


class QueueConsole(object):
    """Adapts metadata_utils' console(str) callback into a message queue
    so log lines produced on the worker thread can be drained safely on
    the main thread."""

    def __init__(self, post_fn):
        self.post_fn = post_fn

    def append(self, text):
        self.post_fn(("log", text))


class BatchApplication(tk.Frame):
    def __init__(self, master=None):
        master.wm_title("Spatial Media Batch Injector")
        master.config(menu=tk.Menu(master))
        tk.Frame.__init__(self, master)

        self.jobs = []
        self.msg_queue = queue.Queue()
        self.worker_thread = None
        self.cancel_event = threading.Event()
        self.processing = False

        self.open_options = {
            "filetypes": [("Videos", ("*.mov", "*.mp4"))],
            "multiple": True,
        }

        self.create_widgets()
        self.pack(fill="both", expand=True)
        self.after(100, self.poll_messages)

        master.geometry("760x620")
        master.minsize(680, 520)
        master.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------------------------------------------------- Widgets --

    def create_widgets(self):
        PAD = 10

        # -- Queue controls -------------------------------------------------
        controls = tk.Frame(self)
        controls.pack(fill="x", padx=PAD, pady=(PAD, 4))

        ttk.Button(controls, text="Add Files...", command=self.action_add_files).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(controls, text="Add Folder...", command=self.action_add_folder).pack(
            side="left", padx=6
        )
        ttk.Button(
            controls, text="Remove Selected", command=self.action_remove_selected
        ).pack(side="left", padx=6)
        ttk.Button(controls, text="Clear Queue", command=self.action_clear).pack(
            side="left", padx=6
        )
        ttk.Button(
            controls, text="Retry Failed", command=self.action_retry_failed
        ).pack(side="left", padx=6)

        # -- Queue table ------------------------------------------------------
        table_frame = tk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=PAD, pady=4)

        columns = ("file", "status", "detail")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="extended"
        )
        self.tree.heading("file", text="File")
        self.tree.heading("status", text="Status")
        self.tree.heading("detail", text="Detail")
        self.tree.column("file", width=340, anchor="w")
        self.tree.column("status", width=100, anchor="center")
        self.tree.column("detail", width=260, anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.tag_configure("done", foreground="#1a7f37")
        self.tree.tag_configure("error", foreground="#c62828")
        self.tree.tag_configure("processing", foreground="#1565c0")
        self.tree.tag_configure("skipped", foreground="#8a6d00")

        # -- Metadata settings --------------------------------------------------
        settings = tk.LabelFrame(self, text="Metadata to apply to every queued file")
        settings.pack(fill="x", padx=PAD, pady=6)

        self.var_spherical = tk.IntVar(value=1)
        self.var_3d = tk.IntVar(value=0)
        self.var_spatial_audio = tk.IntVar(value=0)

        tk.Checkbutton(
            settings,
            text="Spherical (360)",
            variable=self.var_spherical,
            command=self.update_state,
        ).grid(row=0, column=0, sticky="w", padx=PAD, pady=4)

        self.checkbox_3d = tk.Checkbutton(
            settings, text="Stereoscopic 3D (top/bottom)", variable=self.var_3d
        )
        self.checkbox_3d.grid(row=0, column=1, sticky="w", padx=PAD, pady=4)

        self.checkbox_spatial_audio = tk.Checkbutton(
            settings,
            text="Spatial audio (ambiX ACN/SN3D) - detected per file",
            variable=self.var_spatial_audio,
        )
        self.checkbox_spatial_audio.grid(
            row=1, column=0, columnspan=2, sticky="w", padx=PAD, pady=(0, 6)
        )

        # -- Output settings --------------------------------------------------
        output_frame = tk.LabelFrame(self, text="Output")
        output_frame.pack(fill="x", padx=PAD, pady=6)

        self.var_same_folder = tk.IntVar(value=1)
        tk.Radiobutton(
            output_frame,
            text="Save next to each source file",
            variable=self.var_same_folder,
            value=1,
            command=self.update_output_state,
        ).grid(row=0, column=0, sticky="w", padx=PAD, pady=(6, 2), columnspan=3)
        tk.Radiobutton(
            output_frame,
            text="Save to folder:",
            variable=self.var_same_folder,
            value=0,
            command=self.update_output_state,
        ).grid(row=1, column=0, sticky="w", padx=PAD, pady=2)

        self.output_dir_var = tk.StringVar(value="")
        self.entry_output_dir = tk.Entry(
            output_frame, textvariable=self.output_dir_var, state="disabled", width=40
        )
        self.entry_output_dir.grid(row=1, column=1, sticky="we", padx=4, pady=2)
        self.button_browse_output = ttk.Button(
            output_frame, text="Browse...", command=self.action_browse_output, state="disabled"
        )
        self.button_browse_output.grid(row=1, column=2, padx=(4, PAD), pady=2)
        output_frame.columnconfigure(1, weight=1)

        suffix_row = tk.Frame(output_frame)
        suffix_row.grid(row=2, column=0, columnspan=3, sticky="w", padx=PAD, pady=(4, 8))
        tk.Label(suffix_row, text="Filename suffix:").pack(side="left")
        self.suffix_var = tk.StringVar(value="_injected")
        tk.Entry(suffix_row, textvariable=self.suffix_var, width=16).pack(
            side="left", padx=6
        )
        self.var_overwrite = tk.IntVar(value=0)
        tk.Checkbutton(
            suffix_row, text="Overwrite existing output files", variable=self.var_overwrite
        ).pack(side="left", padx=(16, 0))

        # -- Progress + run controls --------------------------------------------------
        run_frame = tk.Frame(self)
        run_frame.pack(fill="x", padx=PAD, pady=(4, 2))

        self.progress = ttk.Progressbar(run_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.button_start = ttk.Button(
            run_frame, text="Start Queue", command=self.action_start
        )
        self.button_start.pack(side="left", padx=4)
        self.button_cancel = ttk.Button(
            run_frame, text="Cancel", command=self.action_cancel, state="disabled"
        )
        self.button_cancel.pack(side="left", padx=4)

        self.label_message = tk.Label(self, anchor="w", fg="blue")
        self.label_message["text"] = (
            "Add files or a folder, choose settings, then Start Queue."
        )
        self.label_message.pack(fill="x", padx=PAD, pady=(2, 8))

    # -------------------------------------------------------------- Queue --

    def action_add_files(self):
        selected = filedialog.askopenfilenames(**self.open_options)
        if not selected:
            return
        self._add_paths(selected)

    def action_add_folder(self):
        folder = filedialog.askdirectory(title="Select folder to scan for videos")
        if not folder:
            return
        found = []
        for root_dir, _dirs, files in os.walk(folder):
            for name in files:
                if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
                    found.append(os.path.join(root_dir, name))
        if not found:
            messagebox.showinfo(
                "No videos found", "No .mp4 or .mov files were found in that folder."
            )
            return
        self._add_paths(sorted(found))

    def _add_paths(self, paths):
        existing = {job.input_path for job in self.jobs}
        added = 0
        for p in paths:
            p = os.path.abspath(p)
            if p in existing:
                continue
            job = Job(p)
            self.jobs.append(job)
            job.row_id = self.tree.insert(
                "", "end", values=(ntpath.basename(p), job.status, "")
            )
            existing.add(p)
            added += 1
        self.set_message(f"{added} file(s) added. Queue has {len(self.jobs)} file(s).")

    def action_remove_selected(self):
        if self.processing:
            return
        selected_rows = set(self.tree.selection())
        if not selected_rows:
            return
        self.jobs = [j for j in self.jobs if j.row_id not in selected_rows]
        for row_id in selected_rows:
            self.tree.delete(row_id)
        self.set_message(f"Queue has {len(self.jobs)} file(s).")

    def action_clear(self):
        if self.processing:
            return
        self.tree.delete(*self.tree.get_children())
        self.jobs = []
        self.set_message("Queue cleared.")

    def action_retry_failed(self):
        if self.processing:
            return
        count = 0
        for job in self.jobs:
            if job.status in (STATUS_ERROR, STATUS_SKIPPED):
                job.status = STATUS_PENDING
                job.detail = ""
                self.tree.item(
                    job.row_id,
                    values=(ntpath.basename(job.input_path), job.status, ""),
                    tags=(),
                )
                count += 1
        self.set_message(f"Reset {count} file(s) to Pending.")

    def action_browse_output(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.output_dir_var.set(folder)

    def update_output_state(self):
        if self.var_same_folder.get():
            self.entry_output_dir.configure(state="disabled")
            self.button_browse_output.configure(state="disabled")
        else:
            self.entry_output_dir.configure(state="normal")
            self.button_browse_output.configure(state="normal")

    def update_state(self):
        pass

    # ------------------------------------------------------------- Run/UI --

    def set_message(self, text, error=False):
        self.label_message["text"] = text
        self.label_message.config(fg="red" if error else "blue")

    def set_running_controls(self, running):
        state = "disabled" if running else "normal"
        for widget in (
            self.button_start,
        ):
            widget.configure(state=state)
        self.button_cancel.configure(state="normal" if running else "disabled")

    def action_start(self):
        if self.processing:
            return
        pending = [j for j in self.jobs if j.status in (STATUS_PENDING,)]
        if not pending:
            messagebox.showinfo(
                "Nothing to do", "Add files and make sure at least one is Pending."
            )
            return
        if not self.var_spherical.get() and not self.var_spatial_audio.get():
            messagebox.showinfo(
                "Nothing selected",
                "Check at least one of Spherical or Spatial audio to inject.",
            )
            return

        same_folder = bool(self.var_same_folder.get())
        out_dir = self.output_dir_var.get().strip()
        if not same_folder:
            if not out_dir:
                messagebox.showerror("Output folder required", "Choose an output folder.")
                return
            os.makedirs(out_dir, exist_ok=True)

        suffix = self.suffix_var.get()
        if same_folder and not suffix:
            messagebox.showerror(
                "Suffix required",
                "When saving next to source files, a filename suffix is required "
                "so outputs don't collide with inputs.",
            )
            return

        # Compute + validate output paths up front.
        for job in pending:
            base, ext = os.path.splitext(os.path.basename(job.input_path))
            out_folder = out_dir if not same_folder else os.path.dirname(job.input_path)
            job.output_path = os.path.join(out_folder, f"{base}{suffix}{ext}")
            if os.path.abspath(job.output_path) == os.path.abspath(job.input_path):
                messagebox.showerror(
                    "Output collides with input",
                    f"Computed output path equals the input for:\n{job.input_path}\n"
                    "Choose a different output folder or suffix.",
                )
                return

        self.processing = True
        self.cancel_event.clear()
        self.progress.configure(maximum=len(pending), value=0)
        self.set_running_controls(True)
        self.set_message(f"Processing {len(pending)} file(s)...")

        stereo = "top-bottom" if self.var_3d.get() else None
        want_spherical = bool(self.var_spherical.get())
        want_audio = bool(self.var_spatial_audio.get())
        overwrite = bool(self.var_overwrite.get())

        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(list(pending), want_spherical, stereo, want_audio, overwrite),
            daemon=True,
        )
        self.worker_thread.start()

    def action_cancel(self):
        self.cancel_event.set()
        self.set_message("Cancelling after the current file finishes...")

    def on_close(self):
        if self.processing:
            if not messagebox.askyesno(
                "Processing in progress",
                "A batch is still running. Quit anyway? The current file will "
                "finish first.",
            ):
                return
            self.cancel_event.set()
        self.master.destroy()

    # ---------------------------------------------------------- Worker thread --

    def _worker(self, jobs, want_spherical, stereo, want_audio, overwrite):
        post = self.msg_queue.put
        console = QueueConsole(post)

        for job in jobs:
            if self.cancel_event.is_set():
                post(("status", job, STATUS_SKIPPED, "Cancelled"))
                continue

            post(("status", job, STATUS_PROCESSING, ""))

            if not overwrite and os.path.exists(job.output_path):
                post(("status", job, STATUS_SKIPPED, "Output already exists"))
                continue

            try:
                metadata = metadata_utils.Metadata()
                if want_spherical:
                    metadata.video = metadata_utils.generate_spherical_xml(stereo=stereo)

                if want_audio:
                    parsed = metadata_utils.parse_metadata(job.input_path, console.append)
                    num_channels = parsed.num_audio_channels if parsed else 0
                    description = metadata_utils.get_spatial_audio_description(num_channels)
                    if description.is_supported:
                        metadata.audio = metadata_utils.get_spatial_audio_metadata(
                            description.order, description.has_head_locked_stereo
                        )
                    else:
                        post(
                            (
                                "log",
                                f"{ntpath.basename(job.input_path)}: {num_channels} "
                                "audio channel(s) is not a supported spatial-audio "
                                "layout; continuing with video metadata only.",
                            )
                        )

                if metadata.video is None and metadata.audio is None:
                    post(("status", job, STATUS_SKIPPED, "No applicable metadata"))
                    continue

                metadata_utils.inject_metadata(
                    job.input_path, job.output_path, metadata, console.append
                )
                post(("status", job, STATUS_DONE, os.path.basename(job.output_path)))

            except Exception as exc:  # noqa: BLE001 - surface any failure per-file
                post(("log", traceback.format_exc()))
                post(("status", job, STATUS_ERROR, str(exc)))

        post(("finished", None, None, None))

    # ---------------------------------------------------------- Main-thread poll --

    def poll_messages(self):
        try:
            while True:
                message = self.msg_queue.get_nowait()
                kind = message[0]

                if kind == "log":
                    pass  # Hook for a log panel; kept minimal by default.

                elif kind == "status":
                    _, job, status, detail = message
                    job.status = status
                    job.detail = detail
                    tag = {
                        STATUS_DONE: "done",
                        STATUS_ERROR: "error",
                        STATUS_PROCESSING: "processing",
                        STATUS_SKIPPED: "skipped",
                    }.get(status, "")
                    self.tree.item(
                        job.row_id,
                        values=(ntpath.basename(job.input_path), status, detail),
                        tags=(tag,) if tag else (),
                    )
                    if status in (STATUS_DONE, STATUS_ERROR, STATUS_SKIPPED):
                        self.progress.step(1)

                elif kind == "finished":
                    self.processing = False
                    self.set_running_controls(False)
                    done = sum(1 for j in self.jobs if j.status == STATUS_DONE)
                    errors = sum(1 for j in self.jobs if j.status == STATUS_ERROR)
                    skipped = sum(1 for j in self.jobs if j.status == STATUS_SKIPPED)
                    self.set_message(
                        f"Finished: {done} done, {errors} error(s), {skipped} skipped.",
                        error=errors > 0,
                    )

        except queue.Empty:
            pass

        self.after(100, self.poll_messages)


def report_callback_exception(self, *args):
    exception = traceback.format_exception(*args)
    messagebox.showerror("Error", "".join(exception))


def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.4)
    except tk.TclError:
        pass
    tk.report_callback_exception = report_callback_exception
    BatchApplication(master=root)
    root.mainloop()


if __name__ == "__main__":
    main()
