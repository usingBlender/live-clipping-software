; Inno Setup script for the Windows installer.
; CI builds it with:  iscc /DMyAppVersion=1.2.3 packaging\windows\gta-clipper.iss
; Per-user install (no admin prompt), so the in-app updater can run it silently.

#define MyAppName "GTA Clipper"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{8F6C2E4A-5B1D-4C7E-9A3F-2D6B8E1C4F70}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=GTA Clipper
DefaultDirName={localappdata}\Programs\GTA Clipper
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=GTA-Clipper-{#MyAppVersion}-setup
SetupIconFile=gta-clipper.ico
UninstallDisplayIcon={app}\gta-clipper.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "autostart"; Description: "Start GTA Clipper automatically when I log in (recommended)"

[Files]
Source: "..\..\build\app\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\..\build\uv.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "gta-clipper.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "bootstrap.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\GTA Clipper"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m web"; WorkingDir: "{app}"; IconFilename: "{app}\gta-clipper.ico"; Comment: "Open GTA Clipper"
Name: "{userstartup}\GTA Clipper"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m web --supervise --no-browser"; WorkingDir: "{app}"; IconFilename: "{app}\gta-clipper.ico"; Tasks: autostart

[Run]
; 1) stop a running copy so its Python files aren't locked
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -like '{app}\*' } | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"""; Flags: runhidden waituntilterminated; StatusMsg: "Stopping GTA Clipper..."
; 2) Python + dependencies (first install downloads ~1-2 GB)
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\bootstrap.ps1"""; Flags: runhidden waituntilterminated; StatusMsg: "Installing Python and dependencies (first install can take several minutes)..."
; 3) start it (also after silent auto-updates)
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m web --supervise"; WorkingDir: "{app}"; Flags: nowait

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -like '{app}\*' } | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"""; Flags: runhidden; RunOnceId: "StopApp"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\python"
