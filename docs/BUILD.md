# Quellcode-Build und Tests (ohne Freigabe/Signierung)

Voraussetzungen: Python 3.11+, `Pillow` und für Tests `pytest`; für den nativen Wayland-Client zusätzliche Ubuntu/Debian-Pakete `libgtk-3-dev`, `libgtk-layer-shell-dev`, `libwayland-dev`, `wayland-protocols`, `libwayland-bin`, `pkg-config`, `gcc`, `make`.

```bash
python3 -m pip install --user Pillow pytest
python3 tools/restore_generated_assets.py
python3 -m compileall -q spot_os
python3 -m spot_os.package --output ./build
make -C spot_os/native
```

Der Generator kontrolliert die SHA256-Werte der beiden PNGs, baut die ursprüngliche 800×480×24-Bit-TGA aus den RGB-Pixeln und akzeptiert nur den ursprünglichen TGA-Hash. Die grosse generierte Datei wird deshalb nicht in Git dupliziert.

Die ZIP aus `./build` ist ein **unsigniertes Entwicklerartefakt**; niemals als offizielle stabile SPOT-OS-Version installieren oder über die signierte OTA-Schiene verteilen.

Unit-Tests werden unter `spot_os/tests/` mitgeliefert. Manche historischen App-/Systemtests sind nur in der kombinierten privaten Entwicklungsumgebung ausführbar. Die öffentliche CI führt deshalb ausdrücklich nur die auf einem generischen Ubuntu-Runner unterstützten, selbständigen Quell- und Paketprüfungen aus; Hardware, SD-Karte, Touch, DSI und Lepton können dadurch nicht als getestet gelten.

**Erstinstallation:** Die endnutzertaugliche, inklusive Treibervorbereitung abgesicherte Anleitung steht im [öffentlichen SPOT-Gesamtprojekt](https://github.com/Koco-008/SPOT). Für laufende Geräte nur [offizielle signierte Releases](https://github.com/Koco-008/SPOT-OS-Releases/releases) installieren.
