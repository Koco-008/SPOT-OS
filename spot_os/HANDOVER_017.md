# OTA-Abschluss 0.1.7: Ursache und Sicherheitsnachweis

## Nachgewiesener Codefehler

0.1.6 speichert nach bestätigter neuer Sitzung `phase=success` und ruft aus
dem Update-Dienst `systemctl try-restart --no-block spot-os-update.service` auf.
`KillMode=control-group` beendet beim Neustart auch dessen systemctl-Kindprozess.
`--no-block` verhindert das Warten auf den Restart-Job, garantiert aber nicht,
dass der Aufrufer seine D-Bus-Antwort noch vor der Beendigung erhält.
`System.command` reduziert jeden Fehler auf `Command failed: /usr/bin/systemctl`;
Rückgabecode und stderr wurden im alten Transaktionsjournal nicht gespeichert.

Der sicher belegte Fehler: jede solche Exception überschreibt die erfolgreiche
Transaktion mit einer persistenten SSH-Warnung. Der nächste Prozess überprüft
diese Warnung nie. Es gibt keinen Nachweis, dass der Restart tatsächlich fehlt.
Der Betreiber berichtet für den realen 0.1.6-Test einen erfolgreich laufenden
Nachfolger. Die genaue ursprüngliche Kernel-/D-Bus-Fehlerantwort lässt sich aus
dem verkürzten Fehlertext **nicht** rückwirkend beweisen. Der Selbst-Neustart
hat jedoch diese Race-Möglichkeit und ist deshalb vollständig entfernt.

## Neuer Ablauf

1. Installationsjournal committed, neue GUI/Kamera gesund, Floor107 dauerhaft.
2. `handover-pending` atomar speichern, inklusive alter PID, /proc-Startzeit,
   systemd-InvocationID und Boot-ID. Bis hier sind unterbrochene Installationen
   weiter durch die bestehende begrenzte Recovery geschützt.
3. Der Worker gibt den prozessübergreifenden Owner frei. Erst danach beendet
   der echte Service-Mainloop sich mit Status75. PID1 startet ihn gezielt neu
   (`SuccessExitStatus=75`, `RestartForceExitStatus=75`). Die vorhandenen
   Startlimits, TimeoutStopSec, RestartSec und sämtliche Sandbox-Grenzen bleiben.
4. Nach READY prüft der Nachfolger seine tatsächliche PID1-MainPID,
   InvocationID, ActiveState=active/SubState=running/Result=success, eigene
   /proc-Identität und eine geänderte Invocation oder Boot-ID. Eine kurze,
   begrenzte Bereitschaftsabfrage überbrückt nur die READY-Verarbeitung in PID1;
   Zeitablauf allein ist niemals ein Erfolgsbeleg.
5. Der laufende Python-Code muss VERSION017 haben; installierte Version,
   Dateiintegrität, vollständige Installation/Aktivierung und frische GUI-/
   Kameragesundheit müssen übereinstimmen. Erst dann `success` mit Belegen.

Echte Handover-Fehler werden als `handover-failed` gespeichert und für die
unveränderte App0.28.13 als `recovery-failed` mit administrativer Diagnose
ausgegeben. `handover-pending` wird als `verifying` übertragen. Neue Änderungen
bleiben gesperrt; ein gesunder bereits committed Zustand wird nicht allein
wegen des Prozesswechsels zurückgerollt. Ein späterer ausdrücklich gestarteter
gesunder Dienst kann den Abschluss erneut prüfen. Keine automatische Endlosschleife.

## Übergang vom tatsächlich installierten 0.1.6-Updater

Die erste Installation führt weiterhin der alte 0.1.6-Code aus. Seine Signatur-,
Range-, Hash-, Floor- und Sandbox-Verträge sind kompatibel mit 0.1.7. Dessen
normaler Restart lädt den neuen Code. Dieser prüft auch alte `success`-Einträge
ohne Handover-Bestätigung, unabhängig davon, ob deren systemctl-Aufruf Null
oder einen Fehler lieferte. Alte Warnungen werden vorher nicht als aktuelle
Erfolgsmeldungen an die App weitergereicht. Ihr Text bleibt im Audit und unter
`handover_previous_message` erhalten. Bestätigte Transaktionen werden beim
nächsten Dienst-/Systemstart nicht erneut verändert. Die 0.1.4-Bridge ist für
0.1.6 → 0.1.7 weder erforderlich noch aufzurufen.

## Prüfgrenzen

Tests laden die exakte 0.1.6-Engine und den Installer aus
`7af6009b1aecc3fdf9676ffa20c7414d390faa78`, prüfen signierte ephemere
017-Testpakete, Journale, Fehler, Rollback und Floors106/107. PID1-CI verwendet
den echten Socket-Service-Mainloop in der produktiven Sandbox und bestätigt
verschiedene Prozesse/Invocations, erfolgreichen Exit75-Handover, historischen
Restart-Fehler und einen tatsächlich nicht bestätigten Nachfolger.
Inventar und physische GUI/Kamera bleiben dabei Testdoubles; kein Pi-Nachweis.

App, Kernel, Lepton-Binaries/-Protokoll, CCI-Lock, Boot-/Touch-/Energiesteuerung,
Vertrauensidentitäten und Benutzerdateien werden nicht geändert. Keine neuen
privilegierten Dienste, Capabilities oder Schreibziele. Audit, Manifeste und
Anti-Replay-Floor werden nicht gelöscht oder zurückgesetzt.
