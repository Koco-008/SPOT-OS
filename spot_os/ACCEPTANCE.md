# Hardwareabnahme SPOT OS 0.1.7 / App 0.28.13

0.1.6 ist laut Betreiber auf echter Hardware nach Touch-OTA und Reboot geprüft.
Das bestätigt nicht den neuen Build. Sämtliche 017-Felder bleiben offen, bis
Commit und Paket-SHA mit einem neuen Betreiberprotokoll gebunden sind.

| Prüfung | Erwartung | 017-Hardware |
| --- | --- | --- |
| Herkunft | bestehender OS-Key, Commit, Hash, Signatur, Sequenz107 | offen |
| Normaler OTA | 016 auf 017 über Touch, ohne Bridge/SSH/Reboot | offen |
| Abschluss | success erst nach belegtem neuen Updater, GUI/Kamera gesund | offen |
| Dauerhaftigkeit | Dienst-/App-/Systemneustart ohne alte OTA-Warnung | offen |
| Bestandsschutz | App13, Streamer, CCI-Inode, Keys, Daten, Boot-ID gleich | offen |
| Bedienung | Messung, Video, Analyse, Energie, Standby, Touch/Display | offen |
| Test-SD-Recovery | vor Commit Rollback, nach Commit bestätigter Nachfolger | offen |
| Stable-Freigabe | bewusste Betreiberfreigabe genau dieses Builds | offen |

Einzelheiten und lesende Befehle: OTA_TEST_017.md. Historische Abnahmesheets:
ACCEPTANCE_016_HISTORICAL.md und ACCEPTANCE_015_HISTORICAL.md. Diese enthalten
damalige Planstände, keine rückwirkend erfundenen Testergebnisse.
