# anaf-sync

<p>
  <a href="https://github.com/robert-malai/anaf-sync/actions/workflows/ci.yml"><img
    src="https://img.shields.io/github/actions/workflow/status/robert-malai/anaf-sync/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="https://codecov.io/gh/robert-malai/anaf-sync"><img
    src="https://img.shields.io/codecov/c/github/robert-malai/anaf-sync?branch=main" alt="Coverage"></a>
  <a href="https://pypi.org/project/anaf-sync/"><img
    src="https://img.shields.io/pypi/v/anaf-sync" alt="PyPI version"></a>
  <a href="https://pypi.org/project/anaf-sync/"><img
    src="https://img.shields.io/pypi/pyversions/anaf-sync" alt="Python versions"></a>
</p>

Arhivator local, programat, pentru facturile RO e-Factura, construit peste
[anafpy](https://github.com/robert-malai/anafpy). ANAF șterge mesajele din SPV
la circa **60 de zile** după depunere; anaf-sync rulează periodic, listează
toată fereastra de retenție, descarcă doar ce nu a mai văzut și așază
facturile pe disc după un șablon de căi construit din datele facturii
(`2026/07/2026-07-03_FCT-1001_ACME SRL.pdf`, nu id-uri opace ANAF). Rulează pe
Windows, Linux și macOS.

> **English:** anaf-sync archives RO e-Factura invoices locally on a
> schedule. User docs are in Romanian because the tool only serves entities
> with Romanian fiscal obligations. Developer docs are in English — see
> [CONTRIBUTING.md](CONTRIBUTING.md) and [DESIGN.md](DESIGN.md).

## Instalare

```bash
uv tool install anaf-sync        # dintr-un wheel publicat
# sau, din acest checkout:
uv tool install --from . anaf-sync
```

Ai nevoie de [uv](https://docs.astral.sh/uv/), care își instalează singur
Python-ul potrivit.

## Autentificare

anaf-sync nu are un sistem propriu de credențiale: refolosește autentificarea
[anafpy](https://github.com/robert-malai/anafpy) — același login servește și
serverul MCP anafpy. Certificatul e necesar **doar la autorizarea
inițială din browser**, cam o dată pe an; după aceea token-urile se
reîmprospătează automat, fără certificat, deci rulările programate merg
nesupravegheate.

### Pasul 1 — precondiții pe portalul ANAF (o singură dată)

1. **Certificat digital calificat** (token USB de la certSIGN, DigiSign,
   Trans Sped, AlfaSign etc.), **înregistrat în SPV** pentru firma ta. Dacă
   accesezi deja Spațiul Privat Virtual al firmei cu certificatul, ești gata.
2. **Înregistrare ca dezvoltator de aplicații**, pe
   [anaf.ro](https://www.anaf.ro/anaf/internet/ANAF/servicii_online/inreg_api):
   *Servicii Online → Înregistrare utilizatori → Dezvoltatori aplicații →
   Înregistrare pentru API-uri*. Confirmarea vine printr-un cod de securitate
   trimis pe e-mail.

### Pasul 2 — profilul OAuth (client_id + client_secret)

Tot pe portal, în formularul *Profil Oauth*, completezi:

| Câmp | Ce pui |
|---|---|
| **Denumire aplicație** | orice nume, de ex. `anaf-sync` |
| **Callback URL 1** | de ex. `https://localhost:8765/callback` — schema trebuie să fie **`https://`** (portalul respinge `http://`); poate fi localhost, nu îți trebuie un server public |
| **Serviciu** | **E-Factura** |

Apeși **Generare Client ID** și primești un **Client ID** și un **Client
Secret** — „parola" aplicației; păstrează-le în siguranță.

### Pasul 3 — login

```bash
export ANAFPY_CLIENT_ID=...          # sau într-un fișier .env
export ANAFPY_CLIENT_SECRET=...

anafpy auth login --redirect-uri https://localhost:8765/callback
```

Se deschide browserul, îți alegi certificatul digital, iar ANAF
redirecționează către callback-ul local. Pentru că ANAF acceptă doar
callback-uri `https://`, iar pentru `localhost` nicio autoritate nu emite
certificate, anafpy generează pe loc un certificat de unică folosință pentru
acest callback: browserul va afișa **o singură dată** avertismentul
„Connection is not private" („Conexiunea nu este privată"). E de așteptat —
comanda te anunță dinainte; apasă „Advanced" → „Proceed to localhost" și
autentificarea se încheie singură.

Alternative: cu propriul certificat — de ex. generat cu
[mkcert](https://github.com/FiloSottile/mkcert) — `--tls-cert`/`--tls-key`
elimină avertismentul; iar `--paste` nu pornește niciun server local —
browserul afișează o eroare de conexiune, tu copiezi URL-ul complet din bara
de adrese în terminal (repede: codul ANAF expiră în ~60 de secunde).

Token-urile se salvează în credential store-ul sistemului de operare. Pe
mașini fără credential store (servere headless), folosește varianta pe
fișier: `ANAFPY_TOKEN_STORE_BACKEND=file` și
`ANAFPY_TOKEN_STORE=~/.anafpy/tokens.json`.

`ANAFPY_CLIENT_ID` și `ANAFPY_CLIENT_SECRET` trebuie să rămână setate (în
mediu sau în `.env`) și după login: cu ele își reîmprospătează rulările
programate token-urile expirate, fără intervenția ta. Token-ul de acces ține ~90 de zile,
refresh-ul ~365 — browserul și certificatul revin în joc doar când expiră și
acesta.

> **Atenție la `.env` + rulări programate:** un `.env` din directorul
> *curent* funcționează doar interactiv — joburile programate (Task
> Scheduler, systemd, launchd) nu pornesc din folderul tău și nu citesc
> profilul shell-ului. Pentru rulările programate pune `.env`-ul cu
> variabilele `ANAFPY_*` lângă `config.toml`, în directorul de configurare
> (calea o vezi cu `anaf-sync status`; tot acolo verifici și dacă
> credențialele sunt găsite).

## Configurare

```bash
anaf-sync init            # scrie un config.toml comentat
anaf-sync status          # arată unde se află fișierul pe platforma ta
```

Fișierul generat e comentat și acoperă toate cheile: `cif = "12345678"` (sau
`cifs = ["...", "..."]` pentru mai multe firme), `direction` (`received`,
`sent` sau `both`), `lookback_days` (1–60 — limita de retenție ANAF) și
`failure_retention_days`, plus secțiunea `[output]` de mai jos. Dacă vrei
config-ul în altă parte, `--config`/`-c` (sau variabila de mediu
`ANAF_SYNC_CONFIG`) funcționează la orice comandă; `anaf-sync init --force`
suprascrie un fișier existent.

Partea interesantă e șablonul de căi:

```toml
[output]
directory = "~/Facturi"
template  = "{cif}/{direction}/{issue_date:%Y}/{issue_date:%m}/{issue_date:%Y-%m-%d}_{number}_{partner_name}"
artifacts = ["zip", "pdf"]        # și: xml, signature, metadata
```

Șabloanele folosesc sintaxa `str.format` din Python peste contextul facturii:
`number`, `issue_date` / `due_date` (date reale — specificatorii `strftime`
funcționează), `issue_month` / `created_month` (numele lunii în română:
`iulie`), `currency`, `kind`, `direction`, `cif`,
`partner_name`/`partner_cif` (*cealaltă* parte, indiferent de direcție),
`message_id`, `request_id`, `message_type`, `created`. Valorile substituite
sunt sanitizate pentru sistemul de fișiere; un `/` literal în șablon creează
foldere; fiecare artefact își adaugă propria extensie.

Primele variabile din listă se completează din XML-ul facturii, deci pentru
mesajele fără XML (fișiere de eroare, mesaje de la cumpărător) devin `unknown`.
Doar `cif`, `direction`, `message_id`, `request_id` și `message_type` există
întotdeauna; `created` și `created_month` vin din listarea ANAF și pot deveni
`unknown` doar în cazuri rare. Un șablon construit exclusiv din variabilele
derivate din XML adună toate mesajele fără XML pe aceeași cale.

Orice variabilă acceptă o conversie de capitalizare: `{issue_month!u}` →
`IULIE`, `{issue_month!c}` → `Iulie`, `{issue_month!l}` → `iulie` (implicit
numele lunilor sunt cu literă mică, conform normelor limbii române), iar
`{partner_name!t}` → `Furnizor Srl` (fiecare cuvânt cu majusculă). Pentru
foldere sortate cronologic, combină numărul și numele lunii:
`{issue_date:%m}-{issue_month}` → `07-iulie`.

## Rulare

```bash
anaf-sync sync --dry-run     # arată ce s-ar descărca, fără să scrie nimic
anaf-sync sync               # descarcă tot ce e nou
anaf-sync sync --days 7      # restrânge fereastra doar pentru această rulare (1–60)
anaf-sync sync --redownload  # re-descarcă tot — util după schimbarea șablonului
```

Rulările sunt idempotente: un fișier de stare reține id-urile mesajelor deja
arhivate, așa că ferestrele de 60 de zile care se suprapun nu duplică
niciodată nimic, iar ce urmează ANAF să șteargă a fost deja capturat.
`--redownload` sare peste această evidență și aduce din nou tot ce e încă în
SPV, rescriind fișierele pe căile date de șablonul curent.

## Programare

```bash
anaf-sync schedule install --every 6h        # sau --daily-at 07:30
anaf-sync schedule status
anaf-sync schedule remove
```

Aceasta înregistrează sincronizarea în planificatorul nativ al sistemului —
Task Scheduler pe Windows, un timer systemd de utilizator pe Linux
(`loginctl enable-linger $USER` ca să ruleze și fără sesiune deschisă),
launchd pe macOS. Fără daemon propriu.

## Aplicația din bara de sistem (opțional)

Un companion desktop discret afișează starea arhivei printr-o iconiță în bara
de sistem, ca să vezi din timp când o sincronizare se strică — înainte ca ANAF
să șteargă mesajele după 60 de zile. Culoarea punctului de stare înseamnă:

- **verde** — arhiva este la zi;
- **galben** — necesită atenție: o factură eșuează repetat sau a fost declarată
  cu întârziere;
- **roșu** — sincronizarea nu funcționează (de obicei autentificarea ANAF a
  expirat — rulează `anafpy auth login`).

Din meniu poți porni o sincronizare, deschide folderul arhivei, răsfoi facturile
arhivate și edita configurația — fără să atingi `config.toml` manual (deși
rămâne editabil manual oricând). Aplicația doar citește arhiva și scrie
`config.toml`; orice descărcare o face tot `anaf-sync sync`.

Instalare (adaugă dependențele grafice PySide6):

```bash
pip install "anaf-sync[tray]"
anaf-sync-tray                 # pornește aplicația
anaf-sync tray install         # pornire automată la logare (idempotent)
anaf-sync tray status
anaf-sync tray remove
```

Alternativ, descarcă un pachet gata compilat de la secțiunea Releases (nu
necesită Python). Pachetele nu sunt semnate deocamdată, așa că la prima
pornire sistemul afișează un avertisment: pe macOS deschide-l cu click‑dreapta
→ „Open" o singură dată; pe Windows alege „More info" → „Run anyway".

Pe Linux/GNOME iconițele din bară au nevoie de extensia AppIndicator
(„AppIndicator and KStatusNotifierItem Support"); pe majoritatea celorlalte
medii desktop funcționează direct.

## Jurnale

Rulările interactive afișează jurnale lizibile în consolă. Rulările
programate (orice rulare fără TTY) scriu direct în facilitatea nativă de
jurnalizare a platformei, deci le inspectezi cu uneltele sistemului — fără
fișiere de log proprii:

```powershell
# Windows — jurnalul de evenimente Application, sursa "anaf-sync"
Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='anaf-sync'} -MaxEvents 20
```

```bash
# macOS — unified log, subsistemul "ro.anaf-sync"
log show --last 1d --info --predicate 'subsystem == "ro.anaf-sync"'
log stream --predicate 'subsystem == "ro.anaf-sync"'   # live, în timpul unui sync

# Linux — journald (și: journalctl --user -u anaf-sync.service)
journalctl --user SYSLOG_IDENTIFIER=anaf-sync --since today
journalctl --user SYSLOG_IDENTIFIER=anaf-sync -p err   # doar erorile
```

Fiecare rulare emite un eveniment-sumar `sync_done` plus evenimente
per-mesaj (`archived`, `download_failed`, …); severitățile se mapează pe
nivelurile native, deci filtrele „doar erori" funcționează peste tot. Setează
`ANAF_SYNC_LOG=console` sau `=system` ca să forțezi modul, peste detecția de
TTY.

## Dezvoltare

Documentația pentru dezvoltatori e în engleză: [CONTRIBUTING.md](CONTRIBUTING.md)
(setup și quality gates), [DESIGN.md](DESIGN.md) (design rationale).
