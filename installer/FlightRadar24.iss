; Compile with ISCC /DAppSource=C:\path\to\FlightRadar24 /DSetupOutput=C:\path\to\dist
; The build must already contain the offline WinUSB helper and its manifest.
#ifndef AppSource
  #define AppSource "..\work\package-native\FlightRadar24"
#endif
#ifndef SetupOutput
  #define SetupOutput "..\work\installer-dist"
#endif
#ifndef AppVersion
  #define AppVersion "0.4.0"
#endif
#if !FileExists(AddBackslash(AppSource) + "FlightRadar24.exe")
  #error "Build the complete native FlightRadar24 application first."
#endif
#if !FileExists(AddBackslash(AppSource) + "runtime\drivers\winusb\wdi-simple.exe")
  #error "The offline WinUSB helper is missing from the application package."
#endif
#if !FileExists(AddBackslash(AppSource) + "runtime\drivers\winusb\manifest.json")
  #error "The offline WinUSB driver manifest is missing from the application package."
#endif

[Setup]
AppId={{3A6F65B8-7F01-4F74-BDF8-18CB7E4CC305}
AppName=FlightRadar24
AppVersion={#AppVersion}
AppPublisher=Kieran O'Rourke
DefaultDirName={code:GetInstallDirectory}
DefaultGroupName=FlightRadar24
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\FlightRadar24.exe
OutputDir={#SetupOutput}
OutputBaseFilename=FlightRadar24-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
InfoBeforeFile=Setup-Info.txt

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Install-UsbDriver.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "Setup-Info.txt"; DestDir: "{app}\setup"; Flags: ignoreversion

[Icons]
Name: "{group}\FlightRadar24"; Filename: "{app}\FlightRadar24.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\FlightRadar24"; Filename: "{app}\FlightRadar24.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\FlightRadar24.exe"; Description: "Open FlightRadar24"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
var
  DriverSummary: String;
  DriverFailed: Boolean;
  DriverRestart: Boolean;

function GetInstallDirectory(Param: String): String;
var
  ExistingDirectory: String;
begin
  { Upgrade the earlier per-user portable installation when present. Profiles
    remain in their established directory regardless of application location. }
  ExistingDirectory := ExpandConstant('{localappdata}\Programs\FlightRadar24');
  if FileExists(ExistingDirectory + '\FlightRadar24.exe') then
    Result := ExistingDirectory
  else
    Result := ExpandConstant('{autopf}\FlightRadar24');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Parameters: String;
  SummaryFile: String;
  SummaryBytes: AnsiString;
begin
  if CurStep <> ssPostInstall then
    Exit;
  WizardForm.StatusLabel.Caption := 'Checking the USB receiver driver...';
  SummaryFile := ExpandConstant('{tmp}\frw-driver-summary.txt');
  { The execution-policy override applies only to this packaged setup process;
    it does not change machine policy or Windows driver-signature enforcement. }
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{app}\setup\Install-UsbDriver.ps1') + '" -Install -DriverRoot "' +
    ExpandConstant('{app}\runtime\drivers\winusb') + '" -ReportPath "' +
    ExpandConstant('{tmp}\frw-driver-report.json') + '" -SummaryPath "' + SummaryFile + '"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
              Parameters, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    DriverFailed := True;
    DriverSummary := 'The application was installed, but Windows could not start USB driver setup.';
  end
  else
  begin
    DriverRestart := ResultCode = 3010;
    DriverFailed := (ResultCode <> 0) and not DriverRestart;
    if LoadStringFromFile(SummaryFile, SummaryBytes) then
      DriverSummary := UTF8Decode(SummaryBytes)
    else if DriverFailed then
      DriverSummary := 'The application was installed. USB driver setup needs attention; reconnect the receiver and run setup again.'
    else
      DriverSummary := 'USB receiver driver checks completed.';
    Log('USB driver setup exit code: ' + IntToStr(ResultCode));
  end;
  Log('USB driver setup summary: ' + DriverSummary);
  if DriverFailed and not WizardSilent then
    MsgBox(DriverSummary, mbInformation, MB_OK);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and (DriverSummary <> '') then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + DriverSummary;
end;

function NeedRestart(): Boolean;
begin
  Result := DriverRestart;
end;

function GetCustomSetupExitCode(): Integer;
begin
  { Automated deployments must not mistake a failed USB setup for success. }
  if DriverFailed then
    Result := 20
  else if DriverRestart then
    Result := 3010
  else
    Result := 0;
end;
