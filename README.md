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
serverul MCP anafpy. Certificatul digital e necesar **doar la autorizarea
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

anafpy auth login --redirect-uri https://localhost:8765/callback --paste
```

Se deschide browserul, îți alegi certificatul, iar ANAF redirecționează către
callback. Cu `--paste` nu pornește niciun server local: browserul va afișa o
eroare de conexiune, dar bara de adrese conține URL-ul complet cu codul de
autorizare — îl copiezi în terminal. (Alternativ, cu un certificat TLS local —
de ex. generat cu [mkcert](https://github.com/FiloSottile/mkcert) —
`--tls-cert`/`--tls-key` capturează redirectul automat, fără copiat.)

Token-urile se salvează în credential store-ul sistemului de operare. Pe
mașini fără credential store (servere headless), folosește varianta pe
fișier: `ANAFPY_TOKEN_STORE_BACKEND=file` și
`ANAFPY_TOKEN_STORE=~/.anafpy/tokens.json`.

`ANAFPY_CLIENT_ID` și `ANAFPY_CLIENT_SECRET` trebuie să rămână setate (în
mediu sau în `.env`) și după login: cu ele își reîmprospătează rulările
programate token-urile expirate. Token-ul de acces ține ~90 de zile,
refresh-ul ~365 — browserul și certificatul revin în joc doar când expiră și
acesta.

## Configurare

```bash
anaf-sync init            # scrie un config.toml comentat
anaf-sync status          # arată unde se află fișierul pe platforma ta
```

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
`iulie`), `currency`, `total`, `kind`, `direction`, `cif`,
`seller_name`/`seller_cif`, `buyer_name`/`buyer_cif`,
`partner_name`/`partner_cif` (*cealaltă* parte, indiferent de direcție),
`message_id`, `request_id`, `message_type`, `created`. Valorile substituite
sunt sanitizate pentru sistemul de fișiere; un `/` literal în șablon creează
foldere; fiecare artefact își adaugă propria extensie.

Orice variabilă acceptă o conversie de capitalizare: `{issue_month!u}` →
`IULIE`, `{issue_month!c}` → `Iulie`, `{issue_month!l}` → `iulie` (implicit
numele lunilor sunt cu literă mică, conform normelor limbii române), iar
`{partner_name!t}` → `Furnizor Srl` (fiecare cuvânt cu majusculă). Pentru
foldere sortate cronologic, combinați numărul și numele lunii:
`{issue_date:%m}-{issue_month}` → `07-iulie`.

## Rulare

```bash
anaf-sync sync --dry-run   # arată ce s-ar descărca, fără să scrie nimic
anaf-sync sync             # descarcă tot ce e nou
```

Rulările sunt idempotente: un fișier de stare reține id-urile mesajelor deja
arhivate, așa că ferestrele de 60 de zile care se suprapun nu duplică
niciodată nimic, iar ce urmează ANAF să șteargă a fost deja capturat.

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
(setup și quality gates), [DESIGN.md](DESIGN.md) (rațiunea arhitecturii).
