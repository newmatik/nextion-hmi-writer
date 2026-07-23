"""Records calls to the CRC helpers in the running Nextion Editor.

The tool changes no project file. Optionally, exactly one existing HMI file can be opened through the
guarded Editor controller once the hooks are in place. The trace ends after a fixed timeout and caps
the number of logged calls.

Editor 1.68.1.3034 loads ``achmi.bin``. The two verified helpers sit in it at:

* RVA 0x7930: byte-wise input, a single byte is processed as a 32-bit word;
* RVA 0x7990: aligned 32-bit words.

Run with the x64 Python runtime for which ``frida`` was installed::

    python -m research.trace_nextion_crc --pid 1234 --seconds 20 --output trace.jsonl
"""

from __future__ import annotations

import argparse
import atexit
import json
import subprocess
import sys
import time
from pathlib import Path


def _hook_source(limit: int) -> str:
    return f"""
const module = Process.getModuleByName('achmi.bin');
const calls = [
  {{ name: 'bytes', address: module.base.add(0x7930) }},
  {{ name: 'words', address: module.base.add(0x7990) }}
];
let count = 0;

for (const item of calls) {{
  Interceptor.attach(item.address, {{
    onEnter(args) {{
      if (count >= {limit}) return;
      this.active = true;
      count++;
      const sp = this.context.esp;
      const length = sp.add(4).readU32();
      const pointer = this.context.edx;
      this.record = {{
        event: 'crc',
        number: count,
        helper: item.name,
        module_base: module.base.toString(),
        caller: sp.readPointer().sub(module.base).toString(),
        init: this.context.ecx.toUInt32(),
        pointer: pointer.toString(),
        length: length
      }};
      if (length <= 96 && !pointer.isNull()) {{
        try {{
          this.record.data_hex = new Uint8Array(pointer.readByteArray(length)).reduce(
            (s, b) => s + ('0' + b.toString(16)).slice(-2), '');
        }} catch (_) {{}}
      }}
    }},
    onLeave(result) {{
      if (!this.active) return;
      this.record.result = result.toUInt32();
      send(this.record);
    }}
  }});
}}
send({{event: 'ready', module_base: module.base.toString()}});
"""


def _trigger_open(path: Path, python_exe: str) -> subprocess.CompletedProcess[str]:
    code = (
        "from research.editor_control import open_file,set_run_limits;"
        "set_run_limits(35,80);"
        f"open_file({str(path.resolve())!r})"
    )
    return subprocess.run(
        [python_exe, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Log Nextion CRC calls with hard limits")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--open", dest="open_path", type=Path)
    parser.add_argument(
        "--controller-python",
        default=sys.executable,
        help="Interpreter used to run the editor controller subprocess (default: this interpreter). "
        "Override it if frida and the GUI stack live in different Python installs.",
    )
    args = parser.parse_args(argv)

    try:
        import frida
    except ImportError as exc:
        raise SystemExit("frida is missing in this Python runtime") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    session = frida.attach(args.pid)
    # Detach on any exit path, including create_script()/load() raising below.
    atexit.register(session.detach)
    script = session.create_script(_hook_source(args.limit))
    ready = False
    records = 0

    with args.output.open("w", encoding="utf-8") as stream:
        def on_message(message, _data) -> None:
            nonlocal ready, records
            payload = message.get("payload") if message.get("type") == "send" else message
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            stream.flush()
            if isinstance(payload, dict) and payload.get("event") == "ready":
                ready = True
            if isinstance(payload, dict) and payload.get("event") == "crc":
                records += 1

        script.on("message", on_message)
        script.load()
        deadline = time.monotonic() + 5.0
        while not ready and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready:
            raise SystemExit("CRC hooks did not become active in time")

        trigger = None
        if args.open_path is not None:
            trigger = _trigger_open(args.open_path, args.controller_python)
            stream.write(
                json.dumps(
                    {
                        "event": "trigger",
                        "returncode": trigger.returncode,
                        "stdout": trigger.stdout,
                        "stderr": trigger.stderr,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            stream.flush()

        end = time.monotonic() + args.seconds
        while time.monotonic() < end and records < args.limit:
            time.sleep(0.1)

    print(f"Trace: {args.output} ({records} CRC calls)")
    if args.open_path is not None and trigger is not None and trigger.returncode != 0:
        print(trigger.stderr, file=sys.stderr)
        return trigger.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
