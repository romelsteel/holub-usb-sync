; Inno Setup skript pro instalátor Holuba (per-user, bez práv správce)
#define AppVer GetEnv("HOLUB_VERSION")
#if AppVer == ""
  #define AppVer "1.3.0"
#endif

[Setup]
AppId={{7B2F4C1E-5A3D-4E8B-9C61-HOLUB0USB001}
AppName=Holub
AppVersion={#AppVer}
AppPublisher=Tomáš Burčal
AppPublisherURL=https://github.com/romelsteel/holub-usb-sync
DefaultDirName={autopf}\Holub
DefaultGroupName=Holub
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=Holub-Setup-{#AppVer}
SetupIconFile=holub.ico
UninstallDisplayIcon={app}\Holub.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=Holub-USB-sync
CloseApplications=yes
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE

[Languages]
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
Name: "autostart"; Description: "Spouštět Holuba po přihlášení do Windows / Start Holub at Windows login"

[Files]
Source: "..\dist\Holub\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Holub"; Filename: "{app}\Holub.exe"
Name: "{autodesktop}\Holub"; Filename: "{app}\Holub.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Holub"; ValueData: """{app}\Holub.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; po tiché aktualizaci z appky (Check: WizardSilent) se Holub spustí znovu
Filename: "{app}\Holub.exe"; Description: "Spustit Holuba / Launch Holub"; Flags: nowait postinstall skipifsilent
Filename: "{app}\Holub.exe"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
Type: files; Name: "{userappdata}\Holub\dialog-vysledek.txt"
