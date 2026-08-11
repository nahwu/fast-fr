#define MyAppName "Fast-FR"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Fast-FR"
#define MyAppExeName "FastFR.exe"

[Setup]
AppId={{67900995-2D2F-4B07-A7A8-5D64DDF822C1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Fast-FR
DefaultGroupName=Fast-FR
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\build\installer
OutputBaseFilename=FastFR-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\build\FastFR\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Fast-FR"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Fast-FR"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Fast-FR"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Application data and biometric records intentionally remain in %LOCALAPPDATA%\FastFR.
Type: filesandordirs; Name: "{app}"
