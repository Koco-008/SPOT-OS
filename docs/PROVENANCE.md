# Herkunft und BMA-Freeze

- Offizielle Version: **SPOT OS 0.1.7 Stable**, Release [`os-v0.1.7`](https://github.com/Koco-008/SPOT-OS-Releases/releases/tag/os-v0.1.7).
- Laut offiziellem GitHub-Release-Text geprüfter originaler Quellcommit: `0e21c1965a4c0eb9238fa9355abe4cf4f48db36a` im umbenannten privaten Repository `Koco-008/SPOT-App` (GitHub Repository-ID `1420310429`).
- App-BMA-Endstand: **0.31.1 Stable**, [signierter Release](https://github.com/Koco-008/SPOT-Releases/releases/tag/v0.31.1).
- Offizieller OS-ZIP-Digest (SHA256 aus GitHub Release-Metadaten): `4d5b7bc6a41d88c440caa300f130c4fc0409605893e6a8167b7e8ac71181b8b0`.
- OS-Public-Key-Digest (SHA256): `b142b246c22fd6f56f4207b86bbd5dcef09b122d228af81c0c51130d4309f907`.

Dieses GitHub-Repository ist ein **kuratierter neuer Quellcode-Snapshot** ohne historische vertrauliche Commits, private Freigabewerkzeuge und alte CI-Artefakte. Die runtime-relevanten Dateien stammen aus dem festen Originalcommit. Die grosse, unveränderte TGA-Grafik wird aus dem signierten Original-PNG rekonstruiert und gegen den im Quellenbestand festgehaltenen SHA256-Wert geprüft.

**Wichtig:** Eine neu erzeugte ZIP aus dieser öffentlichen Arbeitskopie kann wegen bewusst ausgelassener historischer Dokumente, Tests und CI-Tools eine *andere ZIP-Prüfsumme* haben als das bereits signierte Paket. Das ist **kein** neuer offizieller Release und keine Ed25519-Verifikation. Für Installationen ausschliesslich den originalen signierten OS-Release verwenden.

Das ursprüngliche Deployment- und Signiermodell verwendet weiterhin die separat geschützte Quelle und den unabhängigen öffentlichen Signierworkflow im Repository [SPOT-OS-Releases](https://github.com/Koco-008/SPOT-OS-Releases). Die Veröffentlichung hier ändert dessen Quellnachweis, Freigaben und Schlüssel **nicht**.
