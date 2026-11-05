# SPOT OS 0.1.7 - bestätigter OTA-Abschluss

Aktuell: RELEASE_017.md, HANDOVER_017.md und OTA_TEST_017.md. 016 auf 017 ohne
Bridge; Stable-/Hardwarefreigabe des neuen Builds bleibt ausdrücklich offen.

## Historische Architektur und 0.1.6-Ausgangslage

# SPOT OS 0.1.6 – zuverlässige OTA-Transaktionen

App 0.28.13 und bestehender Lepton-Treiber bleiben unverändert.
[Releasevorbereitung](RELEASE_016.md), [Fehleranalyse und Rechte](OTA_RELIABILITY_016.md),
[einmaliger Übergang aus 0.1.4](MIGRATION_014_016.md), [Hardware-/OTA-Test](OTA_TEST_016.md),
[Abnahme](ACCEPTANCE.md), [reproduzierbare Prüfung](BUILD_VERIFY.md).

Kein offizieller Release und keine Signierung in diesem Entwicklungsauftrag.
Der unveränderte alte 0.1.4-Updater benötigt eine ausdrückliche administrative
Vorbereitung aus dem später signierten Paket. Keine automatische Migration behauptet.
Kein Gerätezugriff durch Codex; Hardwareabnahme bleibt offen.
Neue Geräte verwenden weiterhin [Lepton-Provisionierung](LEPTON_PROVISIONING.md).

**Historische Dokumentation:** Die folgenden Abschnitte dokumentieren frühere
Entwicklungsstände. Der reale 0.1.5-OTA scheiterte laut Betreiber und wurde auf
0.1.4 restauriert; frühere Softwareprüfungen sind kein Hardware-OTA-Nachweis.

# SPOT OS 0.1.5 â€“ kontrollierte Lepton-Erstinstallation und OTA

Aktueller bestÃ¤tigter Bestand: App 0.28.13 / OS 0.1.4 mit funktionierendem Treiber.
Neue GerÃ¤te: [Lepton-Setup](LEPTON_PROVISIONING.md). Bestehende GerÃ¤te:
[Touch-OTA und Test-SD-Rollback](OTA_TEST_015.md). [Release](RELEASE_015.md),
[Signierfreigabe](OS_SIGNING_SETUP.md), [Abnahme](ACCEPTANCE.md).
Die App und vorhandene kompatible Treiber bleiben unverÃ¤ndert. Kein Pi-Zugriff
durch Codex. Der neue Stand benÃ¶tigt noch reale Hardwareabnahme.

**Historisch:** Alle folgenden Versionsabschnitte beschreiben frÃ¼here StÃ¤nde.
BOOTSTRAP_014.md bleibt nur die historische 0.1.3â†’0.1.4-Anleitung.

# SPOT OS 0.1.3 / producer-gate-1

Aktueller Upgrade-Kandidat auf der installierten **0.1.2 / lepton-ordering-1**
(7effe01). BestÃ¤tigte Ausgangslage auf BMA-Pi5: signierte App **0.28.12** auf OS 0.1.2;
Kamera, EnergiemenÃ¼, Standby-Doppeltipp und aktueller Boot sind vom Betreiber bestÃ¤tigt.
Der finale App-Commit ist `adb8aa549393b40c3e00e95423be23f8a3715761`.
OS 0.1.3 prÃ¼ft diese App-Voraussetzung und schÃ¼tzt einen spÃ¤teren Legacy-App-
Rollback, ohne eine zweite privilegierte Recovery oder TreiberÃ¤nderung.

[Release und Grenzen](RELEASE_013.md), [einzelne SSH-Befehle / Rollback](INSTALL.txt).
Keine Installation, kein Merge und kein Pi-Reboot durch Codex. Die neue OS-0.1.3-
Installation ist anschlieÃŸend noch am GerÃ¤t abzunehmen. Die folgenden Abschnitte dokumentieren die vorherigen OS-StÃ¤nde.

# SPOT OS 0.1.2

Aktuelle Revision **lepton-ordering-1**: explizites Streamer-Ordering und begrenzter
Read-only-Nachweis des geÃ¶ffneten Produzenten/160Ã—120-Y16-Formats vor Sessionstart.
Fehler lassen die GUI starten. [Details](LEPTON_ORDERING_012.md),
einschlieÃŸlich des transaktionalen Refreshs Ã¤lterer 0.1.2-Kandidaten.

Separater OS-Kandidat auf dem verifizierten 0.1.1-Commit
`4c31b0f27f7ce9c90bd57d73f31669b63af59719`. Die signierte App **0.28.10**,
SmartFFC, App-Recovery, Lepton-Treiber und Update-VertrauensschlÃ¼ssel bleiben
bytegleich. Kein Deployment, Merge oder GerÃ¤te-Reboot durch diesen Auftrag.

FÃ¼r das aktuell konfigurierte 0.1.1 gelten [INSTALL.txt](INSTALL.txt) und
[RELEASE_012.md](RELEASE_012.md). `upgrade` erhÃ¤lt die aktive Bootkonfiguration,
baut zuerst in einem neuen Versionsordner und ersetzt den Import-Link atomar.
Fehler stellen den exakten bisherigen 0.1.1-Datei-/Journalzustand wieder her.
`rollback-upgrade` ist der RÃ¼ckweg zu 0.1.1; `rollback` bleibt der RÃ¼ckweg zum
ursprÃ¼nglichen Desktop. Alte 0.1-Migrationshistorie wird aufbewahrt.

0.1.2 repariert explizit die Rechte des Ã¶ffentlichen Bootstatus unter
`/run/spot-os` (0711 root, Status 0644), prÃ¼ft QLocalSocket im App-Interpreter
und bindet den privaten Bus an den tatsÃ¤chlichen dbus-run-session-Elternprozess.
Der ursprÃ¼ngliche PAM-/TTY-/Labwc-/Native-Splash-Aufbau bleibt bestehen; es
kommen keine TTY-Pre-/Post-Kommandos hinzu. Eine konservative Root-Bereinigung
kann ausschlieÃŸlich nachgewiesene eigene, bereits schlieÃŸende Sessions von
verwaisten privaten D-Bus-Prozessen befreien. Unbekannte Prozesse blockieren.

Lepton-Pi5 [PR 3](https://github.com/Koco-008/Lepton-Pi5/pull/3) wird separat
verwaltet. Die geprÃ¼fte Version, Build-BestÃ¤tigung, laufende Binary-PrÃ¼fsumme
und derselbe geÃ¶ffnete CCI-Lock-Inode sind Voraussetzungen des Preflights.
Der OS-Installer installiert diesen Treiber **nicht**. GUI- und Kameraerfolg
werden getrennt beurteilt; Kameraausfall startet keinen OS-Recovery-Loop.

## Architektur und tatsÃ¤chliche Grenzen

1. RPi-Firmware/DSI initialisieren. Diese erste Phase kann schwarz bleiben.
2. Raspberry-Pi-Fullscreen-Kernellogo: unkomprimierte 24-Bit-TGA, 800Ã—480,
   224 Farben, Ã¼ber eigenen `add_firmware`-Initramfs-Hook. Die Pixelreihenfolge
   entspricht dem offiziellen `-flip`-Beispiel. Kernelparameter
   `fullscreen_logo=1 fullscreen_logo_name=spot-os.tga`.
3. Plymouth-Script-Theme zeichnet exakt das gleiche PNG auf Weiss, ohne Text,
   Animation oder Fortschritt. `plymouth quit --retain-splash` behÃ¤lt das letzte
   Bild, wÃ¤hrend der DRM-Besitz an die Sitzung Ã¼bergeht.
4. systemd startet eine eigene PAM/logind-Sitzung auf tty1 als `admin` mit
   privatem DBus und Labwc. Keine normale Desktop-Sitzung, kein Panel,
   Benachrichtigungsdaemon, Dateimanager, Terminal oder Desktop-Autostart.
5. UnabhÃ¤ngiger C/GTK3-Client zeichnet das PNG als **layer-shell OVERLAY** Ã¼ber
   der noch startenden App und dauerhaft als BACKGROUND hinter ihr. Er bleibt
   bei App-Ende, Update und Rollback bestehen. Nach einem App-Unmap zeigt der
   Compositor unmittelbar das darunterliegende Logo, auch bevor der Manager reagiert.
6. Ein eigener Python-Startmanager wartet auf `wp_presentation.presented` des
   Logo-Fensters. Erst dann startet er den vorhandenen installierten Launcher.
7. Die unverÃ¤nderte App startet darunter. Der OS-Adapter bestÃ¤tigt GUI-Kriterien;
   nach deren ErfÃ¼llung und Kameraerfolg oder 12 s Kamera-Gnadenfrist wird die
   Abdeckung entfernt. Ein Kamerafehler bleibt als solcher bestehen.

Der Ãœbergang Kernel â†’ Plymouth â†’ Labwc ist **auf dem echten Pi noch zu filmen**.
`reuseOutputMode` reduziert einen unnÃ¶tigen Modeset, garantiert aber kein
unterbrechungsfreies DSI/DRM-Handoff. Ein Compositor-/DRM-Neustart kann schwarz
werden. Headless-CI bestÃ¤tigt nicht das physische Display, dessen Farbwiedergabe
oder die allererste sichtbare Bootgrafik. Es werden keine Bootzeiten erfunden.

Grundlagen: [RPi-Konfiguration](https://www.raspberrypi.com/documentation/computers/configuration.html),
[offizieller Splash-Support](https://github.com/raspberrypi/rpi-splash-screen-support),
[Labwc-Konfiguration](https://labwc.github.io/labwc-config.5.html),
[GTK Layer Shell](https://wmww.github.io/gtk-layer-shell/),
[Wayland-Presentation-Protokoll](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/stable/presentation-time/presentation-time.xml).

## Logo und reproduzierbare Dateien

`artwork/original.png` ist die vom Benutzer benannte Originaldatei
`ChatGPT Image 11. Aug. 2026, 15_32_29.png`; SHA-256:
`96b50e0ac3e59a5792f6eafadb0f2c9bdba7534117bed941e2d2c384050e3cd9`.
Die sichtbare Tinte wird ohne fast transparente Export-Artefakte zentriert,
mit Lanczos skaliert und einmal auf 800Ã—480 Weiss komponiert. Symbol und
Schriftzug werden gemeinsam und proportional skaliert. FrÃ¼hstart-TGA und
PNG enthalten dieselben RGB-Pixel. Alle grafischen Phasen benutzen dieses PNG;
nur der endgÃ¼ltige Fehlerzustand darf einen dezenten SPOT-Wartungshinweis zeigen.

Aus dem Projekt: `python -m spot_os.assets ORIGINAL.png` und
`python -m spot_os.package --output outputs/spot-os-0.1.2`.
Das OS-ZIP ist absichtlich unkomprimiert: gleiche Dateien ergeben auch zwischen
Windows und Linux dieselben ZIP-Bytes, unabhÃ¤ngig von zlib. App-Signaturen oder
App-Releases werden damit nicht erzeugt oder verÃ¤ndert. ZIP und PrÃ¼fsumme
Ã¼ber einen vertrauenswÃ¼rdigen administrativen Weg Ã¼bertragen; die PrÃ¼fsumme ist
IntegritÃ¤tsschutz, keine neue OS-Signatur/OTA-Vertrauenskette.

## Bereitschaft und ZustÃ¤nde

ZustÃ¤nde stehen in `model.py`; laufender Zustand Ã¼ber `spot-os status`.
Die Anbindung wird nur in `SPOT_v27.py`, nur bei Start durch den vorhandenen
Launcher und nur mit Manager-Umgebung geladen. Es werden keine signierten
Release-Dateien verÃ¤ndert. Die OS-Dateien samt `sitecustomize.py` sind root-eigen.
Ein neuer signierter App-Release ist fÃ¼r diese Integration nicht nÃ¶tig.

Die zusÃ¤tzliche Bereitschaft erfordert:

- Unix-Socket mit Modus 0600 in einem 0700-Verzeichnis und frische Manager-Capability;
- Kernel-`SO_PEERCRED` statt einer vom Client behaupteten PID;
- tatsÃ¤chlichen `/proc`-Befehlspfad im aktuellen Release, richtigen Launcher-Elternprozess,
  richtige Prefix-Umgebung und gehaltenen bestehenden `app.lock`;
- QtWayland, compositorseitig bestÃ¤tigtes Fullscreen-Toplevel-Mapping, fertig
  gerenderten Qt-Backingstore, Paint-Ereignisse und zwei durch den
  Qt-Ereignisdispatcher bearbeitete Probe-Ereignisse;
- initialisierte SPOT-Komponenten, aktivierte HauptoberflÃ¤che und abgeschlossene
  bestehende Update-BootprÃ¼fung;
- Fullscreen, 800Ã—480-Fenster, Position (0,0) und 800Ã—480-Screen bei Skalierung 1.

Die App ist wÃ¤hrend dieser PrÃ¼fung vom Logo verdeckt; Qt meldet dann auf Labwc
korrekt `isExposed=False`. Deshalb ist Exposure kein Freigabekriterium: der
unabhÃ¤ngige native Client bestÃ¤tigt genau ein Fullscreen-`spot-app`-Toplevel Ã¼ber
`wlr-foreign-toplevel-management`. Zusammen mit der peer-authentifizierten
Release-/ProzessprÃ¼fung und Qt-Backingstore-Rendering ist dies der Mappingnachweis.
Eine physisch sichtbare App-PrÃ¤sentation kann es unter dem Logo noch nicht geben.
Die Logo-Abdeckung wird dagegen mit dem echten
Wayland-Presentation-Feedback bestÃ¤tigt, nicht per Timer. Die Event-Probe prÃ¼ft
den Dispatcher, nicht einen echten Fingertipp: dieser gehÃ¶rt zur GerÃ¤teabnahme.

GUI und Kamera sind getrennt. Nach 12 s bestÃ¤tigter GUI-Bereitschaft darf die App
mit echter Kamera-Fehleranzeige erscheinen. Die bestehende Handoff-PrÃ¼fung bleibt
strenger, wenn das Update `camera_required` gesetzt hat: sie wird nicht umgangen.
Ohne GUI-Beweis bleibt das Logo bis zum Starttimeout von 100 s; danach erscheint
die SPOT-Wiederherstellungsansicht. Keine Systempfade, Tokens oder Stacktraces
auf dem Display. Diagnosen bleiben im Journal/SSH.

## Updates, Neustart und Fehler

Die OS-Anbindung ergÃ¤nzt vor `AppUpdateController.activate` eine asynchrone
Abdeckung. Erst nach `wp_presentation.presented` ruft sie die ursprÃ¼ngliche
Aktivierung auf. Diese prÃ¼ft AktivitÃ¤t und Launcher-Kontext nochmals. Bei
fehlender AbdeckbestÃ¤tigung bleibt die bisherige App offen. Bei einem frÃ¼hen
Updatefehler kann die alte App die Abdeckung zurÃ¼cknehmen.

`spot_handoff.py` behÃ¤lt allein `update.lock`, verifiziert erneut Ed25519,
SHA-256, Herkunft und Nonce, wartet auf `app.lock`, installiert und startet das
signierte neue Release. Der OS-Manager startet wÃ¤hrend gehaltenem App-/Update-Lock
**keinen zweiten Launcher**. Ein neuer GUI-Prozess erhÃ¤lt die gleiche geschÃ¼tzte
OS-Anbindung Ã¼ber seine geerbte Umgebung. Erst nach Handoff-Abschluss und eigenem
GUI-Beweis wird aufgedeckt. Bei Rollback wartet der Manager auf die vorherige App.
Ein Neustart von Pi oder Compositor ist fÃ¼r App-Updates nicht vorgesehen.

ZusÃ¤tzlich wird die vorhandene `BootReadiness.isVisible`-Bedingung im OS-Adapter
mit einer weiteren UND-Bedingung versehen: Der Manager muss das native Mapping,
fertiges Rendering und Dispatcherbereitschaft bestÃ¤tigt haben, bevor die
ursprÃ¼ngliche BootReadiness `gui_ready` melden kann. Deren Kamera-, Nonce-, PID-
und Commit-PrÃ¼fungen bleiben bestehen. DafÃ¼r wird die noch deaktivierte
HauptoberflÃ¤che nicht als Initialisierungsfehler gewertet; zum Aufdecken ist ihre
Aktivierung nach erfolgreichem Handoff trotzdem zwingend. Fehlender zusÃ¤tzlicher
GUI-Beweis fÃ¼hrt damit zum vorhandenen Handoff-Timeout/Rollback und nicht zu einem
vorzeitigen Commit eines halbfertigen Fensters.

Bei Prozessende zeigt der unabhÃ¤ngige Splash wieder das Logo. Maximal drei
Startversuche je Managerlauf, mit begrenztem exponentiellem Backoff. Danach bleibt die
Wiederherstellungsansicht mit SSH-Zugang. Eine nicht mehr antwortende, noch
lebende App wird nicht gewaltsam durch eine zweite Instanz ersetzt. Auch ein
Recorder, der beim Beenden noch schreibt, behÃ¤lt seinen App-Lock. Keine Locks
werden gelÃ¶scht, kein fremder Prozess per pauschalem `pkill` beendet.

`test-restart` ist ein kontrollierter App-Neustart, nur bei frischer, freier App
ohne Update. Nach bestÃ¤tigter Abdeckung erhÃ¤lt der validierte Launcher SIGTERM
Ã¼ber ein PID-FD. Bei Stop-Timeout bleibt die Wiederherstellung sichtbar; kein
SIGKILL. Da PAM Nachfahren in einen logind-Scope verschieben kann, verbindet
der Sitzungs-Supervisor die Elternprozesse zusÃ¤tzlich mit Parent-Death-Signalen.
Der OS-Adapter fÃ¼hrt SIGTERM durch den Qt-Ereignisdispatcher zur bestehenden
`closeEvent`-Logik, damit diese laufende Datei-/Recorderarbeit kontrolliert
abschliessen kann. Der unverÃ¤nderte Launcher hÃ¤lt dabei weiter den App-Lock.

systemd begrenzt Sitzungsfehler auf drei Versuche bis zum manuellen Reset oder
Neustart, mit je 15 Sekunden Pause. Ein persistenter ZÃ¤hler
begrenzt auf drei nicht bestÃ¤tigte GerÃ¤teboots. Er wird erst nach zehn Sekunden
RUNNING zurÃ¼ckgesetzt. Nach Ãœberschreiten des Limits startet nur die grafische
Wiederherstellung, ohne App-Start; ein root-eigenes Signal ist an die Boot-ID
gebunden. Scheitert bereits DRM/Labwc, kann keine grafische
Recovery garantiert werden; vorhandenes frÃ¼hes Logo/SSH bleiben der RÃ¼ckweg.
`repair` kann den ZÃ¤hler nach Diagnose zurÃ¼cksetzen; kein automatischer Reboot.

## Installation auf BMA-Pi5

Ziel: Pi5, Debian13/aarch64, ein DSI-Display 800Ã—480 bei Skalierung1,
FT5x06-Direkttouch, Backlight 11-0045/rpi_touchscreen_attiny, vorhandene
signierte SPOT-Installation, funktionierender Lepton-Dienst und administrativer SSH-Zugang.
Der letzte bekannte Kernel `6.18.50+rpt-rpi-2712` wird als Hinweis behandelt;
der Installer liest den tatsÃ¤chlichen Kernel und die GerÃ¤te erneut.

Die vollstÃ¤ndigen, getrennten SSH-Einzeiler stehen in [INSTALL.txt](INSTALL.txt):
Paket/Hash prÃ¼fen, eigene Sitzung regulÃ¤r stoppen, Altlast konservativ bereinigen,
QtNetwork und externen CCI-Build separat prÃ¼fen, dann Preflight und `upgrade`.
Frische Systeme verwenden `install`, Ã¤ltere 0.1-ZustÃ¤nde `migrate`. Aktivierung,
Sitzungsneustart und echter GerÃ¤te-Reboot sind getrennte Benutzeraktionen.

`preflight` prÃ¼ft tatsÃ¤chliche Hardware/OS, root, direkten Touch, DRM, Display,
QtWayland **und QLocalSocket im App-Interpreter**, Release-Hashes und stabilen
Launcher, Lepton-/SSH-Dienst und Ã¼berprÃ¼ften gemeinsamen CCI-Inode, freien
SSH-Maintenancezugang, Boot-/BackupkapazitÃ¤t, PaketstÃ¤nde, Initramfs/Plymouth/
Fullscreen-Hook sowie freie App-/Update-Locks. Ein nicht nachweisbar gestoppter
Dienst blockiert Upgrade/Uninstall. Kein anderer Desktop oder SSH wird beendet.
Bei Fresh Install Ã¤ndern Vorbereitung und Aktivierung weiterhin getrennt zuerst
OS-Dateien und anschlieÃŸend den Bootpfad. Das 0.1.1-Upgrade erhÃ¤lt dagegen dessen
bereits aktivierte Bootdateien, ohne diese zurÃ¼ckzubauen oder neu zu erzeugen.

Die Paketliste ist deklarativ; genaue Debian-Revisionsnummern hÃ¤ngen vom
konfigurierten RPi-Repository ab und werden unter `/etc/spot-os/package-lock.json`
erfasst. FÃ¼r spÃ¤tere Images dieses Lockfile und einen Repository-Snapshot verwenden.
Es wird kein ungeprÃ¼ftes SD-Image und kein Benutzerpasswort erzeugt.

## RÃ¼ckkehr zum bisherigen System

Jede kontrollierte Datei wird **vor** der Ã„nderung in einem root-privaten,
SHA-256-geprÃ¼ften Backup gesichert, inklusive Existenz, Symlink, Unix-Modus und
EigentÃ¼mer. Ein unterbrochener Vorgang bleibt als Journal sichtbar. Ein Fehler
wÃ¤hrend Aktivierung restauriert die ursprÃ¼nglichen Dateien und Initramfs-Bytes.
Keine App, DCIM-Datei, Messung, Netzwerkkonfiguration, Kamera-/Lepton-Installation
oder SSH-Konfiguration gehÃ¶rt zu diesem Schreibbereich.

Nach regulÃ¤rem Sitzungsstop Ã¼ber SSH: `sudo spot-os rollback`.
Alternativ stellt `rollback-upgrade` die gesicherte OS-Version 0.1.1 wieder her.
Gleichbedeutend fÃ¼r den Startpfad: `sudo spot-os deactivate`; danach selbst rebooten.
Dies restauriert Bootparameter, Firmware-Splash, Initramfs, Plymouth, Desktop-
Alias/Masken und vorherige Power-Profil-Datei. Die aktuelle App wird nicht abrupt
beendet. Beim nÃ¤chsten Boot startet wieder die vorherige Umgebung.

Nach RÃ¼ckkehr zum Desktop: `sudo spot-os uninstall` entfernt die installierten
OS-Dateien/Units und den CLI-Einstieg. App und Nutzerdateien bleiben erhalten.
Auditbackups unter `/var/lib/spot-os` und leere Verzeichnisse bleiben absichtlich
erhalten; es erfolgt keine rekursive LÃ¶schung. Vorher vorhandene Dateien werden
restauriert. Es wird kein Netzwerkpaket deinstalliert.

Falls eine Konfiguration beschÃ¤digt wurde und keine Messung/App lÃ¤uft:
`sudo --preserve-env=SSH_CONNECTION spot-os repair --user admin`.
Unterbrochene Journale werden zuerst restauriert; vorbereitete/aktive eigene
Dateien werden aus geprÃ¼ften Soll-Backups repariert. Bootreparatur regeneriert
Initramfs; eine restaurierte Aktivierung muss wieder ausdrÃ¼cklich aktiviert werden.

**Falls SSH nicht verfÃ¼gbar ist:** GerÃ¤t kontrolliert ausschalten, SD-Karte an
einem Linux-Rechner mit ext4-UnterstÃ¼tzung einhÃ¤ngen. Nicht nur die FAT-Bootpartition
bearbeiten: die root-privaten Journale/Backups liegen auf ext4. Mit dem nachfolgend
beschriebenen Offline-Werkzeug den alten Startpfad restaurieren. Im Zweifel ein
vorher erstelltes vollstÃ¤ndiges SD-Backup auf einen separaten DatentrÃ¤ger kopieren.
Ohne Linux/ext4-Zugang kann Windows die Root-Backups nicht sicher restaurieren.

Mit beiden SD-Partitionen unter `/mnt/spot` und `/mnt/spot/boot/firmware` auf einem
separaten Linux-Rechner gemountet, aus dem entpackten geprÃ¼ften OS-Paket:
`sudo python3 -m spot_os.offline_restore --root /mnt/spot`.
Das Werkzeug fÃ¼hrt keine Programme der Ziel-SD aus; Details im Abnahmeplan.

## Diagnose und Zeitmessung

`sudo spot-os status`, `sudo spot-os doctor`, `sudo spot-os check` und
`journalctl -u spot-os-session.service -b --no-pager` liefern ZustÃ¤nde, App-Version,
Update-Locks/Transaktion, Versuche, prÃ¤sentierte Abdeckung und monotone Meilensteine.
`systemd-analyze critical-chain spot-os-session.service` zeigt die systemseitige
Startkette. `sudo spot-os test-restart` fordert einen sicheren App-Neustart an;
mit `sudo spot-os status` auf RUNNING warten. FÃ¼r den Zeitpunkt der ersten sichtbaren
Firmware-/Kernelgrafik ist eine externe Bootaufnahme nÃ¶tig. OS-Zeitstempel beginnen
im Userspace und werden nicht als diese frÃ¼he Zeit ausgegeben.

## Backlight 0: sichere Zertifizierung

Software-Standby bleibt Standard. Kein `bl_power=4`, kein DSI-/Touch-Abschalten,
kein Suspend. Die vorhandene App speichert/restauriert spÃ¤ter ihren genauen
Originalwert mit GerÃ¤teidentitÃ¤t und privatem Restore-Record. Der Manager kann
bei App-Absturz diesen bestehenden Record restaurieren, bevor eine neue App startet.

Auf dem Pi zuerst normal in den **Software-Standby** gehen. Dann Ã¼ber SSH:
`sudo --preserve-env=SSH_CONNECTION spot-os test-backlight`.
Der Befehl prÃ¼ft die Hardware erneut, startet vor Helligkeit 0 einen unabhÃ¤ngigen
root-systemd-WÃ¤chter und wartet auf dessen Bereitschaft. Dieser restauriert nach
30 s genau den gespeicherten Originalwert, auch wenn Testprozess, SSH oder App
ausfallen. Am Display doppeltippen. Zertifizierung erfordert echte Raw-Touch-Taps
UND die Wake-ZustandsÃ¤nderung der laufenden SPOT-App, danach bestÃ¤tigte RÃ¼ckkehr
der Helligkeit. Ohne beides bleibt das Power-Profil unverÃ¤ndert.

Nur nach Erfolg schreibt der Befehl das bestehende root-eigene Profil
`/etc/spot/device-power.json`, gebunden an aktuellen Kernel, Wayland, Touchname und
genau Backlight 11-0045. Die bereits vorhandene Gruppe `spot-device` erhÃ¤lt nur
Schreibzugriff auf diesen Brightness-Knoten; keine allgemeinen Adminrechte.
Der Benutzer muss schon Ã¼ber die bestehende GerÃ¤teberechtigungsarchitektur in
dieser Gruppe sein. Bei Kernel-/HardwareÃ¤nderung verfÃ¤llt die Freigabe und die App
bleibt im Software-Standby. Der Test ist **noch nicht am Pi durchgefÃ¼hrt**.

## Dateien, Tests und nÃ¤chste Image-Stufe

`config/spot-os-session.service`, `config/labwc/*`, `config/plymouth/*`,
`native/splash.c`, `native/Makefile`, `manager.py`, `model.py`, `qt_bridge.py`,
`bootstrap/sitecustomize.py`, `installer.py`, `system.py`, `cli.py`, `backlight.py`,
`assets.py`, `package.py`, `packages.txt`, `cursors/` und `tests/` bilden das Paket.
Die Imagestruktur ist damit vorbereitbar; ein vollstÃ¤ndiges Image-Build ist
bewusst kein behauptetes Ergebnis dieser Version.

Automatisch: `QT_QPA_PLATFORM=offscreen python -m pytest tests spot_os/tests -q`.
Unter Linux zusÃ¤tzlich `make -C spot_os/native && python spot_os/tests/run_wayland.py`.
Die vollstÃ¤ndige vorhandene App-Suite bleibt dabei aktiv. Wayland-Proofs enthalten
echte Labwc/GTK/Qt-Prozesse mit Kamera-Double, Abdeckung, GUI-Fehlergrenze,
kontrolliertem Neustart, begrenzten Crash-Retries und dem zusammengesetzten
signierten Handoff: erfolgreicher Kandidat, Crash-Rollback und GUI-Proof-Timeout.
Die bestehende Handoff-/Rollback-Suite lÃ¤uft zusÃ¤tzlich mit echten POSIX-Locks
und separaten Prozessen; die GerÃ¤teabnahme
muss zusÃ¤tzlich den zusammengesetzten Ablauf auf Pi/DSI bestÃ¤tigen.

Siehe [Abnahmeplan](ACCEPTANCE.md). MaÃŸgeblich fÃ¼r ein einsatzbereites GerÃ¤t ist
die dokumentierte reale Abnahme, nicht allein ein grÃ¼ner CI-Lauf.
