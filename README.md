# SPOT OS – öffentliches Quellcode-Repository

**SPOT OS 0.1.7 Stable · eingefrorener BMA-Stand vom 5. November 2026**

SPOT OS ist die spezialisierte Raspberry-Pi-5-Systemumgebung der offenen [SPOT-Wärmebildkamera](https://github.com/Koco-008/SPOT). Sie startet die SPOT-App mit einer dedizierten 800 × 480-Touchoberfläche, integriert die Lepton-Kamera, ein Splashscreen-/Wayland-System, einen Health-/Recovery-Manager und einen unabhängig signierten OTA-Updatepfad.

Dieses Repository veröffentlicht einen **bereinigten Quellcode-Snapshot** der für SPOT OS 0.1.7 geprüften privaten Quelle – **ohne private Git-Historie, Signierschlüssel oder CI-Staging-Artefakte**.

## Was gehört wohin?

| Repository | Zweck |
| --- | --- |
| [SPOT](https://github.com/Koco-008/SPOT) | Gesamtprojekt, Einkaufsliste, Bau und Ersteinrichtung |
| **SPOT-OS (hier)** | Öffentlich lesbarer OS-Laufzeitcode, Konfigurationen, native Quellen und Tests |
| [SPOT-App](https://github.com/Koco-008/SPOT-App) | Privates App-Entwicklungsrepository; dort entstand auch der originale OS-Code |
| [SPOT-Releases](https://github.com/Koco-008/SPOT-Releases/releases/tag/v0.31.1) | **Signierter App 0.31.1 Stable** |
| [SPOT-OS-Releases](https://github.com/Koco-008/SPOT-OS-Releases/releases/tag/os-v0.1.7) | **Signierter OS 0.1.7 Stable**, einziger offizieller OS-Updatekanal |

**Für ein Gerät immer die offiziellen signierten Release-Assets verwenden.** Das automatisch von GitHub angebotene „Source code (zip)“ oder ein lokaler Build aus diesem Repository sind **keine signierten OTA-Updates**. Der private Ed25519-Schlüssel wird nicht veröffentlicht. `SPOT-OS` hat bewusst keinen Release-Publisher.

## Quellcode und Struktur

- [spot_os/](spot_os/) – Python-Laufzeitcode, Systeminstallation, Session, Recovery, OTA, Lepton-Provisionierung, Konfigurationen.
- [spot_os/native/](spot_os/native/) – C-Quellen und Wayland-Protokoll für den unabhängigen Splash-/Abdeckclient.
- [spot_os/artwork/](spot_os/artwork/) – originale und vorbereitete PNG-Grafiken; die grosse TGA-Bootgrafik wird aus dem PNG **verlustfrei und mit festem SHA256** wiederhergestellt.
- [spot_os/tests/](spot_os/tests/) – Quelltests; einige historische Integrationstests benötigen zusätzlich die private App-Testumgebung.
- [docs/BUILD.md](docs/BUILD.md) – öffentlicher Build-/Testablauf.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) – Aufbau und Grenzen.
- [docs/PROVENANCE.md](docs/PROVENANCE.md) – Herkunft des stabilen Quellstands und Sicherheitsmodell.

## Quellstand lokal prüfen und bauen

Benötigt wird Python 3.11+ mit Pillow, für Tests ausserdem pytest und bei Bedarf weitere Pi-spezifische Systempakete. Ein Build auf dem PC ist **keine** Hardwarefreigabe.

```bash
git clone https://github.com/Koco-008/SPOT-OS.git
cd SPOT-OS
python3 -m venv .venv
. .venv/bin/activate
python -m pip install Pillow pytest
python tools/restore_generated_assets.py
python3 -m spot_os.package --output ./build
```

*Hinweis:* Ältere unverändert übernommene Dateien wie `spot_os/README.md` und `spot_os/INSTALL.txt` dokumentieren teilweise die Situation **vor** der Stable-Veröffentlichung. Für den aktuellen Stand gelten diese Einstiegs-README, `docs/PROVENANCE.md` und der offizielle signierte Release.

Das Skript stellt `spot_os/artwork/splash.tga` aus den unveränderten offiziellen PNG-Pixeln her und kontrolliert die ursprüngliche SHA256. Kein Netz- oder GitHub-Token wird dafür benötigt. Siehe [Builddetails](docs/BUILD.md).

## BMA-Stable-Freeze / Hardwaregrenzen

- OS-Version: **0.1.7**, App-Version: **0.31.1**. Beide wurden am BMA-Prototyp getestet.
- Hardware: **Raspberry Pi 5, FLIR Lepton 3.5, Raspberry Pi Camera Module 3, Waveshare 5" DSI 800×480**.
- Das Lepton-Erst-Provisioning ist bisher **nur** für den explizit getesteten Kernel `6.18.50+rpt-rpi-2712` auf Debian 13/aarch64 zugelassen. Andere Kernel erfordern eine separate Freigabe.
- Eine vollständige Erstinstallation auf einem fabrikneuen Pi/SD-Image wurde noch **nicht unabhängig** von Dritten validiert. Siehe [SPOT-Nachbauanleitung](https://github.com/Koco-008/SPOT).
- App, Driver, OS und deren Signaturen bleiben bei der öffentlichen Quellcodeveröffentlichung unverändert. Spätere neue Funktionen sind **nicht Bestandteil der BMA**.

## Sicherheit und Lizenzen

Das eigene SPOT-OS-Programm steht unter der [MIT-Lizenz](LICENSE). Drittsoftware und [Wayland-Protokolltexte](spot_os/NOTICE.md) behalten ihre jeweils eigenen Hinweise und Bedingungen. Das [Lepton-Pi5-Treiberprojekt](https://github.com/Koco-008/Lepton-Pi5) ist ein getrenntes externes Projekt; dessen Lizenzbedingungen werden nicht durch die SPOT-Lizenz ersetzt.

**Keine privaten Schlüssel, WLAN-Zugangsdaten, Gerätenotizen oder personenbezogenen Logdateien als Issues anhängen.** [Security](SECURITY.md) · [Contribution](CONTRIBUTING.md).
