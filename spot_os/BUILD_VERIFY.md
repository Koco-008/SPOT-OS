# Reproduzierbarer Build und PrÃ¼fnachweise OS 0.1.7

Nur Entwicklungscheckout/Linux-CI, niemals Produktiv-Pi. OS-0.1.4-Referenz:
`c8c10f2c016a6a8a46ac313b75a51d8d79979889`. App bleibt 0.28.13,
verÃ¶ffentlichter App-Commit `29fd45d32eb2be7bf759cf82876ec7d373cade70`.
ZusÃ¤tzliche ursprÃ¼ngliche signierte App-0.28.12-Fixture bleibt als Regression.

Mit den AbhÃ¤ngigkeiten aus den Workflows, jede Zeile separat:

```sh
make -C spot_os/native
python spot_os/tests/run_lepton_source.py
python spot_os/tests/run_provision_security.py
QT_QPA_PLATFORM=offscreen python -m pytest tests spot_os/tests -q
python spot_os/tests/run_wayland.py
python spot_os/tests/run_ordering.py
python spot_os/tests/run_units.py
python spot_os/tests/run_startlimit.py
python spot_os/tests/run_admin.py
python spot_os/tests/run_ota_sandbox.py
python spot_os/tests/run_signed_app.py
python spot_os/tests/verify_build.py
python spot_os/tests/audit_immutability.py
```

`verify_build.py`: zwei unabhÃ¤ngige ZIP_STORE-Builds, feste Zeitstempel/LF,
identische Bytes/SHA256, CRC und jeder extrahierte Inhalt geprÃ¼ft. Nachweise
unter `outputs/spot-os-0.1.7/`, Quell-Commit/Tree und Dirty-Status im externen
Buildnachweis. Keine Signatur wird beim gewÃ¶hnlichen Build erfunden. Die vier
offiziellen Release-Artefakte entstehen erst im geschÃ¼tzten Ã¶ffentlichen Signierer.

Linux prÃ¼ft echte Root-EigentÃ¼mer, FIFO-/Symlink-Manipulation, konkurrierende
Prozesssperren, systemd-Ordering/Startlimit/PAM/SSH, Wayland und Root-CLI in
eindeutig benannten Wegwerf-DateibÃ¤umen/-Units. Lepton-Build prÃ¼ft den festen
Archivhash, native SDK-/Streamer-/Radiometrie-/CCI-Tests und dtc-Overlay-Build.
Dieser Lauf installiert kein Modul und prÃ¼ft keinen ARM64-Kernelmodul-Build.
Provisionierungsfehler und Kernel-/Header-Antworten sind simuliert, ebenso die
Zielwurzel der echten Installer-/Journal-/OTA-/Rollback-Integration.

Die vollstÃ¤ndige App-/OS-Suite umfasst Signaturen, IPC, Rootdienst, UI,
Aufnahmen/Busy-ZustÃ¤nde, Kamera-Reader und Wiederherstellung. Der zusÃ¤tzliche
OTA-Test verwendet den echten verÃ¶ffentlichten OS-0.1.4-Installer und verweigert
jeden Aufruf der neuen Provisionierung. Treiber-, App-, Konfigurations- und
Lock-Inode-/Mtime-Sentinels bleiben bei Erfolg und Fehlerrollback erhalten.
Das Immutability-Audit friert alle App-0.28.13-Dateien einschliesslich bisheriger
UI-Allowlist gegen den verÃ¶ffentlichten Commit ein.

Fehlende GerÃ¤teabnahme bleibt offen: echte Pi-5-Kompilierung, Neu-Provisionierung,
Overlay-Boot, DSI/Touch, CCI-Kamera, tatsÃ¤chliche Touch-OTA und Test-SD-Rollback.
Automatisierte Ergebnisse ersetzen diese Hardwarebelege nicht.

Neu: OTA_RELIABILITY_016.md beschreibt die echte Namespace-/PID1-Matrix,
deren Plattform-/Health-Doubles sowie die weiterhin offenen Geräteprüfungen.
MIGRATION_014_016.md dokumentiert den ausdrücklich administrativen Übergang.
