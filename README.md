[![Pylint](https://github.com/Cougar/OmaTasku/actions/workflows/pylint.yml/badge.svg)](https://github.com/Cougar/OmaTasku/actions/workflows/pylint.yml)
[![CodeQL Advanced](https://github.com/Cougar/OmaTasku/actions/workflows/codeql.yml/badge.svg)](https://github.com/Cougar/OmaTasku/actions/workflows/codeql.yml)
[![Docker Image CI](https://github.com/Cougar/OmaTasku/actions/workflows/docker-image.yml/badge.svg)](https://github.com/Cougar/OmaTasku/actions/workflows/docker-image.yml)
[![OmaTasku Status](https://img.shields.io/uptimerobot/status/m803724870-d2d62fcecea83d09bca996f8?label=OmaTasku)](https://omatasku.v6n.xyz/)

# OmaTasku (Own Podcast) 📻

> ### 🚀 AVALIK TEENUS (GENERAL PUBLIC)
> Kui oled **tavakasutaja** (soovid lihtsalt kuulata oma lemmiksaateid täispikkuses ilma vaheklippideta), siis **SUL EI OLE VAJA seda koodi kloonida, ehitada ega ise käivitada!** OmaTasku on kõigile tasuta ja turvaliselt kättesaadav ametlikus veebiportaalis:
> 
> 👉 **[https://omatasku.v6n.xyz/](https://omatasku.v6n.xyz/)**
> 
> Mine portaali, paigalda sealt 1 klikiga meie turvaline abiskript ja asu oma lemmiksaateid kuulama otse oma harjumuspärases podcasti-mängijas!

---

### 💻 ARENDAJAD JA ISEMAJUTAJAD (DEVELOPERS & SELF-HOSTERS)
Selle GitHubi repositooriumi lähtekood, ehitusjuhendid ja konteinerlahendused on mõeldud **ainult arendajatele ja isemajutajatele**, kes soovivad luua oma isikliku kohaliku testkeskkonna aadressil `localhost`.

OmaTasku on kergekaaluline ja ülikiire Pythoni-põhine FastAPI proxy teenus premium-podcastide RSS-voogude peegeldamiseks. See asendab avalikes voogudes olevad lühikesed tutvustusklippide helilingid täispikkade ja allalaaditavate premium-helilinkidega, autoriseerides päringud sinu kehtiva tellija seansiküpsise (`__tac`) abil.

Et sa ei peaks oma podcasti mängijas (VLC, Pocket Casts, Apple Podcasts, Overcast jne) saate linke iga kord muutma, kui brauserisessioon aegub, seob OmaTasku sinu püsiva kordumatu kasutaja ID (UUID) kohalikus andmebaasis sinu aktiivse seansiküpsisega. Seansiküpsist saab sujuvalt ja taustal uuendada läbi veebiliidese või 1 klikiga otse raadioportaali lehtedelt meie mugava brauseriskripti abil!

---

## 🏗️ Süsteemi arhitektuur

```
                    ┌─────────────────────────┐
                    │   Podcasti mängija      │
                    └──────────┬──────────────┘
                               │
            1. Päri RSS voog   │   5. Striimi premium MP3 faili
  (/rss/{user_id}/{feed_slug}) │
                               ▼
  ┌────────────────────────────────────────────────────────────┐
  │ OmaTasku RSS peegeldusteenus (FastAPI)                     │
  │                                                            │
  │  ┌──────────────┐      2. Päri RSS voog  ┌──────────┐      │
  │  │   RSS voo    ├───────────────────────>│  Avalik  │      │
  │  │  kontroller  │<───────────────────────┤ RSS voog │      │
  │  └──────┬───────┘       Algne XML        └──────────┘      │
  │         │                                                  │
  │         │ 3. Tuvasta ID-d ja lahenda                       │
  │         ▼                                                  │
  │  ┌──────────────┐    Päri allkirjastatud   ┌────────┐      │
  │  │ URL-i lahenda├─────────────────────────>│ Audio  │      │
  │  │              │<─────────────────────────┤ backend│      │
  │  └──────┬───────┘      (Sessiooniga)       └────────┘      │
  │         │                                                  │
  │         ├─── (Küsi aktiivset __tac) ──┐                    │
  │         │                             │                    │
  │         ▼                             ▼                    │
  │  ┌──────────────┐             ┌──────────────┐             │
  │  │ Mitmekihiline│             │  SQLite /    │             │
  │  │ vahemälu     │             │  JSON-pood   │             │
  │  └──────────────┘             └──────┬───────┘             │
  │                                      │                     │
  │         ┌────────────────────────────┘                     │
  │         ▼ 4. Uuenda sessiooniküpsist                       │
  │  ┌──────────────┐                                          │
  │  │ Veebiportaal │                                          │
  │  │   (HTML)     │<── [Veebibrauser (Kasutaja uuendab seanssi)]
  │  └──────────────┘                                          │
  └────────────────────────────────────────────────────────────┘
```

---

## 🚀 Paigaldamine ja käivitamine (Arendajatele)

### Variant 1: Käivitamine Docker Compose'i abil (Soovituslik)
OmaTasku töötab maksimaalse turvalisuse tagamiseks **täielikult kirjutuskaitstud (read-only) konteineris**:

1. Klooni see repositoorium kohalikku masinasse.
2. Käivita teenus kohalikult (`localhost` vaikimisi seadistusega):
   ```bash
   docker-compose up -d
   ```
3. Ava OmaTasku kohalik portaal aadressil **`http://localhost:8080`**.

### Variant 2: Kohalik käivitamine (Python / Uvicorn)
Teenuse native käivitamine otse Uvicorniga on eelistatud arendusmeetod, kuna see asetab uvicorni peaprotsessiks (PID 1), tagades operatsioonisüsteemi lõpetamissignaalide (`SIGTERM`, `SIGINT`) kohese ja korrektse käsitlemise:

```bash
# 1. Loo virtuaalkeskkond ja aktiveeri see
python3 -m venv .venv_sys
source .venv_sys/bin/activate

# 2. Paigalda vajalikud teegid
pip install -r requirements.txt

# 3. Käivita teenus kohalikult uvicorniga (SOOVITUSLIK)
UVICORN_PORT=8080 UVICORN_HOST=127.0.0.1 uvicorn main:app
```

#### Käivitamine CLI argumentidega (Programmilise argumendiga)
Soovi korral saad teenuse käivitada ka CLI argumente kasutades:
```bash
python3 main.py --host 127.0.0.1 --port 8080 --base-url "http://localhost:8080/"
```

---

## ⚙️ Seadistamine (Strict 12-Factor App Environment)

Kogu teenuse seadistamine toimub eranditult läbi keskkonnamuutujate (keskkonnasõbralik pilvelahendus, `.env` faile ei loeta):

* **`DEFAULT_USER_ID`** ja **`PIANO_TAC_COOKIE`**: *(Valikuline — ideaalne isiklikuks isemajutamiseks).* 
  Need muutujad on mõeldud mugavaks **"Zero-Configuration"** käivitamiseks, kui majutad teenust isiklikuks otstarbeks (näiteks koduses NAS-serveris või Raspberry Pi peal) ainult iseenda jaoks.
  * **Kuidas see töötab:** Teenuse käivitumisel kontrollitakse, kas need muutujad on defineeritud. Kui jah, luuakse andmebaasi automaatselt kasutaja antud ID-ga (või uuendatakse olemasoleva küpsise väärtust uuega).
  * See võimaldab sul oma premium-voogu kasutada kohe ilma veebiliidest avamata aadressil `http://localhost:8080/sinu-default-user-id/postimees/rss/shows/saate-tunnus`.
  * **Avalikus teenuses** (`https://omatasku.v6n.xyz/`) neid muutujaid **ei kasutata**, kuna seal registreerivad kõik kasutajad oma seansid dünaamiliselt läbi veebiliidese.
* **`BASE_URL`**: Avalik välisveebi aadress (isiklikul käivitamisel vaikimisi **`http://localhost:8080/`**), mida kasutatakse veebiliideses kopeeritavate RSS linkide tekitamiseks.
* **`DB_DIR`**: Kaust, kuhu salvestatakse SQLite andmebaas (vaikimisi `.`). Konteinerites peaks see viitama andmemahu `/data` kaustale.
* **`DB_NAME`**: SQLite andmebaasi faili nimi (vaikimisi `omatasku.db`).
* **`RSS_CACHE_TTL`**: Algse platformi XML voogude vahemälu kestus sekundites (vaikimisi `60`).
* **`OTEL_EXPORTER_OTLP_ENDPOINT`**: (Valikuline) OpenTelemetry HTTP/Protobuf kollektori endpoint (nt. `http://localhost:4318`), kuhu saata jaotatud logisid ja jälgi (tracing).

---

## ⚡ Premium seansside sünkroonimine

OmaTasku sisaldab mugavat ja turvalist kaasasolevat Violentmonkey/Tampermonkey abiskripti, mis automatiseerib kogu tegevuse:

1. Ava oma kohalik veebiliides **`http://localhost:8080`**.
2. Klõpsa nupule **📥 Paigalda OmaTasku Partner Skript**, et paigaldada kasutajaskript oma brauserisse.
3. Ava mistahes saate leht portaalis [Kuku Raadio (kuula.postimees.ee)](https://kuula.postimees.ee/).
4. Klõpsa ekraani all paremas nurgas asuvale sinisele nupule **`⚡ OmaTasku Sünk`**. Kasutajaskript loeb automaatselt sinu sisselogitud premium-seansi tokeni ja sünkroonib selle turvaliselt sinu kohaliku OmaTasku serveriga (`http://localhost:8080`) vähem kui sekundiga!
5. Klõpsates mistahes saate pealkirja kõrvale tekkivat rohelist nuppu **`📻 OmaTasku RSS`**, kopeeritakse selle saate isiklik premium-mängija link otse sinu lõikelauale, valmis sisestamiseks suvalisse mängijasse!

---

## 📈 Jälgitavus ja seire (Observability)

* **`/metrics`**: Väljastab Prometheus-ühilduvaid seireandmeid, mis jälgivad registreeritud seansside arvu, viimaseid sünkroonimisaegu ning API päringute counts koos staatuskoodidega (rakendus puhastab automaatselt tundlikud kasutaja UUID-d päringute teedest, et tagada andmeturve ja vältida Prometheuse andmebaasi ülekoormust).
* **OpenTelemetry (OTLP):** OmaTasku on täielikult instrumenteeritud. Seadistades muutujaga `OTEL_EXPORTER_OTLP_ENDPOINT`, edastab süsteem detailsed trace span'id sissetulevate päringute, algsete RSS-XML-ide päringute ning paralleelsete failisuuruste HEAD päringute kohta (sensor eemaldab automaatselt ja tsenseerib turvalisuse huvides siseandmetest tundliku seansiküpsise sisu).
