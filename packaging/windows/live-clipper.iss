; Inno Setup script for the Windows installer.
; CI builds it with:  iscc /DMyAppVersion=1.2.3 packaging\windows\live-clipper.iss
; Per-user install (no admin prompt), so the in-app updater can run it silently.
; Python + dependencies are installed by uv.exe, called directly from [Code] (no PowerShell scripts,
; so it also works where company policy blocks .ps1 files). Log: %LOCALAPPDATA%\live-clipper\install.log

#define MyAppName "Live Clipper"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{AC2B5134-04B6-4222-8F59-2A989AC9DFCF}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Live Clipper
DefaultDirName={localappdata}\Programs\Live Clipper
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Live-Clipper-{#MyAppVersion}-setup
SetupIconFile=live-clipper.ico
UninstallDisplayIcon={app}\live-clipper.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "autostart"; Description: "Start Live Clipper automatically when I log in (recommended)"

[Files]
Source: "..\..\build\app\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\..\build\uv.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "live-clipper.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\Live Clipper"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m web"; WorkingDir: "{app}"; IconFilename: "{app}\live-clipper.ico"; Comment: "Open Live Clipper"
Name: "{userstartup}\Live Clipper"; Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m web --supervise --no-browser"; WorkingDir: "{app}"; IconFilename: "{app}\live-clipper.ico"; Tasks: autostart

[Run]
; start it (also after silent auto-updates) - only if the Python environment was created
Filename: "{app}\.venv\Scripts\pythonw.exe"; Parameters: "-m web --supervise"; WorkingDir: "{app}"; Flags: nowait; Check: VenvReady

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -Command ""Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -like '{app}\*' } | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"""; Flags: runhidden; RunOnceId: "StopApp"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"

[Code]
var
  LogFile: String;

function VenvReady(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\.venv\Scripts\pythonw.exe'));
end;

{ Runs a command hidden, appending its output to the install log. True when exit code is 0. }
function RunLogged(const Exe, Params, Status: String): Boolean;
var
  Code: Integer;
begin
  WizardForm.StatusLabel.Caption := Status;
  SaveStringToFile(LogFile, #13#10 + '> ' + Exe + ' ' + Params + #13#10, True);
  Result := Exec(ExpandConstant('{cmd}'), '/C ""' + Exe + '" ' + Params + ' >> "' + LogFile + '" 2>&1"',
                 '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  if not Result then
    SaveStringToFile(LogFile, '(failed, exit code ' + IntToStr(Code) + ')' + #13#10, True);
end;

{ Stop a running copy so its files aren't locked (only python processes started from our folder). }
procedure StopRunningApp();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like ''' +
    ExpandConstant('{app}') + '\*'' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  App, Uv, Py: String;
  Code: Integer;
  Ok: Boolean;
begin
  if CurStep = ssInstall then
    StopRunningApp();
  if CurStep = ssPostInstall then
  begin
    App := ExpandConstant('{app}');
    Uv := App + '\bin\uv.exe';
    Py := App + '\.venv\Scripts\python.exe';
    LogFile := ExpandConstant('{localappdata}\live-clipper\install.log');
    ForceDirectories(ExtractFileDir(LogFile));
    SaveStringToFile(LogFile, #13#10 + '=== Live Clipper {#MyAppVersion} install ' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + ' ===' + #13#10, True);

    Ok := RunLogged(Uv, 'venv --allow-existing --python 3.12 "' + App + '\.venv"', 'Installing Python 3.12...');
    if Ok then
      Ok := RunLogged(Uv, 'pip install --python "' + Py + '" -r "' + App + '\requirements.txt"',
                      'Installing dependencies (first install downloads 1-2 GB, this can take several minutes)...');

    { FFmpeg: install with winget if it isn't on PATH }
    if not Exec(ExpandConstant('{cmd}'), '/C where ffmpeg >NUL 2>&1', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RunLogged('winget', 'install -e --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements',
                'Installing FFmpeg...');

    if not Ok or not VenvReady() then
      SuppressibleMsgBox('Live Clipper could not finish installing Python and its dependencies.' + #13#10#13#10 +
        'Details are in:' + #13#10 + LogFile + #13#10#13#10 +
        'Check your internet connection (or company proxy/firewall) and run the installer again.',
        mbError, MB_OK, IDOK);
  end;
end;
