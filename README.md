# Auto-Wächter

Sucht alle 10 Minuten neue Occasionen und schickt einen Push aufs iPhone (ntfy), sobald ein Auto zu einem deiner Wächter passt oder im Preis sinkt.

- **Wächter** (`waechter.json`): beliebig viele Suchaufträge, je mit Marken und Modellen, Aufbau, 4x4, Preis, km, Jahrgang, Umkreis, Treibstoff, Getriebe, Verkäufer, „ab MFK“ und Push-Art (sofort, leise, Sammel-Meldung um 18 Uhr). Bearbeiten über die Seite `docs/index.html` (GitHub Pages) oder direkt in der Datei.
- **Quellen:**
  - **tutti.ch direkt**, nur Rubrik Autos, neueste zuerst. tutti zeigt auch die Händler-Inserate von AutoScout24. Die Detailseite liefert Jahrgang, km, Aufbau, Treibstoff, Getriebe und Standort.
  - **Suchabo-Mails** von AutoScout24, tutti, Comparis, Ricardo und anderen. Diese Plattformen sperren automatische Abfragen (403), daher der Umweg über die Mails.
- **Push:** Titel mit Modell und Preis, darunter Jahrgang, km, Treibstoff, Getriebe, Ort mit Distanz. Antippen öffnet das Inserat, „Anfragetext“ öffnet `docs/anfrage.html`. Preissenkungen kommen als eigener Push. Dasselbe Auto auf mehreren Plattformen gibt nur einen Push. Nachtruhe 22–7 Uhr: gesammelt um 7 Uhr.
- **Unklare Angaben:** Fehlt in einem Inserat z. B. der km-Stand, wird das Auto trotzdem gemeldet, mit dem Hinweis „Prüfen: km unbekannt“.

## Einrichten

1. **iPhone:** In der App **ntfy** auf **+** tippen und den Kanal aus `NTFY_TOPIC` abonnieren (Server: ntfy.sh). Eigener Kanal, getrennt vom Parkplatz-Wächter.
2. **GitHub:** Öffentliches Repository mit diesen Dateien. Bei öffentlichen Repositories sind die Actions-Minuten gratis.
3. **Settings → Secrets and variables → Actions:**
   - Secrets: `NTFY_TOPIC`, `HOME_LAT`, `HOME_LON` (ab hier wird der Umkreis gemessen; die Adresse bleibt geheim)
   - Variable: `SEITE` = `https://<benutzer>.github.io/auto-waechter/`
4. **Settings → Pages:** Branch `main`, Ordner `/docs`. Dann ist die Wächter-Seite unter `SEITE` erreichbar. Auf dem iPhone in Safari öffnen und „Zum Home-Bildschirm“ wählen.
5. **Schlüssel zum Speichern:** Auf GitHub einen Fine-grained Token anlegen, nur für dieses Repo, Berechtigung *Contents: Read and write*. Auf der Wächter-Seite unten einfügen. Er bleibt nur in diesem Browser.
6. **Actions → Auto-Wächter → Run workflow** einmal von Hand starten. Der erste Lauf meldet alle Autos, die schon jetzt passen. Danach nur noch Neues.

## Mail-Brücke (AutoScout24, tutti, Comparis, Ricardo)

1. Auf jeder Plattform ein **breites** Suchabo mit E-Mail anlegen, zum Beispiel: Occasionen, Kombi und SUV, bis CHF 30'000, ganze Schweiz, Benachrichtigung „sofort“. Die feinen Filter macht der Wächter. Neue Marken brauchen dann kein neues Suchabo.
2. Im Postfach einen Filter anlegen, der Mails dieser Absender in das Label `Auto-Alarm` legt.
3. App-Passwort für das Postfach (Gmail: myaccount.google.com/apppasswords). Das vom Parkplatz-Wächter kann wiederverwendet werden.
4. Secrets `MAIL_USER` und `MAIL_APP_PASSWORD` anlegen. Anderer Ordnername: Variable `MAIL_ORDNER`.

Eine gespeicherte Suchabo-Mail (.eml) lässt sich lokal prüfen: `python3 waechter.py --mail-datei mail.eml`.

## Lokal testen

```bash
set -a; source .env.local; set +a
python3 waechter.py --dry-run     # zeigt alle gefundenen Autos und ob sie passen, sendet nichts
python3 waechter.py --test-push   # schickt einen Beispiel-Push
python3 waechter.py --alle        # schickt alle aktuell passenden Autos als Liste
```

Hinweis: tutti.ch sperrt die Server von GitHub. Dort wird tutti still übersprungen und die Suchabo-Mails übernehmen. Lokal auf dem Mac funktioniert tutti direkt.
