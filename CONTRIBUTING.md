# Contributing

Thanks for your interest. This project is a reverse-engineering reference and its supporting tooling,
so contributions are held to one standard above all: **a claim is only as good as its proof.**

## Repository layout

- **Root `*.md`** — the reference. This is the spec: the byte-level `.HMI` format, plus
  [`driving-the-editor.md`](driving-the-editor.md) and [`methodology.md`](methodology.md).
- **[`research/`](research/README.md)** — the tooling that decoded and drives the format: the
  canonical reader/writer, the checksum work, the editor drivers and the Frida trace. Research code,
  not a polished library — but kept inspectable so results can be re-derived and challenged.
- **[`samples/`](samples/README.md)** — small measured data and calibration. The multi-gigabyte
  sample `.HMI` files are not shipped and are not needed.

## Ground rules

- **Confidence is explicit.** Mark new findings **Confirmed** (verified against the pinned editor,
  ideally byte-for-byte) or **Uncertain** (inferred, partial, or reported). Do not present an
  inference as a fact.
- **Pin the editor version.** Container and `.TFT` details are version-sensitive. Everything here is
  tied to **Nextion Editor 1.68.1.3034**; state the version behind any new observation.
- **The canonical modules must stay proven.** [`research/nextion_hmi_binary.py`](research/nextion_hmi_binary.py)
  and [`research/nextion_hmi_checksum.py`](research/nextion_hmi_checksum.py) are the executable form of
  the spec. Any change to the checksum code must keep the test vectors green; the reader/writer is held
  to its byte-exact round-trip against reference `.HMI` files:

  ```sh
  python -m unittest research.test_vectors
  ```

  The suite parses the vectors out of [`test-vectors.md`](test-vectors.md), so the doc and the code
  cannot drift apart — update both together.
- **No vendor or customer binaries.** Do not commit `.HMI`, `.TFT`, `.zi` or panel screenshots. The
  `.gitignore` already excludes the sample and capture output; keep it that way.
- **English, with one deliberate exception.** Prose and identifiers are English. The German-locale
  editor GUI strings that the drivers match (`"Speichern"`, `"Öffnen"`, `"Ja"`, `"Nein"`, …) are data,
  not prose — translating them would break the automation, so they stay and are glossed in English.
  Running against an English-locale editor means extending those caption lists, not editing the
  surrounding text.
- **Style.** `ruff check .` is the informational style pass (config in `pyproject.toml`); it never
  blocks a merge, but keep new code clean.

## Licensing of contributions

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
