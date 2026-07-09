# Batch Queue Injector — what changed and how to get the .exe

## What's new
- `spatialmedia/batch_gui.py` — a new queue-based GUI. Add files/folders any
  time, remove or re-add without losing your place, watch per-file status
  live, retry only the ones that failed. Runs on a background thread so the
  window never freezes, unlike the stock `gui.py` which blocks while it
  works through whatever was selected at the moment you clicked Inject.
- `spatialmedia/spatial_media_batch_injector.spec` — PyInstaller spec for it.
- `build_executables.py` — now takes an optional target: `python
  build_executables.py batch`, `... single`, or no argument to build both.
- `.github/workflows/build-windows-exe.yml` — builds real Windows `.exe`
  files on GitHub's Windows runner and attaches them as workflow artifacts.
- `smoke_test.py`, `smoke_test_edge_cases.py` — headless regression tests
  (run under Xvfb) that exercise the actual worker thread against the repo's
  sample files: a full batch run with metadata verified afterward, plus a
  deliberately-corrupt file to confirm one bad input doesn't take down the
  rest of the batch.

## One correctness fix along the way
The existing multi-select code in `gui.py` derives spatial-audio metadata
(ambisonic order, head-locked-stereo flag) from **only the first selected
file**, then reuses that for every file in the batch — silently wrong for
any file whose channel count differs from the first one. `batch_gui.py`
parses each file's own channel count and derives its own audio metadata,
skipping just that file (with a logged reason) if its channel count isn't a
supported ambisonics layout, rather than mis-tagging it or halting the batch.

## Getting the actual Windows .exe
PyInstaller does not cross-compile — a Windows `.exe` can only be built by
running PyInstaller *on* Windows. I can't produce a genuine Windows binary
from this sandbox, so there are two ways to get one, both included:

**Automatic (recommended):** push this to a GitHub repo. The included
workflow (`.github/workflows/build-windows-exe.yml`) runs on every push to
`main` and on manual trigger (Actions tab → "Build Windows executables" →
Run workflow). It builds on a real `windows-latest` runner and uploads both
`Spatial Media Batch Injector.exe` and `Spatial Media Metadata Injector.exe`
as downloadable artifacts — no local Windows machine needed.

**Manual (on a Windows machine you have access to):**
```
pip install -r requirements.txt pyinstaller
python build_executables.py batch
```
Output lands in `dist\Spatial Media Batch Injector.exe`.

## What I verified here (Linux sandbox, no Windows available)
- Byte-compiled `batch_gui.py` cleanly.
- Launched it for real under Xvfb (virtual display), queued the repo's
  sample `.mp4` files, ran an actual threaded batch injection, and verified
  the injected spherical metadata round-trips through `metadata_utils.parse_metadata`.
- Confirmed a corrupt input file is caught and marked `Error` without
  aborting the rest of the queue, and that `Retry Failed` only resets failed
  rows.
- Ran `build_executables.py batch` through PyInstaller end-to-end (produces
  a Linux binary here, but exercises the same dependency-collection code
  path PyInstaller uses on Windows) and confirmed the frozen binary launches
  cleanly — this is the step most likely to surface a "missing hidden
  import" error, and it didn't.
- I did **not** run this on actual Windows, so please treat the first
  Windows Actions run as the real acceptance test — if anything's off
  (an icon, a DPI quirk, antivirus flagging the unsigned exe), it's a quick
  fix from here.
