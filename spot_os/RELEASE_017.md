# SPOT OS 0.1.7 – OTA-Abschluss

Vorbereitet für `os-v0.1.7`, Sequenz107, Kanalstable, exakt OS0.1.6 als
Vorgänger, Appminimum0.28.13, `reboot=false`. Noch keine Veröffentlichung und
keine Hardwarefreigabe des neuen Builds.

- Selbst-Neustart des Updaters ersetzt durch prozessbestätigten PID1-Handover.
- Erfolg erst nach bestätigtem Nachfolger; historischer 0.1.6-Restart-Fehler
  bleibt im Audit, erscheint nach erfolgreicher Bestätigung nicht erneut.
- Echte Abschlussfehler bleiben sichtbar und sperren weitere OS-Änderungen.
- Versionsunabhängige lesende Vorher-/Nachher-Abnahme mit Bestandsschutzvergleich.
- Privater Kandidaten-Workflow leitet Paketnamen aus VERSION ab.

Ursache, Prozess-/Journalverträge und Tests: HANDOVER_017.md.
Touch-OTA, Hardwareabnahme und Wiederherstellung: OTA_TEST_017.md.
Stabile Signierfreigabe, exakte Eingaben und öffentliche Nachprüfung stehen
im öffentlichen Signierer-PR unter RELEASE_PROCESS.md.

Der Betreiber hat OS0.1.6 auf dem Pi nach Touch-OTA und späterem Reboot
erfolgreich geprüft. Das ist keine Hardwareabnahme von 0.1.7. Vor Stable sind
die Abnahme dieses exakten Commits/Pakethashes und die gesonderte bewusste
Betreiberfreigabe offen. CI alleine darf das Stable-Gate nicht öffnen.
