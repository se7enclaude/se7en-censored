; Inno Setup 6 — se7en installers (built by packaging\build.ps1).
;
;   full     ISCC /DAppVersion=1.1.0 installer.iss                 -> dist\se7en-Censored-Setup-1.1.0.exe
;            everything: program, FFmpeg, Deno, the NudeNet detector
;   update   ISCC /DAppVersion=1.1.0 /DUpdateOnly installer.iss    -> dist\se7en-Censored-Update-1.1.0.exe
;            the program only (bin\ is kept): what automatic updates download
;
; Per-user install (no administrator prompt), so automatic updates run silently.
; User data (settings, projects) lives in %LOCALAPPDATA%\se7enCensored and is never removed.

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\se7en-censored"
#endif
#define AppName "se7en Censored"
#define AppExe "se7en-censored.exe"

[Setup]
AppId={{CB283103-F57B-4DE4-9203-954EB272FD92}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=se7en
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile=wizard-large.bmp
WizardSmallImageFile=wizard-small.bmp
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=force
RestartApplications=no
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=8
LZMAUseSeparateProcess=yes
#ifdef UpdateOnly
OutputBaseFilename=se7en-Censored-Update-{#AppVersion}
DisableWelcomePage=yes
DisableDirPage=yes
DisableReadyPage=yes
#else
OutputBaseFilename=se7en-Censored-Setup-{#AppVersion}
DisableWelcomePage=no
#endif

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fr.LaunchSe7en=Lancer se7en Censored
en.LaunchSe7en=Launch se7en Censored
fr.NotInstalled=se7en Censored n'est pas installé sur ce PC. Installez d'abord se7en-Censored-Setup.
en.NotInstalled=se7en Censored is not installed on this PC. Install se7en-Censored-Setup first.
fr.WebView2Fail=Le composant Microsoft WebView2 n'a pas pu être installé automatiquement. se7en s'ouvrira dans votre navigateur ; vous pouvez l'installer plus tard depuis https://developer.microsoft.com/microsoft-edge/webview2/
en.WebView2Fail=The Microsoft WebView2 Runtime could not be installed automatically. se7en will open in your web browser; you can install it later from https://developer.microsoft.com/microsoft-edge/webview2/

[Tasks]
#ifndef UpdateOnly
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
#endif

[InstallDelete]
; the program folder is replaced as a whole (no stale libraries after an update); bin\ and user data stay
Type: filesandordirs; Name: "{app}\_internal"

[Files]
#ifdef UpdateOnly
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "bin\*,portable.flag"; Flags: ignoreversion recursesubdirs createallsubdirs
#else
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "portable.flag"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
#ifndef UpdateOnly
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
#endif

[Registry]
; "Open with > se7en" for media files (never takes over the default program)
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "se7en Censored"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".mp4"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".mkv"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".webm"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".mov"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".avi"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".mp3"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".m4a"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".flac"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".wav"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".opus"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".ogg"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\se7en-censored.exe\SupportedTypes"; ValueType: string; ValueName: ".wma"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\.mp4\OpenWithList\se7en-censored.exe"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.mkv\OpenWithList\se7en-censored.exe"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.mp3\OpenWithList\se7en-censored.exe"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\.flac\OpenWithList\se7en-censored.exe"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchSe7en}"; Flags: nowait postinstall skipifsilent
; automatic update: se7en closed itself, the new version restarts on its own
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: ShouldRelaunch

[UninstallDelete]
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\_internal"

[Code]
const
  WV2_KEY = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WV2_URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  UNINST_KEY = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{5B7C0E0A-7A51-4C6E-9C43-6D1B6F0A6E11}_is1';

var
  DownloadPage: TDownloadWizardPage;

function ShouldRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function WebView2Installed(): Boolean;
var v: String;
begin
  Result := (RegQueryStringValue(HKLM, WV2_KEY, 'pv', v) and (v <> '') and (v <> '0.0.0.0')) or
            (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0'));
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
#ifdef UpdateOnly
  // The update package does not contain FFmpeg / AI models: it needs an existing installation.
  if not RegKeyExists(HKCU, UNINST_KEY) and not RegKeyExists(HKLM, UNINST_KEY) then begin
    if not WizardSilent() then MsgBox(CustomMessage('NotInstalled'), mbError, MB_OK);
    Result := False;
  end;
#endif
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), 'Microsoft Edge WebView2 Runtime', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var ResultCode: Integer;
begin
  Result := True;
  if (CurPageID = wpReady) and (not WebView2Installed()) then begin
    DownloadPage.Clear;
    DownloadPage.Add(WV2_URL, 'MicrosoftEdgeWebview2Setup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
      except
        MsgBox(CustomMessage('WebView2Fail'), mbInformation, MB_OK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;
