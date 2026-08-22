; Inno Setup script for the anaf-sync Windows installer.
;
; Consumes the PyInstaller one-dir output (`dist\anaf-sync-tray\`, built from
; tray.spec — see its header: the directory carries BOTH executables) and wraps
; it in a single setup.exe. Build from the repo root:
;
;     set ANAF_SYNC_VERSION=0.8.0 && iscc packaging\windows-setup.iss
;
; The version has no default on purpose: `release-tray.yml` reads it from
; pyproject.toml and passes it in, so the installer can never announce a
; version the bundle it carries does not have. CI passes it through the
; environment rather than /D — Git Bash on the Windows runners rewrites any
; argument that starts with a slash into a Windows path, so `/DAppVersion=...`
; reached ISCC as a second script filename.
;
; PER-USER, deliberately, and not a shortcut to avoid a UAC prompt: everything
; this app registers is per-user. `autostart.py` writes HKCU\...\Run and
; `scheduling.py` registers a *user* schtasks task; the config, the archive DB
; and the anafpy token store all live under the running user's profile. An
; elevated per-machine install would register the schedule and the autostart
; for the administrator who ran it, not for the operator who uses it.
;
; Known gap (out of scope): the installer is UNSIGNED, so SmartScreen shows
; "Windows protected your PC" on first run. See README for the workaround.

#ifndef AppVersion
  #define AppVersion GetEnv("ANAF_SYNC_VERSION")
#endif
#if AppVersion == ""
  #error "Set ANAF_SYNC_VERSION, or pass /DAppVersion=x.y.z (from cmd/PowerShell)"
#endif

#define AppName "anaf-sync"
#define AppPublisher "Robert Malai"
#define AppURL "https://github.com/robert-malai/anaf-sync"
#define TrayExe "anaf-sync-tray.exe"
#define CliExe "anaf-sync.exe"

[Setup]
; Never change AppId: it is how every future installer recognises — and
; upgrades in place — what this one installed.
AppId={{87B663E6-3B5B-46E5-9FDF-9E3ED703E172}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
; With PrivilegesRequired=lowest, {autopf} resolves to {localappdata}\Programs.
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=anaf-sync-setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The tray holds its own exe and Qt's DLLs open, so an upgrade over a running
; instance would fail on locked files; Restart Manager closes it first.
CloseApplications=yes
RestartApplications=no
LicenseFile=..\LICENSE
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#TrayExe}

; English wizard chrome, Romanian option labels — a deliberate split, not an
; oversight. Inno bundles no Romanian translation: `Romanian.isl` is one of the
; *unofficial* ones, and one flagged as out of date for current Inno at that,
; so naming it here would either fail the build or vendor an unmaintained file
; into the release pipeline. The standard wizard strings (Next, Browse, Install)
; survive that fine; the strings an operator has to make a decision about are
; the [CustomMessages] below, and those are Romanian.
[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

; Unprefixed: with one language they apply to it.
[CustomMessages]
TrayTask=Pornește aplicația din bara de sistem la logare
ScheduleTask=Sincronizează automat la fiecare 6 ore (necesită configurare și autentificare în prealabil)
DesktopIcon=Creează o scurtătură pe desktop
LaunchTray=Pornește anaf-sync acum
OptionalGroup=Opțiuni suplimentare:

[Tasks]
Name: "autostart"; Description: "{cm:TrayTask}"; GroupDescription: "{cm:OptionalGroup}"
; Unchecked by default, and the description says why: a schedule registered
; before `anaf-sync init` and `anafpy auth login` would run every six hours
; only to fail every six hours.
Name: "schedule"; Description: "{cm:ScheduleTask}"; GroupDescription: "{cm:OptionalGroup}"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:OptionalGroup}"; Flags: unchecked

[Files]
; The whole one-dir bundle: both executables, Qt, and the Python runtime.
Source: "..\dist\anaf-sync-tray\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#TrayExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#TrayExe}"; Tasks: desktopicon

[Run]
; The CLI registers both of these itself — the installer never writes the Run
; key or calls schtasks directly, so there is exactly one implementation of
; each and `anaf-sync tray status` / `schedule status` keep telling the truth.
Filename: "{app}\{#CliExe}"; Parameters: "tray install"; Tasks: autostart; Flags: runhidden waituntilterminated
Filename: "{app}\{#CliExe}"; Parameters: "schedule install --every 6h"; Tasks: schedule; Flags: runhidden waituntilterminated
Filename: "{app}\{#TrayExe}"; Description: "{cm:LaunchTray}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Before the files go: both commands run the CLI out of {app}. Skipping this
; would leave a scheduled task and an HKCU\Run value pointing at a deleted
; executable — a login-time error dialog and a task that fails forever, with
; nothing left on disk to explain either. Failures here are ignored by design
; (schtasks /Delete exits non-zero when there is no task, which is fine).
Filename: "{app}\{#CliExe}"; Parameters: "schedule remove"; RunOnceId: "RemoveSchedule"; Flags: runhidden waituntilterminated
Filename: "{app}\{#CliExe}"; Parameters: "tray remove"; RunOnceId: "RemoveAutostart"; Flags: runhidden waituntilterminated

; Deliberately absent: an [UninstallDelete] for the config, the archive
; database or the downloaded invoices. Uninstalling the tool must never delete
; the archive it exists to protect — those files are the point.
