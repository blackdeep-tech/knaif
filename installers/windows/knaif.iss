; Inno Setup script for the knaif native CLI (Windows).
;
; Per-user install (no admin) of a self-contained artifact staged by installers/package.sh.
; Component tree mirrors the product model: core is mandatory; each skill is an optional
; component. The installed layout ({app}\bin\knaif.exe + {app}\skills + {app}\contracts) is exactly
; what the binary's exe-relative resource resolution expects, so it runs from anywhere.
;
; Third-party tools (ffmpeg, LibreOffice, Ghostscript, Tesseract) are NEVER bundled — the doctor
; (`knaif skills deps`) tells the user what to install via winget/vendor installers post-install.
;
; Build (from repo root, after staging a CPU artifact with `just package-native cpu`):
;   & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installers\windows\knaif.iss
; Output: dist\knaif-<ver>-windows-x64-setup.exe
;
; Kind/arch/version can be overridden: ISCC /DKind=vulkan /DAppVersion=0.1.0 ...

#ifndef AppVersion
  #define AppVersion "1.0.1"
#endif
#ifndef Arch
  #define Arch "x64"
#endif
; Which staged artifact to wrap. `vulkan` is the DEFAULT release artifact and gets the plain staged
; name; every other kind carries a suffix (see the SUFFIX rule in package.sh, which this mirrors).
;
; The default is CPU+Vulkan — under Option 3 the Vulkan backend is one extra loadable lib beside the
; same CPU backends, and it loses device selection on a box with no usable GPU. So it is a strict
; superset of `cpu` and runs everywhere `cpu` does; `cpu` is a build kind, not a release artifact.
; Only ONE installer is published, so OutputBaseFilename carries no kind suffix — override /DKind=
; for a local experiment only, and mind that it overwrites the same output name.
#ifndef Kind
  #define Kind "vulkan"
#endif
#if Kind == "vulkan"
  #define Stage "..\..\dist\staging\knaif-" + AppVersion + "-windows-" + Arch
#else
  #define Stage "..\..\dist\staging\knaif-" + AppVersion + "-windows-" + Arch + "-" + Kind
#endif
; Default model offered at install (name = manifest recommendation; File = its store filename, used
; to skip the download when already present). Keep in sync with contracts/models/model-manifest.yaml.
#ifndef DefaultModel
  #define DefaultModel "knaif-qwen3-4b-v1"
#endif
#ifndef DefaultModelFile
  #define DefaultModelFile "knaif-qwen3-4b-v1-q4_k_m.gguf"
#endif

[Setup]
AppId={{7E9F3C2A-4B6D-4E1F-9A2B-1C3D5E7F9A0B}
AppName=knaif
AppVersion={#AppVersion}
AppPublisher=knaif
AppPublisherURL=https://github.com/
DefaultDirName={localappdata}\Programs\knaif
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes
OutputDir=..\..\dist
OutputBaseFilename=knaif-{#AppVersion}-windows-{#Arch}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=knaif {#AppVersion}
LicenseFile={#Stage}\LICENSE

[Types]
Name: "full"; Description: "Full installation (all skills)"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core"; Description: "knaif core runtime (required)"; Types: full custom; Flags: fixed
Name: "skills"; Description: "Skills"; Types: full custom
Name: "skills\ffmpeg"; Description: "ffmpeg — convert / compress / resize video & audio"; Types: full custom
Name: "skills\documents"; Description: "documents — PDF & Office toolkit"; Types: full custom

[Tasks]
Name: "addtopath"; Description: "Add knaif to my PATH (so ""knaif"" works in any terminal)"; GroupDescription: "Integration:"
; Supporting external tools, installed for you via winget (skipped if already present, or if winget
; is unavailable). Shown per selected skill. ffmpeg is required by the ffmpeg skill, so it defaults on.
Name: "deps\ffmpeg";    Description: "FFmpeg — required for the ffmpeg skill";                     GroupDescription: "Install supporting tools (via winget):"; Components: skills\ffmpeg
Name: "deps\gs";        Description: "Ghostscript — aggressive PDF compression (optional, AGPL)";   GroupDescription: "Install supporting tools (via winget):"; Components: skills\documents; Flags: unchecked
Name: "deps\soffice";   Description: "LibreOffice — Office <-> PDF conversion (optional)";           GroupDescription: "Install supporting tools (via winget):"; Components: skills\documents; Flags: unchecked
Name: "deps\tesseract"; Description: "Tesseract OCR — scanned-PDF / image text (optional)";          GroupDescription: "Install supporting tools (via winget):"; Components: skills\documents; Flags: unchecked
; The AI model powers `run` (turning a request into a command). ~2.5 GB, one-time — default on so
; knaif works out of the box; skipped automatically if the model is already downloaded.
Name: "getmodel"; Description: "Download the knaif AI model now — {#DefaultModel}, a Qwen3-4B fine-tune (~2.5 GB, needed for ""run"")"; GroupDescription: "AI model:"

[Files]
; Core: binary + language-neutral contracts + docs.
Source: "{#Stage}\bin\*";     DestDir: "{app}\bin";    Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\contracts\*";  DestDir: "{app}\contracts"; Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\LICENSE";   DestDir: "{app}";        Components: core; Flags: ignoreversion
Source: "{#Stage}\README.txt";DestDir: "{app}";        Components: core; Flags: ignoreversion
Source: "{#Stage}\licenses\*";DestDir: "{app}\licenses";Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
; Skills (runtime data only) — one component each.
Source: "{#Stage}\skills\ffmpeg\*";    DestDir: "{app}\skills\ffmpeg";    Components: skills\ffmpeg;    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\skills\documents\*"; DestDir: "{app}\skills\documents"; Components: skills\documents; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; PATH is only touched with explicit consent (the addtopath task); appended, never overwritten.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}\bin"; \
    Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}\bin'))

[Run]
; Install the selected supporting tools via winget — but only when winget exists AND the tool
; isn't already on PATH (ShouldInstall). Third-party tools are never bundled; winget fetches each
; from its own vendor. Non-fatal: knaif is already installed regardless of the outcome.
Filename: "winget"; Parameters: "install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements"; \
    StatusMsg: "Installing FFmpeg via winget (this can take a minute)..."; Flags: shellexec waituntilterminated; \
    Tasks: "deps\ffmpeg"; Check: ShouldInstall('ffmpeg')
Filename: "winget"; Parameters: "install -e --id ArtifexSoftware.GhostScript --accept-package-agreements --accept-source-agreements"; \
    StatusMsg: "Installing Ghostscript via winget..."; Flags: shellexec waituntilterminated; \
    Tasks: "deps\gs"; Check: ShouldInstall('gswin64c')
Filename: "winget"; Parameters: "install -e --id TheDocumentFoundation.LibreOffice --accept-package-agreements --accept-source-agreements"; \
    StatusMsg: "Installing LibreOffice via winget (large download, please wait)..."; Flags: shellexec waituntilterminated; \
    Tasks: "deps\soffice"; Check: ShouldInstall('soffice')
Filename: "winget"; Parameters: "install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements"; \
    StatusMsg: "Installing Tesseract OCR via winget..."; Flags: shellexec waituntilterminated; \
    Tasks: "deps\tesseract"; Check: ShouldInstall('tesseract')
; Download the recommended model via the just-installed knaif (its own progress bar shows in a
; console). Skipped when the GGUF is already in the shared store. Non-fatal if the download fails.
Filename: "{app}\bin\knaif.exe"; Parameters: "models pull {#DefaultModel}"; \
    StatusMsg: "Downloading the {#DefaultModel} AI model (~2.5 GB, one time)..."; Flags: waituntilterminated; \
    Tasks: "getmodel"; Check: NeedsModel

[Code]
const
  EnvKey = 'Environment';

{ True if <Exe>.exe is found in any PATH directory (mirrors the runtime `knaif skills deps` probe). }
function CmdOnPath(Exe: string): Boolean;
var
  Paths, Dir: string;
  P: Integer;
begin
  Result := False;
  Paths := GetEnv('PATH') + ';';
  while Pos(';', Paths) > 0 do
  begin
    P := Pos(';', Paths);
    Dir := Copy(Paths, 1, P - 1);
    Delete(Paths, 1, P);
    if (Dir <> '') and FileExists(AddBackslash(Dir) + Exe + '.exe') then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ Install <Exe>'s package only when winget is available AND the tool isn't already present. }
function ShouldInstall(Exe: string): Boolean;
begin
  Result := CmdOnPath('winget') and not CmdOnPath(Exe);
end;

// True when the recommended model GGUF isn't already in the shared store (~/.knaif/models).
// Inno env-var constant is {%NAME} (NOT the cmd-style {%NAME%}, which silently mis-resolves).
function NeedsModel: Boolean;
begin
  Result := not FileExists(ExpandConstant('{%USERPROFILE}\.knaif\models\{#DefaultModelFile}'));
end;

function NeedsAddPath(PathDir: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', OrigPath) then
  begin
    Result := True;
    Exit;
  end;
  { True only if PathDir is not already present (case-insensitive, delimiter-padded). }
  Result := Pos(';' + Uppercase(PathDir) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

procedure RemovePath(PathDir: string);
var
  Paths, Padded: string;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    Exit;
  Padded := ';' + Paths + ';';
  { Drop the exact ';PathDir;' we appended on install -> ';'. No-op if absent. }
  StringChangeEx(Padded, ';' + PathDir + ';', ';', True);
  if Length(Padded) >= 2 then
    Paths := Copy(Padded, 2, Length(Padded) - 2)
  else
    Paths := '';
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths);
end;

// The user data dir. Deliberately OUTSIDE the app dir: it holds the GGUF store (~2.5 GB), the
// opt-in ~/.knaif/backends payload dir, and local state, and it must survive an UPGRADE so a
// reinstall does not re-download the model. The flip side is that plain file removal orphans it,
// so a real uninstall has to ask.
// NOTE: use // here, not a { } comment — a brace constant like {app} inside one CLOSES it early.
function KnaifDataDir: string;
begin
  Result := ExpandConstant('{%USERPROFILE}\.knaif');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usUninstall then
    RemovePath(ExpandConstant('{app}\bin'));

  { Offer to remove the data dir after the files are gone. DELETING IS THE DEFAULT: uninstall means
    gone, and most users never learn ~/.knaif exists to clean it up by hand — leaving multiple GB
    behind is the more surprising outcome. Choosing No is the escape hatch for someone who intends
    to reinstall.
    The same IDYES is what SuppressibleMsgBox answers under /SILENT and /VERYSILENT, so an
    unattended uninstall also removes the data. That is deliberate and consistent. It is safe for
    upgrades because Inno installs over an existing install WITHOUT running the uninstaller; the
    only cost is a re-download for a deployment tool that does uninstall-then-reinstall. }
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := KnaifDataDir;
    if DirExists(DataDir) then
      if SuppressibleMsgBox(
           'Also delete downloaded AI models and knaif data?' + #13#10#13#10 +
           DataDir + #13#10#13#10 +
           'This includes the AI model (~2.5 GB) and any optional GPU backends you added.' + #13#10 +
           'Choose No only if you plan to reinstall — keeping them avoids re-downloading the model.',
           mbConfirmation, MB_YESNO, IDYES) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
