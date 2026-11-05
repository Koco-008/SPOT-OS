# Architektur von SPOT OS 0.1.7

**Plattform:** Raspberry Pi 5, Raspberry Pi OS / Debian 13 64-bit, Labwc/Wayland, Plymouth, GTK3-Layer-Shell und ein eigenständiger systemd-Sessionmanager.

1. Bootgrafik: Firmwarelogo, Plymouth und ein unabhängiger Layer-Shell-Client überbrücken den Start der GUI.
2. Systemd und PAM: die dedizierte Sitzung startet die bestehende SPOT-App über ihren versionierten Launcher; normale Desktop- und Autostartkomponenten werden ausgeblendet.
3. Bereitschaft: unabhängige GUI-/Camera-Healthprüfungen und Bootbestätigung verhindern voreiligen Start-/Update-Erfolg.
4. Imaging: Lepton-Pi5 liefert radiometrische 160×120-Y16-Daten; der Treiber ist ein eigenes Drittsoftwareprojekt.
5. Updates: App-OTA und OS-OTA sind getrennt. Der OS-Service prüft die Ed25519-Signatur und Kompatibilität; die App hat ihren eigenen Prüfschlüssel.
6. Recovery: begrenzte Neustartversuche, atomarer Wechsel und Wiederherstellung statt permanentem Start-Loop.

**Code:** `spot_os/manager.py`, `installer.py`, `update_*.py`, `lepton_provision.py`, `native/`, `config/`, `artwork/`.

**Grenzen:** Kein Ersatz für Raspberry Pi OS; kein garantiertes Booten mit abweichendem Kernel, Display oder fremdem Treiber. Ein OS-Build auf einem PC ist weder ein Hardwaretest noch ein offizielles OTA.
