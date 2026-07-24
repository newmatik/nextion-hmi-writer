# Setup

## Reading the reference

The documents at the repository root are plain Markdown. Start with [`README.md`](README.md), then
[`driving-the-editor.md`](driving-the-editor.md). Nothing needs to be installed to read them.

## Running the tests

The test suite is **stdlib-only** — no Nextion Editor, no Windows, no `.HMI` files. From the
repository root:

```sh
python -m unittest research.test_vectors
```

It verifies [`research/nextion_hmi_checksum.py`](research/nextion_hmi_checksum.py) against every
vector in [`test-vectors.md`](test-vectors.md).
[`research/nextion_hmi_binary.py`](research/nextion_hmi_binary.py) is proven separately by its
byte-exact round-trip, which needs real `.HMI` files (see below). Requires Python 3.10 or newer.

## Reading and writing `.HMI` files

The format core and the checksum solvers are stdlib-only. Run any module from the repository root:

```sh
python -m research.nextion_hmi_binary path/to/project.HMI   # list sections, prove round-trip
```

[`research/nextion_hmi_writer.py`](research/nextion_hmi_writer.py) authors components by
clone-and-patch. It needs an **exemplar** `.HMI` you build once by hand in the pinned editor, holding
one instance of each component type you use — see
[`driving-the-editor.md`](driving-the-editor.md#generating-an-hmi-safely). No exemplar is bundled,
because component bytes are editor- and version-specific.

## Driving the editor (Windows only)

The GUI drivers need a live **Nextion Editor 1.68.1.3034** on Windows plus a few packages:

```sh
python -m pip install -r requirements.txt
```

Optional extras (see [`pyproject.toml`](pyproject.toml)):

```sh
python -m pip install -e ".[trace]"     # frida, for research/trace_nextion_crc.py (x64 Python)
python -m pip install -e ".[solvers]"   # numpy, for a few of the checksum solvers
```

Then, for example, screenshot every page of a project from the maximised editor:

```sh
python research/capture_screenshots.py --title myproject.HMI --pages home,menu,status
```

### Calibration

The GUI drivers assume a **maximised editor on a 1800 x 1130 screen**; the click and crop coordinates
are calibrated to that geometry. On a different screen or layout, **recalibrate** from a fresh
full-screen grab rather than nudging the constants — for the screenshot tool, read the canvas and
page-list rectangles off a raw frame and pass them via `--config`. See the header of
[`research/capture_screenshots.py`](research/capture_screenshots.py) and
[`research/README.md`](research/README.md).
