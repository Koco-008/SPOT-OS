# Kontrollierte Lepton-Provisionierung ab OS 0.1.5

## Architektur und unterstützter Stand

`lepton_provision.probe` liest Inventar, Attestation und den vorhandenen
Producer-/G_FMT-Bereitschaftsnachweis. Keine Formatverhandlung, kein S_FMT,
kein CCI-Schreibzugriff, kein read() und kein Dienststart im Preflight.
`Provisioner` ist ausschliesslich im administrativen CLI erreichbar. Bootmanager,
Session, Update-Socket und produktiver OTA-Backend importieren ihn nicht.

Neue Einrichtung derzeit nur: Raspberry Pi 5, Debian 13, aarch64,
**6.18.50+rpt-rpi-2712**, exakt passende Header einschliesslich Module.symvers,
kernel.release und Kbuild-kernelrelease. Dies ist eine bewusst enge
Zulassungsliste, keine Behauptung neuer Hardwaretests. Andere Kernel werden
gesperrt; eine Erweiterung benötigt eigenen Build-/Gerätenachweis. Kein
Kernelwechsel als vermeintliche Header-Reparatur.

Quelle: [Lepton-Pi5 20be84ea](https://github.com/Koco-008/Lepton-Pi5/tree/20be84ea89c030822159ba70934bb66c3a1d45e6),
Archiv `https://codeload.github.com/Koco-008/Lepton-Pi5/zip/20be84ea89c030822159ba70934bb66c3a1d45e6`,
SHA256 `388926f3b55422a645c289ed3cfee954ff19a2a588d48a8ce48dbe1dd1ef7cbf`.
Ein geändertes Archiv wird abgelehnt, auch wenn GitHub den gleichen URL liefert.
Es gibt keinen Fallback auf main oder eine ungeprüfte Prüfsumme.

Der historische v1.0.0-Installer pinnt stattdessen **f031edf2** und ist hier
ausdrücklich keine Installationsquelle. `tools/setup_lepton_dkms.sh` im
App-Projekt ist eine historische Migration eines bereits installierten Moduls,
kein Fresh-Installer. Er bleibt zusammen mit der App unverändert. Sein geprüftes
Modul-Dateilayout wird im neuen OS-Modul wiederverwendet. Das Upstream-Skript
würde Dienste stoppen, Loopback entladen und Dateien unjournalisiert schreiben;
deshalb übernimmt OS dessen Ziele/Baureihenfolge, führt es aber nicht aus.

## Zustände / CLI

| Zustand | Aktion |
| --- | --- |
| `compatible` | Vollständiger No-op, auch bei erneuter administrativer Ausführung. |
| `missing` | Explizites Setup darf feste Quellen holen, bauen und vorbereiten. |
| `incomplete` | Nur eigener Journalstand darf bewusst fortgesetzt/zurückgenommen werden. |
| `incompatible` | Fremden Bestand nicht ersetzen; administrativ untersuchen. |
| `unsupported` | Hardware/OS/Kernel nicht zugelassen; keine Mutation. |

`driver-status` ist read-only und vor Aktivierung nutzbar. Ausführliche JSON-Gründe
enthalten z.B. `unsupported_kernel`, `matching_headers_missing_or_wrong`,
`source_integrity_failed`, `operation_busy`, `existing_driver_refused`,
`owned_files_changed`, `capture_format_not_ready`, `radiometry_contract_unverified`.
CLI: Erfolg 0, Fehler 1. Ein vorbereiteter notwendiger manueller Reboot ist
`ok=true, pending=true, reboot_required=true`, ausdrücklich noch keine Abnahme.

Auf bereits installiertem OS, jede Zeile einzeln:

```sh
sudo spot-os driver-status --user admin
sudo spot-os provision-lepton --user admin
sudo spot-os provision-lepton --user admin --resume-lepton
```

**Diese Befehle gehören nicht zum OTA-Test auf dem funktionierenden BMA-Pi5.**

## Neues Gerät ohne installiertes SPOT OS

Zuerst Raspberry Pi OS Debian 13/64 Bit, Display/Touch, SSH-Rettungszugang,
signierte App 0.28.13 einrichten. Die App einmal regulär starten/beenden, damit ihre stabilen
Launcher-Sperren vorhanden sind. Kamera muss hierfür noch nicht funktionieren.
Ein reiner `provision-lepton`-Aufruf funktioniert auch vor der App-Installation,
solange noch kein Launcher-Präfix besteht. OS-Installation selbst benötigt App,
Qt/Wayland, Display und SSH wie bisher; diese Prüfungen werden nicht abgeschwächt.

Nur nach Veröffentlichung: die vier signierten OS-Assets und den unabhängig
bestätigten PUBLIC-Key auf dem administrativen Rechner prüfen. OS-Key-SHA256:
`b142b246c22fd6f56f4207b86bbd5dcef09b122d228af81c0c51130d4309f907`.
Keine privaten Schlüssel auf den Pi. Signatur/ZIP vor dem Import irgendeines
Paket-Codes mit System-Python/cryptography prüfen, siehe historischer
`BOOTSTRAP_014.md` für den identischen isolierten Prüfmechanismus; Version/Tag/
Dateinamen müssen dabei **0.1.5/os-v0.1.5** sein. Danach in ein neues root-eigenes
Verzeichnis unter `/root/spot-os-0.1.5/` entpacken (keine vorhandene Installation
überschreiben). Kein Setup aus einem beschreibbaren Benutzerverzeichnis.

Ein expliziter administrativer Ablauf vor dem gewöhnlichen OS-Preflight:

```sh
sudo --preserve-env=SSH_CONNECTION python3 -I /root/spot-os-0.1.5/spot_os/setup_entry.py setup --provision-lepton --user admin
```

Dieser Aufruf prüft Root-Eigentümer, Rechte, fehlende Symlinks und PACKAGE.json,
ohne installiertes `spot-os` zu benötigen. Er installiert nötige Build-/Runtime-
Pakete, beim kombinierten Fresh-Setup auch `packages.txt` für OS, und nur
`linux-headers-$(uname -r)` (keine generischen Kernel-Metapakete).
APT-Simulation muss ohne Upgrades, Entfernungen und Kernel-/Firmwareänderungen
auskommen; andernfalls Abbruch. Paketzustände stammen aus signierten APT-Quellen.
Quellarchiv SHA256 wird geprüft, Pfade werden begrenzt, die vier internen VoSPI-
Symlinks werden als normale Dateien materialisiert, dann feste Make-Ziele gebaut.

Lepton-DKMS-Version `1.0.0.spot20be84ea`: add/build/install nur für den laufenden
Kernel, ohne `--force`, `AUTOINSTALL=no`. Ein späteres Kernel-Upgrade muss erneut
bewusst qualifiziert werden. Distro-v4l2loopback-dkms behält seine eigenen Paket-
Hooks; dies ist kein Versprechen, beliebige Paket-Hooks transaktional rückgängig
machen zu können. Der Installer aktualisiert selbst keinen Kernel.

GPIO-Vertrag SPI0 CS0 / VSYNC GPIO17 / I2C1 wird für den nächsten Boot vorbereitet.
Einmaliger **manueller** Reboot ist nur für ein neu gesetztes Overlay/Gruppen nötig:

```sh
sudo reboot
```

Danach denselben bewusst gestarteten Ablauf fortsetzen:

```sh
sudo --preserve-env=SSH_CONNECTION python3 -I /root/spot-os-0.1.5/spot_os/setup_entry.py setup --provision-lepton --resume-lepton --user admin
sudo spot-os preflight --user admin
sudo spot-os check
sudo spot-os static-check
```

Fortsetzung prüft Journaldateien, Kernel/Benutzer, echte Producer-Generation,
Y16/Rohformat, Farbausgabe, radiometrischen Status `kelvin_x100` unter dem
gemeinsamen CCI-flock sowie dieselbe Lock-Inode im Streamer. Es wird kein
Streamer-Neustart als Reparatur versucht. Erst danach läuft der normale
OS-Installer mit seinen vollständigen Voraussetzungen. Aktivierung der neuen
Appliance bleibt der bisherige bewusste `sudo spot-os activate --user admin`-
Schritt. Nach dem eigenen manuellen Boot `sudo spot-os doctor` ausführen.

Nur bei einer NEU provisionierten Installation setzt der eigene CCI-Drop-in
`Restart=no`: ein fehlender Sensor führt zu einem sichtbaren Dienstfehler statt
zu einer dauernden systemd-Reparaturfolge. Die bestehende begrenzte App-Recovery
bleibt verfügbar. Bei bereits kompatiblen Installationen wird kein Drop-in
verändert und die bisherige Treiber-Dienstpolitik vollständig erhalten.

## Journal, Abbruch und Wiederherstellung

Eigener Vorgang in `/var/lib/spot-os/lepton-provision.json`, gesicherte Original-
dateien in den vorhandenen content-addressierten `backups/`, historische
Journale in `history/`, Quellbäume in `lepton-source/`. Datei- und Bootänderungen
verwenden exakt `Installer.Journal`, keinen zweiten Dateirestaurationsmechanismus.
Status bleibt über Abbruch/Boot erhalten. Paketplan, Phase, Hash, Kernel und
Bestätigung sind nachvollziehbar, keine Tokens/Passwörter werden erfasst.

Vor Mutation: globale `installer.lock`, dann vorhandene Launcher-`update.lock`
und `app.lock` in der bisherigen Reihenfolge. Gleichzeitiges Setup/OTA wird
abgewiesen. CCI-Verzeichnis root-eigen 0755, Lock root:i2c 0660; die Inode wird
nicht ersetzt. Bestehender kompatibler CCI-Bestand wird überhaupt nicht geändert.

Bei `awaiting-reboot`/`verifying`: Ursache prüfen, dann explizit
`--resume-lepton`. Bei teilweise fehlgeschlagenem Build/Setup nicht blind erneut
installieren. Journal und APT-/DKMS-Logs sichern und prüfen:

```sh
sudo cat /var/lib/spot-os/lepton-provision.json
sudo dkms status
sudo journalctl -b -u lepton-streamer.service --no-pager -n 150
sudo spot-os rollback-lepton
```

Rollback verweigert einen aktiven Streamer oder ein geladenes Lepton-Modul.
Auf einer isolierten neuen/Test-Installation müssen diese zuerst bewusst durch
den Administrator stillgelegt werden. Niemals den funktionierenden Haupt-Pi
dafür verändern. Eigene DKMS-Registrierung wird gezielt entfernt, gesicherte
kontrollierte Dateien wiederhergestellt; Fremdänderungen an Elternpfaden führen
zum Abbruch. Bereits entfernte DKMS-Registrierungen vor Wiederholung prüfen.
APT-Pakete, Accounts/Gruppenergänzungen und Quell-/Buildlogs bleiben erhalten.
Keine automatische Paketentfernung, kein automatischer Reboot/Reset.

**Grenze:** APT-/DKMS-Hooks und Kontodatenbank sind keine atomare Transaktion.
Bei unterbrochenem dpkg zuerst dessen tatsächlichen Zustand administrativ prüfen
(`/var/log/apt`, `/var/log/dpkg.log`, DKMS make.log). Kein Erfolg behaupten, solange
Paketmanager/Rechte/Live-Format nicht bestätigt sind. Die sichere vollständige
Rückkehr bei unklarem Paketstatus ist das vorher erstellte SD-Abbild. Nach
Datei-Rollback auf derselben SD bleibt das Ausgangs-Overlay bis zum nächsten
bewussten Boot im laufenden Kernel wirksam.

## Fremdsoftware / öffentliche Nachbaubarkeit

Das OS-ZIP enthält **keine** Lepton-/FLIR-Quellen oder -Binaries. Sie werden erst
beim autorisierten Setup aus dem fest geprüften öffentlichen Stand geladen.
FLIR-SDK-Dateiköpfe enthalten eine bedingte Erlaubnis für Source-/Binary-
Weitergabe mit Erhalt der Copyright-/Haftungshinweise und ohne Namenswerbung;
daneben stehen Proprietary-/Export-Hinweise. Total-Phase-Dateien haben eigene
Bedingungen, FTDI-Header ebenfalls. Der Treiber nennt GPL als Modul-Lizenz, das
Pi5-Overlay `GPL-2.0 OR MIT`; ein einheitliches Top-Level-LICENSE fehlt im geprüften
Upstream. Daraus folgt keine pauschale MIT-Lizenz für das gesamte Treiberarchiv.

Die lokalen Originalquellen bleiben mit ihren Hinweisen erhalten. Das Setup
legt SDK-Dateiköpfe zusätzlich unter
`/usr/local/share/doc/lepton-pi5/UPSTREAM-NOTICES.txt` ab. Wer Geräte/kompilierte
Treiber weiterverteilt, muss die jeweiligen Bedingungen anhand der Original-
dateien prüfen und alle erforderlichen Hinweise/Quellangebote beilegen. Die
SPOT-MIT-Lizenz ersetzt diese Rechte nicht; keine fremden Binärbibliotheken
werden heruntergeladen oder in den OS-Release aufgenommen.
