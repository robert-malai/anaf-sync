# anaf-sync

<p>
  <a href="https://github.com/robert-malai/anaf-sync/actions/workflows/ci.yml"><img
    src="https://img.shields.io/github/actions/workflow/status/robert-malai/anaf-sync/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="https://codecov.io/gh/robert-malai/anaf-sync"><img
    src="https://img.shields.io/codecov/c/github/robert-malai/anaf-sync?branch=main" alt="Coverage"></a>
  <a href="https://pypi.org/project/anaf-sync/"><img
    src="https://img.shields.io/pypi/v/anaf-sync" alt="PyPI version"></a>
  <a href="https://pepy.tech/project/anaf-sync"><img
    src="https://img.shields.io/pepy/dt/anaf-sync" alt="Downloads"></a>
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
Python-ul potrivit. Ce s-a schimbat la fiecare versiune găsești în
[Releases](https://github.com/robert-malai/anaf-sync/releases).

### Instalare asistată, cu Claude

Dacă folosești [Claude Code](https://claude.com/claude-code), un skill de
instalare parcurge tot ce urmează în acest ghid — instalare, autentificare,
configurare, prima sincronizare și programarea — pas cu pas, pe calculatorul
tău:

```
/plugin marketplace add robert-malai/anafpy
/plugin install anaf-sync-setup@anafpy
```

apoi cere pur și simplu „instalează anaf-sync". Skill-ul e sigur de re-rulat
oricând — de exemplu când sincronizarea s-a stricat sau a expirat login-ul
anual ANAF.

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

Comanda `anafpy` e a pachetului anafpy — `uv tool install anaf-sync` nu o
expune. Login-ul e însă un pas pe care îl faci o singură dată, așa că cel mai
simplu îl rulezi cu `uvx`, fără nicio instalare (token-ul se scrie oricum pe
disc, deci rezultatul e permanent):

```bash
export ANAFPY_CLIENT_ID=...          # sau într-un fișier .env
export ANAFPY_CLIENT_SECRET=...

uvx anafpy auth login --redirect-uri https://localhost:8765/callback
```

Dacă vrei comanda `anafpy` permanent pe PATH — de exemplu o folosești deja
pentru serverul MCP anafpy — instaleaz-o o dată ca unealtă uv, cu
`uv tool install anafpy`, și apoi rulezi `anafpy auth login ...` direct.

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
anaf-sync init 12345678             # config.toml comentat, cu CIF-ul tău în el
anaf-sync init 12345678 87654321    # mai multe firme deodată
anaf-sync status                    # arată unde se află fișierul pe platforma ta
```

CIF-ul e obligatoriu — fișierul se scrie gata configurat, nu cu un exemplu pe
care să-l uiți neînlocuit. Prefixul `RO` e opțional (se elimină automat).

Fișierul generat e comentat și acoperă toate cheile: `cif = "12345678"` (sau
`cifs = ["...", "..."]` pentru mai multe firme), `direction` (`received`,
`sent` sau `both`), `lookback_days` (1–60 — limita de retenție ANAF) și
`failure_retention_days`, plus secțiunea `[output]` de mai jos. Dacă vrei
config-ul în altă parte, `--config`/`-c` (sau variabila de mediu
`ANAF_SYNC_CONFIG`) funcționează la orice comandă; `anaf-sync init <CIF>
--force` suprascrie un fișier existent.

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

### Ce se salvează pentru fiecare factură

`artifacts` alege ce ajunge pe disc: `zip` (arhiva semnată, exact cum o dă
ANAF), `xml` (UBL-ul facturii), `signature` (semnătura detașată a Ministerului
Finanțelor), `pdf` (randarea făcută de ANAF) și `metadata` (un fișier JSON cu
detaliile mesajului).

Tentația e să păstrezi doar PDF-ul — e singurul pe care îl citești efectiv.
Merită totuși să lași `zip`-ul în listă: el e originalul semnat, iar toate
celelalte se obțin din el (XML-ul și semnătura sunt fișierele din interiorul
lui, iar PDF-ul e o randare a XML-ului). Invers nu funcționează: dintr-o arhivă
numai cu PDF-uri nu mai poți reconstitui nimic, iar ANAF nu îți mai dă factura
după 60 de zile. Pe scurt: PDF-ul e ce citești, `zip`-ul e ce păstrezi.

Dacă niciun artefact configurat nu poate fi scris pentru un mesaj — de exemplu
o arhivă doar cu `pdf`, iar serviciul de randare al ANAF refuză documentul —
mesajul **nu** e marcat ca arhivat: rularea îl raportează ca eșec, îl vezi în
`anaf-sync status` și se reîncearcă automat la următoarea rulare, cât timp
fereastra de 60 de zile e încă deschisă.

## Rulare

```bash
anaf-sync sync --dry-run     # arată ce s-ar descărca, fără să scrie nimic
anaf-sync sync               # descarcă tot ce e nou
anaf-sync sync --days 7      # restrânge fereastra doar pentru această rulare (1–60)
anaf-sync sync --redownload  # re-descarcă tot ce mai e în SPV
```

Rulările sunt idempotente: un fișier de stare reține id-urile mesajelor deja
arhivate, așa că ferestrele de 60 de zile care se suprapun nu duplică
niciodată nimic, iar ce urmează ANAF să șteargă a fost deja capturat.
`--redownload` sare peste această evidență și aduce din nou tot ce e încă în
SPV, rescriind fișierele pe căile date de șablonul curent. Pentru simpla
rearanjare a arhivei după schimbarea șablonului există însă o cale mai bună,
care nu depinde de fereastra de 60 de zile: `anaf-sync reprocess --move`
(vezi mai jos).

### PDF-uri lipsă

Se poate întâmpla ca descărcarea să reușească, dar randarea PDF-ului să fie
refuzată de ANAF — firewall-ul lor respinge uneori XML-ul unor facturi perfect
valide. Mesajul e arhivat atunci doar cu ZIP-ul (originalul semnat nu se pierde
niciodată), iar PDF-ul rămâne pe lista de așteptare.

Nu trebuie făcut nimic: la sfârșitul fiecărei rulări, `sync` reîncearcă automat
PDF-urile lipsă, direct din ZIP-urile deja salvate — fără autentificare și fără
limita de 60 de zile, pentru că XML-ul e deja pe disc. Când există astfel de
restanțe, sumarul rulării capătă o linie `pdf repair: …`.

Același pas există și ca o comandă de sine stătătoare, ca să repari o arhivă
existentă pe loc, fără să aștepți următoarea rulare programată:

```bash
anaf-sync render --dry-run   # arată ce PDF-uri lipsesc și s-ar putea randa
anaf-sync render             # le randează din ZIP-urile salvate
```

Un refuz repetat nu e o eroare de-a ta: se reîncearcă la fiecare rulare și se
rezolvă de la sine când ANAF acceptă documentul.

### Facturi care apar cu `unknown`

Uneori o factură ajunge în arhivă, dar fără date: în aplicația din bara de
sistem apare fără număr, fără partener și fără valoare, iar pe disc stă
într-un folder `unknown`. Descărcarea a reușit — doar citirea XML-ului nu:
ANAF a acceptat un document pe care versiunea instalată de `anafpy` nu îl
poate încă interpreta. Factura în sine e intactă, ZIP-ul semnat e la locul lui.

Nu e nevoie să o descarci din nou (și nici nu s-ar putea, după 60 de zile):
tot ce lipsește se recalculează din ZIP-ul deja salvat, oricând, fără
autentificare și fără limită de timp.

```bash
anaf-sync reprocess --dry-run          # arată ce s-ar corecta
anaf-sync reprocess                    # recitește ZIP-urile și corectează catalogul
anaf-sync reprocess --move --dry-run   # și unde s-ar muta fișierele
anaf-sync reprocess --move             # le mută pe calea dată de șablon
```

Pentru o singură factură există și butonul **„Recitește din arhivă”** din
fereastra Facturi a aplicației din bara de sistem; pe rândurile fără date apare
evidențiat, cu explicația de mai sus. Din linia de comandă, aceeași restrângere
se face cu `--message-id`:

```bash
anaf-sync reprocess --move --message-id 3210447811
```

Fără `--move`, comanda schimbă doar ce se vede în aplicație — fișierele rămân
exact unde sunt. Cu `--move`, recalculează și calea din șablon și mută acolo
toate fișierele facturii, golind folderul `unknown` rămas în urmă. Dacă o
factură tot nu poate fi citită, comanda o numără separat, pe `unreadable`:
atunci merită raportată, iar o versiune mai nouă de `anafpy` plus încă o rulare
a aceleiași comenzi rezolvă restul.

`--move` e și modul corect de a reorganiza arhiva **după ce schimbi
`template`**: rearanjează fișierele deja descărcate, local, fără să mai ceară
nimic de la ANAF — spre deosebire de `sync --redownload`, care le poate aduce
din nou doar pe cele încă aflate în fereastra de 60 de zile. Rulează întâi cu
`--dry-run`, ca să vezi câte fișiere s-ar muta.

## Facturi mai vechi de 60 de zile

ANAF păstrează mesajele 60 de zile și atât. Orice factură mai veche de-atât nu
mai poate fi descărcată — dar dacă ai deja arhivele ZIP pe disc (descărcate
manual din SPV, de contabil, sau de o instalare anterioară), `backfill` le
citește și le trece în catalog:

```bash
anaf-sync backfill ~/Arhiva-veche --dry-run   # arată ce ar cataloga
anaf-sync backfill ~/Arhiva-veche             # citește și catalogează
```

Comanda **doar citește**: nu descarcă, nu mută și nu redenumește nimic —
fișierele rămân exact unde sunt. Din fiecare ZIP scoate numărul, data, partenerul,
valoarea și moneda, iar sensul facturii (primită sau trimisă) îl deduce din CIF-urile
din documentul propriu-zis. Facturile între alte firme decât ale tale sunt sărite
și doar numărate.

Două lucruri nu se pot reconstitui din fișiere, pentru că există numai în
listarea ANAF: tipul mesajului și data la care factura a intrat în SPV — deci
pentru rândurile aduse prin `backfill` verificarea „declarată cu întârziere” nu
are pe ce să se bazeze. Din acelaşi motiv aceste rânduri **nu** blochează
descărcările: dacă o factură catalogată astfel e încă în fereastra de 60 de zile,
`sync` o va aduce oricum de la ANAF, cu id-ul ei real. Poți rula comanda de
câte ori vrei — a doua oară actualizează aceleași rânduri, nu le dublează.

Aceeași comandă reface catalogul dacă baza de date se pierde: rulează
`backfill` peste folderul din `[output] directory` și rândurile care lipsesc
sunt recitite din ZIP-uri. Cele deja catalogate sunt lăsate neatinse — id-ul lor
real de la ANAF e mai bun decât orice se poate deduce de pe disc.

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

`schedule status` — și rândul `schedule:` din `anaf-sync status` — citește
ritmul chiar din planificator și îl arată și ca expresie cron, ca să vezi dintr-o
privire ce s-a instalat de fapt:

```
launchd agent ro.anaf-sync.sync: loaded — runs daily at 07:30 (cron: 30 7 * * *)
```

Expresia cron descrie doar ritmul, nu și punctul de pornire: un interval
(`--every`) se numără de la momentul instalării, deci orele reale pot fi
decalate față de cele din cron. Când ritmul nu încape exact într-o expresie
cron (`--every 45m`, `--every 2d`), nu e afișată niciuna — doar intervalul.

## Aplicația din bara de sistem (opțional)

Un companion desktop discret afișează starea arhivei printr-o iconiță în bara
de sistem, ca să vezi din timp când o sincronizare se strică — înainte ca ANAF
să șteargă mesajele după 60 de zile. Culoarea punctului de stare înseamnă:

- **verde** — arhiva este la zi;
- **galben** — necesită atenție: o factură eșuează repetat sau a fost declarată
  cu întârziere;
- **roșu** — sincronizarea nu funcționează (de obicei autentificarea ANAF a
  expirat — rulează `uvx anafpy auth login`, ca la Pasul 3).

Din meniu poți porni o sincronizare, deschide folderul arhivei, răsfoi facturile
arhivate și edita configurația — fără să atingi `config.toml` manual (deși
rămâne editabil manual oricând). Aplicația doar citește arhiva și scrie
`config.toml`; orice descărcare o face tot `anaf-sync sync`.

În fereastra **Facturi**, panoul din dreapta are pentru fiecare factură un buton
**„Recitește din arhivă”**: recalculează datele din fișierul deja salvat și, dacă
e cazul, mută factura pe calea dată de șablon. E aceeași operație ca
`anaf-sync reprocess --move`, doar că pentru o singură factură — util mai ales
la facturile [afișate cu `unknown`](#facturi-care-apar-cu-unknown), unde butonul
apare evidențiat, sub o explicație a motivului.

Instalare (adaugă dependențele grafice PySide6):

```bash
pip install "anaf-sync[tray]"
anaf-sync-tray                 # pornește aplicația
anaf-sync tray install         # pornire automată la logare (idempotent)
anaf-sync tray status
anaf-sync tray remove
```

### Pachete gata compilate

Alternativ, descarcă un pachet gata făcut din
[secțiunea Releases](https://github.com/robert-malai/anaf-sync/releases/latest)
— nu trebuie să instalezi nici Python, nici PySide6.

**Windows** — `anaf-sync-setup-X.Y.Z.exe`

Instalatorul se instalează **doar pentru utilizatorul tău**, deci nu cere
drepturi de administrator. În timpul instalării poți bifa pornirea automată la
logare și, opțional, sincronizarea automată la fiecare 6 ore — pe a doua las-o
nebifată până termini configurarea și autentificarea de mai sus, altfel se
programează o sarcină care nu are ce să facă. Dezinstalarea (Setări →
Aplicații) scoate și sarcina programată, și pornirea automată; **arhiva de
facturi și `config.toml` rămân neatinse** — nu se șterge nimic din ce ai
descărcat. Există și `anaf-sync-tray-windows.zip`, aceeași aplicație fără
instalator, pentru calculatoarele pe care nu se pot rula instalatoare.

**macOS** — două imagini de disc, alege-o pe cea potrivită procesorului
(meniul Apple → „About This Mac"):

| Fișier | Pentru |
|---|---|
| `anaf-sync-tray-macos-arm64.dmg` | Apple Silicon (M1 și mai nou) |
| `anaf-sync-tray-macos-x86_64.dmg` | Intel |

Deschizi imaginea și tragi aplicația peste scurtătura „Applications" de lângă
ea. Ambele cer macOS 13 sau mai nou.

**Linux** — `anaf-sync-tray-linux.tar.gz`: dezarhivezi oriunde și rulezi
`anaf-sync-tray` din folderul rezultat.

Fiecare pachet conține **și** comanda `anaf-sync`, alături de aplicație:
programarea și butonul „Sincronizează acum" o folosesc direct de acolo. Dacă
vrei să dai comenzi în terminal (`anaf-sync init`, `anaf-sync status`),
folosește calea completă din locul instalării:

| Sistem | Calea către `anaf-sync` |
|---|---|
| Windows | `%LOCALAPPDATA%\Programs\anaf-sync\anaf-sync.exe` |
| macOS | `/Applications/anaf-sync-tray.app/Contents/MacOS/anaf-sync` |
| Linux | `anaf-sync-tray/anaf-sync`, în folderul dezarhivat |

Un singur lucru rămâne în afara pachetului: **autentificarea**. Comanda de
login e a pachetului anafpy, deci pentru ea îți trebuie uv —
`uvx anafpy auth login ...`, ca la [Pasul 3](#pasul-3--login).

#### Avertismentele de la prima pornire

Pachetele **nu sunt semnate digital** deocamdată, așa că sistemul le tratează
ca venind de la un dezvoltator necunoscut:

- **Windows** — SmartScreen afișează „Windows protected your PC". Alegi
  „More info", apoi „Run anyway".
- **macOS** — aplicația e blocată la prima deschidere. Mergi la meniul Apple →
  „System Settings" → „Privacy & Security", derulezi până la mesajul despre
  `anaf-sync-tray` și apeși „Open Anyway", apoi confirmi cu parola. Se cere o
  singură dată. (Vechiul truc cu click‑dreapta → „Open" nu mai funcționează pe
  versiunile recente de macOS.)

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
