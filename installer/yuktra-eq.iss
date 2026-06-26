; ============================================================================
;  Yuktra-EQ Windows Installer (Inno Setup 6)
;  Builds install.exe (+ auto uninstaller). Installs the portable app into
;  Program Files, seeds data into ProgramData, registers the backend as an
;  auto-start Windows Service (NSSM), adds firewall rules, installs WebView2,
;  and creates desktop/Start-Menu icons.
;
;  Compile with:  iscc.exe yuktra-eq.iss   (run by build_installer.ps1)
;  Expects a staged  payload\  folder next to this script (see build_installer.ps1).
; ============================================================================

#define AppName       "Yuktra-EQ"
#define AppVersion    "1.0.0"
#define AppPublisher  "Emorphis"
#define SvcName       "YuktraEQBackend"
#define BackendExe    "yktra-eq-backend.exe"
#define RunnerExe     "webview-runner.exe"
#define LauncherExe   "yuktra-eq.exe"

[Setup]
AppId={{8E5C1A10-YUKT-RAEQ-0001-PRODUCTGUID00}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
OutputDir=..\dist_installer
OutputBaseFilename=yuktra-eq-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=payload\icons\yuktra.ico
UninstallDisplayIcon={app}\{#LauncherExe}
AlwaysRestart=no

[Files]
; Entire staged payload -> Program Files (app, webview-runner, python, launcher, tools, icons)
Source: "payload\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; Data is NOT compiled into the installer (build is data-less). If a `data\`
; folder is placed NEXT TO setup.exe, it is copied into ProgramData at install
; time; if absent, install still succeeds and data is provided separately.
; {src} = folder where setup.exe runs from; external = read at install time.
Source: "{src}\data\*"; DestDir: "{commonappdata}\{#AppName}\data"; Flags: external recursesubdirs createallsubdirs skipifsourcedoesntexist uninsneveruninstall

[Dirs]
Name: "{commonappdata}\{#AppName}\data"; Permissions: users-modify

[Icons]
Name: "{autodesktop}\{#AppName}";        Filename: "{app}\{#LauncherExe}"; IconFilename: "{app}\icons\yuktra.ico"
Name: "{group}\{#AppName}";              Filename: "{app}\{#LauncherExe}"; IconFilename: "{app}\icons\yuktra.ico"
Name: "{group}\Uninstall {#AppName}";    Filename: "{uninstallexe}"

[Registry]
; System-wide env vars so the service + UI both see the right paths/ports.
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "DATA_DIR";              ValueData: "{commonappdata}\{#AppName}\data"; Flags: preservestringtype uninsdeletevalue
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "YUKTRA_QNA_API_HOST";   ValueData: "127.0.0.1"; Flags: preservestringtype uninsdeletevalue
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "YUKTRA_QNA_API_PORT";   ValueData: "{code:GetBackendPort}"; Flags: preservestringtype uninsdeletevalue
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "YUKTRA_QNA_API_BASE";   ValueData: "http://127.0.0.1:{code:GetBackendPort}"; Flags: preservestringtype uninsdeletevalue
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "YUKTRA_QNA_SKIP_WARMUP"; ValueData: "1"; Flags: preservestringtype uninsdeletevalue
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "YUKTRA_PIPER_PYTHON";    ValueData: "{app}\python\python.exe"; Flags: preservestringtype uninsdeletevalue
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "YUKTRA_PIPER_MODEL_PATH";ValueData: "{commonappdata}\{#AppName}\data\models\piper\en_IN-medium.onnx"; Flags: preservestringtype uninsdeletevalue
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: string; ValueName: "YUKTRA_STT_PYTHON";      ValueData: "{app}\python\python.exe"; Flags: preservestringtype uninsdeletevalue
; Track data dir + chosen ports for the uninstaller (to delete the exact firewall rules).
Root: HKLM; Subkey: "Software\{#AppPublisher}\{#AppName}"; ValueType: string; ValueName: "DataDir";     ValueData: "{commonappdata}\{#AppName}\data"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\{#AppPublisher}\{#AppName}"; ValueType: string; ValueName: "BackendPort"; ValueData: "{code:GetBackendPort}"
Root: HKLM; Subkey: "Software\{#AppPublisher}\{#AppName}"; ValueType: string; ValueName: "UiPort";      ValueData: "{code:GetUiPort}"

[Code]
var
  PortPage: TInputQueryWizardPage;

function GetBackendPort(Param: string): string;
begin
  if Assigned(PortPage) and (Trim(PortPage.Values[0]) <> '') then
    Result := Trim(PortPage.Values[0])
  else
    Result := '8008';
end;

function GetUiPort(Param: string): string;
begin
  if Assigned(PortPage) and (Trim(PortPage.Values[1]) <> '') then
    Result := Trim(PortPage.Values[1])
  else
    Result := '8009';
end;

procedure InitializeWizard;
begin
  PortPage := CreateInputQueryPage(wpSelectDir,
    'Network ports', 'Choose the ports Yuktra-EQ will use',
    'Backend API and UI ports (defaults are fine for most setups).');
  PortPage.Add('Backend port:', False);
  PortPage.Add('UI port:', False);
  PortPage.Values[0] := '8008';
  PortPage.Values[1] := '8009';
end;

function WebView2Installed(): Boolean;
var
  v: string;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDB-FCBA1C7C9C2B}', 'pv', v) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDB-FCBA1C7C9C2B}', 'pv', v);
end;

procedure RunHidden(const Exe, Params: string);
var
  rc: Integer;
begin
  Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, rc);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  app, data, backend, nssm, bp, up: string;
begin
  if CurStep = ssPostInstall then
  begin
    app := ExpandConstant('{app}');
    data := ExpandConstant('{commonappdata}\{#AppName}\data');
    backend := app + '\{#BackendExe}';
    nssm := app + '\nssm.exe';
    bp := GetBackendPort('');
    up := GetUiPort('');

    { 1) WebView2 runtime (silent, only if missing) }
    if not WebView2Installed() then
      RunHidden(app + '\MicrosoftEdgeWebView2Setup.exe', '/silent /install');

    { 2) Backend as an auto-start Windows service via NSSM }
    RunHidden(nssm, 'install {#SvcName} "' + backend + '"');
    RunHidden(nssm, 'set {#SvcName} AppDirectory "' + app + '"');
    RunHidden(nssm, 'set {#SvcName} Start SERVICE_AUTO_START');
    RunHidden(nssm, 'set {#SvcName} AppStdout "' + data + '\logs\service.log"');
    RunHidden(nssm, 'set {#SvcName} AppStderr "' + data + '\logs\service.log"');
    RunHidden(nssm, 'set {#SvcName} AppEnvironmentExtra ' +
      'DATA_DIR=' + data + ' ' +
      'YUKTRA_QNA_API_HOST=127.0.0.1 ' +
      'YUKTRA_QNA_API_PORT=' + bp + ' ' +
      'YUKTRA_QNA_API_BASE=http://127.0.0.1:' + bp + ' ' +
      'YUKTRA_QNA_SKIP_WARMUP=1');
    RunHidden(nssm, 'start {#SvcName}');

    { 3) Firewall: program + port rules, inbound + outbound (prefix "YuktraEQ ") }
    RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall add rule name="YuktraEQ Backend In" dir=in action=allow program="' + backend + '" enable=yes');
    RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall add rule name="YuktraEQ Backend Out" dir=out action=allow program="' + backend + '" enable=yes');
    RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall add rule name="YuktraEQ Port ' + bp + ' In" dir=in action=allow protocol=TCP localport=' + bp + ' enable=yes');
    RunHidden(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall add rule name="YuktraEQ Port ' + up + ' In" dir=in action=allow protocol=TCP localport=' + up + ' enable=yes');
  end;
end;

function GetStoredPort(ValueName, Default: string): string;
var
  v: string;
begin
  if RegQueryStringValue(HKLM, 'Software\{#AppPublisher}\{#AppName}', ValueName, v) and (Trim(v) <> '') then
    Result := Trim(v)
  else
    Result := Default;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  app, data, nssm, sys, bp, up: string;
begin
  if CurUninstallStep = usUninstall then
  begin
    app  := ExpandConstant('{app}');
    nssm := app + '\nssm.exe';
    sys  := ExpandConstant('{sys}\netsh.exe');
    { Read the ports chosen at install time so we delete the exact firewall rules. }
    bp := GetStoredPort('BackendPort', '8008');
    up := GetStoredPort('UiPort', '8009');

    { 1) STOP + REMOVE the service. stop_app.ps1 also force-kills the LocalSystem
      backend process (a plain "nssm stop" can leave a hung SYSTEM process holding
      file locks, which blocks deletion of the install folder) and frees the ports.
      The uninstaller already runs elevated, so this does not re-prompt for UAC. }
    RunHidden('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -File "' + app + '\stop_app.ps1" -RemoveService -NoPause -InstallDir "' + app + '"');
    RunHidden(nssm, 'remove {#SvcName} confirm');  { belt-and-suspenders }

    { 2) REVERT ALL firewall rules we added (program rules + the chosen ports).
      (System env vars like DATA_DIR are reverted via uninsdeletevalue in [Registry].) }
    RunHidden(sys, 'advfirewall firewall delete rule name="YuktraEQ Backend In"');
    RunHidden(sys, 'advfirewall firewall delete rule name="YuktraEQ Backend Out"');
    RunHidden(sys, 'advfirewall firewall delete rule name="YuktraEQ Port ' + bp + ' In"');
    RunHidden(sys, 'advfirewall firewall delete rule name="YuktraEQ Port ' + up + ' In"');

    { 3) ASK whether to delete the data directory (models, vector store, chats, logs).
      The app program folder is removed automatically by the uninstaller. }
    if RegQueryStringValue(HKLM, 'Software\{#AppPublisher}\{#AppName}', 'DataDir', data) then
    begin
      if MsgBox('Also delete your data folder (documents, models, vector store, chat history and logs)?'
                + #13#10 + #13#10 + data,
                mbConfirmation, MB_YESNO) = IDYES then
        DelTree(data, True, True, True);
    end;
  end;
end;
