# Betreiberabnahme 0.1.6 → 0.1.7

Keine Ausführung durch Codex. Das Hauptgerät bleibt unverändert, bis der
Betreiber einen getrennten Test ausdrücklich autorisiert. 0.1.6 wurde laut
Betreiber auf echter Hardware erfolgreich getestet; alle folgenden 017-Felder
sind bis zur neuen Abnahme offen.

## Vor der Stable-Freigabe

Beide PRs prüfen. Den exakt vorgesehenen privaten main-Commit und dessen
Kandidatenhash nach erfolgreicher push-main-CI festlegen. Den neuen Build auf
einer separaten Test-SD nach ausdrücklich vereinbartem, authentifiziertem
administrativem Testverfahren abnehmen. Ein unsigniertes CI-ZIP ist kein
offizielles Touch-OTA-Paket; diese Dokumentation gibt ihm keine
Installationsfreigabe auf dem Hauptgerät. Falls ein signierter Probelauf
benötigt wird, muss dessen kontrollierter, nichtöffentlicher Ablauf vorab
mit dem Betreiber vereinbart werden. Das Stable-Gate bleibt bis dahin zu.

Abnahmeprotokoll an Commit, ZIP-SHA256, Kernel, Appversion und Test-SD binden.
GUI/Kamera, Messung, Video/PbP, Galerie/Analyse, Energie/Standby, SSH und
Startpfad prüfen. Keine Hardwarefreigabe allein aus den erfolgreichen 016-
Tests oder grüner CI ableiten. Erst danach die bestehenden Environment-
Freigaben und das zusätzliche Stable-Gate bewusst bedienen.

## Nach gesondert freigegebener signierter Veröffentlichung

Die vier öffentlichen Dateien und die bestehende OS-Identität nach der
Signierer-Anleitung prüfen. Für den normalen OTA-Ablauf sind weder SSH noch
Bridge noch Pi-Reboot nötig:

1. Auf dem laufenden OS0.1.6/App0.28.13 Software-Updates öffnen.
2. Pre-Releases müssen für den Stable-Kandidaten nicht aktiviert werden.
3. OS0.1.7 suchen, laden, Signatur prüfen lassen und Installation bestätigen.
4. Automatischen Sitzungswechsel abwarten. Erwartung: „SPOT OS 0.1.7 wurde
   erfolgreich installiert.“ Keine alte SSH-Warnung.
5. Neue Sitzung und Kamera, alle üblichen Gerätefunktionen prüfen.
6. Erneuter App-/Dienststart und ein späterer bewusst durchgeführter Reboot
   dürfen die alte OTA-Warnung nicht wiederbringen. Der normale OTA-Test selbst
   muss **ohne** Pi-Reboot abschliessen.

## Optionale lesende Nachweise (SSH nur zur Abnahme, nicht zur Installation)

Vorher auf gesundem 016, aus dem bereits vorhandenen oder gesondert geprüften
Abnahmeskript; keine unbestätigten privilegierten Netzwerk-Skripte ausführen:

```sh
sudo sh /PFAD/ZUM/GEPRUEFTEN/spot_os/hardware_acceptance.sh --output /var/tmp/spot-before-017.json
```

Nach erfolgreichem OTA nutzt der Pfad den installierten 017-Code:

```sh
sudo sh /usr/local/lib/spot-os/spot_os/hardware_acceptance.sh --output /var/tmp/spot-after-017.json --compare /var/tmp/spot-before-017.json
sudo spot-os doctor
sudo systemctl show spot-os-update.service spot-os-update.socket -p ActiveState -p SubState -p Result -p MainPID -p InvocationID -p NRestarts
```

Der Vergleich verlangt gleiche Boot-ID/Kernel, App-Metadaten, Lepton-
Streamer-PID/Invocation/Restartzahl, Binary-/Vertrags-/Trust-Hashes und CCI-
Lock-Inode. Er bestätigt zudem Version017, gesunden Doctor, success mit
Handover-Belegen und Floor≥107. Separaten späteren Boot nicht mit diesem
„kein Reboot während OTA“-Vergleich vermischen. Die Skripte öffnen keine
Kamera, schreiben keine CCI-Befehle und starten/stoppen keine Dienste.

## Fehler und Recovery – nur bei tatsächlichem Fehler

`handover-failed` bedeutet: installierte Sitzung kann bereits gesund sein,
der Updater ist aber nicht bestätigt. Zuerst Doctor, transaction.json,
audit.jsonl und die beiden Unit-Journale sichern. Keine Floors/Journale
löschen. Ein ausdrücklich administrativ gestarteter neuer Update-Dienst
kann den Abschluss anhand frischer Belege bestätigen. Nur das fehlerhafte
Originalkommando zu wiederholen ist kein Erfolgsnachweis.

Unterbrochene Installationen vor Commit nutzen weiterhin die bestehende
begrenzte Recovery. Bei wirklicher `recovery-failed` gelten die bisherigen
administrativen Stop-/Recover-Anweisungen; einen gesunden committed OS-
Zustand nicht wegen einer historischen Warnung zurückrollen.

Fehler-/Rollbacktests ausschliesslich auf separater Test-SD: Abbruch vor
Commit, nach Commit, ausgefallene neue Sitzung/Kamera und unterbrochener
Rollback; Rückgabewert/Journale/Dateien müssen übereinstimmen. Nach Rückkehr
auf 016 bleibt Floor107 erhalten: derselbe Release darf nicht erneut als
frisches Update angeboten werden. Für Wiederholung einen neuen, ausdrücklich
freigegebenen Teststand verwenden; kein Zurücksetzen des Floors am Gerät.
