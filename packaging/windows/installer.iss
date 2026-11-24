; Inno Setup script for RF Script Studio
;   iscc /DAppVersion=1.0.0 /DSourceDir=..\..\dist\RFScriptStudio packaging\windows\installer.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\RFScriptStudio"
#endif
#define AppName "RF Script Studio"
#define AppExe "rfscriptstudio.exe"

[Setup]
AppId={{6C1F0E4B-7B7E-4F7A-9D3B-5A0C2E8B9F11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=RFScriptStudio-{#AppVersion}-windows-x64-setup
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start {#AppName} when I log in (minimised to the tray)"; GroupDescription: "Background:"; Flags: unchecked
Name: "usbdriver"; Description: "Open Zadig to install the HackRF USB driver (WinUSB) - only needed if Windows doesn't detect the board"; GroupDescription: "HackRF driver:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} (start minimised)"; Filename: "{app}\{#AppExe}"; Parameters: "--minimized"
Name: "{group}\HackRF USB driver (Zadig)"; Filename: "{app}\_internal\drivers\zadig.exe"; WorkingDir: "{app}\_internal\drivers"; Check: ZadigBundled
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\_internal\drivers\zadig.exe"; WorkingDir: "{app}\_internal\drivers"; Description: "Install HackRF USB driver"; Flags: shellexec waituntilterminated; Tasks: usbdriver; Check: ZadigBundled
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""{#AppName}"" /F"; Flags: runhidden; RunOnceId: "RemoveTask"

[Code]
function ZadigBundled: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\_internal\drivers\zadig.exe'));
end;
