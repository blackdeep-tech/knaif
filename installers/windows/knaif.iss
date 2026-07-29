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
  #define AppVersion "1.1.0"
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
; Minimum NVIDIA driver major version for the opt-in CUDA backend (the CUDA 13 floor). The
; authoritative declaration is `requires.min_driver` in contracts/backends/backend-manifest.yaml;
; this is a duplicate because ISPP cannot read YAML, and python/core/tests/test_installer_iss.py
; asserts the two agree. Offering the payload below this floor would hand the user ~618 MB that
; then fails to load — which reaches them as "CUDA didn't work", the least debuggable outcome.
#ifndef MinNvidiaDriver
  #define MinNvidiaDriver "580"
#endif
; The loadable backend file the payload installs. Its presence in the backends dir is how the
; installer knows not to re-offer the download.
#ifndef CudaBackendFile
  #define CudaBackendFile "ggml-cuda.dll"
#endif
; AppId is how Windows identifies the application: Inno derives the uninstall registry key
; ({AppId}_is1) from it, and two builds that share an AppId ARE the same application. Overridable
; so a scratch verification install cannot collide with a real one — tearing a test install down by
; deleting the production key is precisely how F1 (no Add/Remove row, no upgrade detection) is
; created. Always pass a throwaway GUID when compiling to a scratch dir:
;   ISCC /DAppIdGuid=00000000-0000-0000-0000-00000000TEST /O<scratch> installers\windows\knaif.iss
#ifndef AppIdGuid
  #define AppIdGuid "7E9F3C2A-4B6D-4E1F-9A2B-1C3D5E7F9A0B"
#endif
; The default install location, defined once: [Setup] sets DefaultDirName from it, and the
; stale-install rescue probes it. Those two must never drift — the rescue can only look where the
; install would have gone by default.
#define DefaultDir "{localappdata}\Programs\knaif"
; Mark an overridden build in Add/Remove Programs so a test install is never mistaken for the real
; one — the two now coexist there rather than overwriting each other.
#if AppIdGuid == "7E9F3C2A-4B6D-4E1F-9A2B-1C3D5E7F9A0B"
  #define TestSuffix ""
#else
  #define TestSuffix " (TEST BUILD)"
#endif

[Setup]
; `{{` escapes a literal brace; ISPP expands {#AppIdGuid} first, leaving Inno `{{<guid>}`.
AppId={{{#AppIdGuid}}
AppName=knaif
AppVersion={#AppVersion}
; Identity shown in Add/Remove Programs and setup.exe's Properties tab. AppPublisher must name the
; maintaining entity, not the product — it is what Windows displays beside the verified-publisher
; string once W4 signs, and a user comparing the two has no way to tell a benign mismatch from a
; malicious one. When a certificate is issued, reconcile AppPublisher with the CERT SUBJECT and
; leave LICENSE/NOTICE alone: those are ownership statements with no matching requirement.
; Tracked in docs/plans/2026-07-27-code-signing.md (S3).
AppPublisher=Blackdeep Technologies Ltd.
AppPublisherURL=https://blackdeep.tech
AppSupportURL=https://github.com/blackdeep-tech/knaif/issues
AppUpdatesURL=https://github.com/blackdeep-tech/knaif/releases
AppContact=knaif@blackdeep.tech
AppReadmeFile={app}\README.txt
; setup.exe's Properties -> Details tab is blank without these, and that tab is exactly what a
; cautious user checks after the SmartScreen prompt (F5).
VersionInfoVersion={#AppVersion}
VersionInfoCompany=Blackdeep Technologies Ltd.
VersionInfoProductName=knaif
VersionInfoDescription=knaif installer
VersionInfoCopyright=Copyright 2026 Blackdeep Technologies Ltd.
DefaultDirName={#DefaultDir}
DisableProgramGroupPage=yes
; Refuse to install over a running CLI. Without this an upgrade hits a locked bin\knaif.exe and
; silently defers the replacement to the next reboot, so the user keeps running the old build with
; no indication why. knaif.exe holds this exact name (see hold_app_mutex in apps/cli/src/main.rs);
; the two strings must stay in step. SetupMutex is the same class of problem between two setups —
; scoped by AppId so a TEST BUILD cannot block the real installer. A per-user install needs no
; Global\ namespace for either.
AppMutex=knaif-cli-running
SetupMutex=knaif-setup-{#AppIdGuid}
; Give Add/Remove Programs the product icon rather than a generic one. apps/cli/build.rs embeds the
; same media/knaif.ico into knaif.exe, so the row, the exe and setup.exe all show one mark.
UninstallDisplayIcon={app}\bin\knaif.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes
OutputDir=..\..\dist
OutputBaseFilename=knaif-{#AppVersion}-windows-{#Arch}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=knaif {#AppVersion}{#TestSuffix}
LicenseFile={#Stage}\LICENSE
; A last page that says what to do next (F10). ChangesEnvironment only reaches processes started
; after the broadcast, so the PATH task genuinely does not work in a terminal the user already had
; open — and with [Icons] deliberately empty, the wizard would otherwise finish offering nothing.
; Compile-time content from the repo, not staged into {app}. NB the command is `skills deps`;
; there is no `knaif doctor` subcommand.
InfoAfterFile=postinstall.txt
; setup.exe's own icon. The same .ico is embedded into knaif.exe by apps/cli/build.rs, so the
; installer, the executable and the Add/Remove Programs row (UninstallDisplayIcon) all match.
SetupIconFile=..\..\media\knaif.ico

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
;
; Task names are FLAT and must stay that way. A dotted `deps\<x>` name declares `deps` as a parent
; task; when the parent is not defined in [Tasks] the children render under the preceding task and
; silently lose both their GroupDescription and their `unchecked` flag — which is how AGPL
; Ghostscript and a ~350 MB LibreOffice shipped pre-checked against this script's stated intent.
; Declaring a real `deps` parent does NOT fix it: Inno force-checks children of a checked parent,
; so the defaults would break again the moment anyone ticks it. Flat names are the only shape in
; which per-task defaults survive.
Name: "depsffmpeg";    Description: "FFmpeg — required for the ffmpeg skill";                      GroupDescription: "Install supporting tools (via winget):"; Components: skills\ffmpeg
Name: "depsgs";        Description: "Ghostscript — aggressive PDF compression (optional, AGPL)";    GroupDescription: "Install supporting tools (via winget):"; Components: skills\documents; Flags: unchecked
Name: "depssoffice";   Description: "LibreOffice — Office <-> PDF conversion (optional)";           GroupDescription: "Install supporting tools (via winget):"; Components: skills\documents; Flags: unchecked
Name: "depstesseract"; Description: "Tesseract OCR — scanned-PDF / image text (optional)";          GroupDescription: "Install supporting tools (via winget):"; Components: skills\documents; Flags: unchecked
; The AI model powers `run` (turning a request into a command). ~2.5 GB, one-time — default on so
; knaif works out of the box. `Check: NeedsModel` hides the task (and with it the whole "AI model:"
; group) when the GGUF is already in the store, instead of offering a download that then no-ops.
; The same Check stays on the [Run] entry: it guards the gap between the wizard page and the install
; step. The description states that setup waits, because the [Run] entry has no `nowait` and Inno
; disables Cancel during that stage — the wait is disclosed where the user opts in, not when it starts.
Name: "getmodel"; Description: "Download the knaif AI model now — {#DefaultModel}, a Qwen3-4B fine-tune (~2.5 GB, needed for ""run""; setup will wait for this)"; GroupDescription: "AI model:"; Check: NeedsModel
; The opt-in NVIDIA CUDA backend. Re-enabled for 1.1.0: v1 shipped no such component precisely
; because `knaif backend install` did not exist for it to call, and an opt-in task with no command
; behind it is worse than no offer.
;
; UNCHECKED, and hidden entirely unless CudaOfferable. Three conditions, all of which must hold:
; an NVIDIA GPU is present, its driver meets the CUDA 13 floor, and the backend is not already
; installed. Below the floor the payload would download and then fail to load; on an AMD box the
; offer is pure noise. `knaif backend install cuda` remains available afterwards either way, which
; is what makes it safe for this to be conservative.
Name: "cudabackend"; Description: "Download the NVIDIA CUDA backend now (~618 MB — faster inference on your NVIDIA GPU; you can also run ""knaif backend install cuda"" later)"; GroupDescription: "GPU acceleration:"; Flags: unchecked; Check: CudaOfferable

[Files]
; Core: binary + language-neutral contracts + docs.
Source: "{#Stage}\bin\*";     DestDir: "{app}\bin";    Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\contracts\*";  DestDir: "{app}\contracts"; Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\LICENSE";   DestDir: "{app}";        Components: core; Flags: ignoreversion
; NOTICE is an Apache-2.0 §4(d) obligation, not documentation — it must land in the installed tree
; alongside LICENSE. installers/smoke.sh asserts both are staged.
Source: "{#Stage}\NOTICE";    DestDir: "{app}";        Components: core; Flags: ignoreversion
Source: "{#Stage}\README.txt";DestDir: "{app}";        Components: core; Flags: ignoreversion
Source: "{#Stage}\licenses\*";DestDir: "{app}\licenses";Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
; Skills (runtime data only) — one component each.
Source: "{#Stage}\skills\ffmpeg\*";    DestDir: "{app}\skills\ffmpeg";    Components: skills\ffmpeg;    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Stage}\skills\documents\*"; DestDir: "{app}\skills\documents"; Components: skills\documents; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Wipe the staged payload before [Files] runs. Without this, deselecting a skill on a reinstall
; leaves the old one on disk and still listed by `knaif skills list`, and a file dropped between
; releases survives forever. Wipe-and-recopy costs only disk churn and is the one shape where the
; installed tree always matches what was actually selected.
;
; SAFE ONLY BECAUSE THE USER DATA DIR IS OUTSIDE {app} — see KnaifDataDir, which puts the GGUF
; store and the backends payload under ~/.knaif. All four directories below are pure staged
; payload. If anything ever starts writing user state under {app}, this section becomes
; destructive and must be narrowed first.
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\skills"
Type: filesandordirs; Name: "{app}\contracts"
Type: filesandordirs; Name: "{app}\licenses"

[Registry]
; PATH is only touched with explicit consent (the addtopath task); appended, never overwritten.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}\bin"; \
    Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}\bin'))

[Run]
; Install the selected supporting tools via winget — but only when winget exists AND the runtime
; would not already consider the tool satisfied. Third-party tools are never bundled; winget fetches
; each from its own vendor. Non-fatal: knaif is already installed regardless of the outcome.
;
; The command lists below MIRROR `dependencies.external_tools` in skills/<skill>/skill.yaml, which is
; the source of truth. `ShouldInstallAll` is `all_required: true` (distinct binaries, every one
; needed); `ShouldInstallAny` is the default (alternative names for one binary, any one satisfies).
; These lists are duplicated here because ISPP cannot read YAML; python/core/tests/test_installer_iss.py
; asserts the two agree — commands, all_required-vs-alias semantics, and the task's default checked
; state against the tool's `required` flag. Change a list here and that test tells you which contract
; it no longer matches.
Filename: "winget"; Parameters: "install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements"; \
    StatusMsg: "Installing FFmpeg via winget (this can take a minute)..."; Flags: shellexec waituntilterminated; \
    Tasks: depsffmpeg; Check: ShouldInstallAll('ffmpeg,ffprobe')
Filename: "winget"; Parameters: "install -e --id ArtifexSoftware.GhostScript --accept-package-agreements --accept-source-agreements"; \
    StatusMsg: "Installing Ghostscript via winget..."; Flags: shellexec waituntilterminated; \
    Tasks: depsgs; Check: ShouldInstallAny('gs,gswin64c,gswin32c')
Filename: "winget"; Parameters: "install -e --id TheDocumentFoundation.LibreOffice --accept-package-agreements --accept-source-agreements"; \
    StatusMsg: "Installing LibreOffice via winget (large download, please wait)..."; Flags: shellexec waituntilterminated; \
    Tasks: depssoffice; Check: ShouldInstallAny('soffice,libreoffice')
Filename: "winget"; Parameters: "install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements"; \
    StatusMsg: "Installing Tesseract OCR via winget..."; Flags: shellexec waituntilterminated; \
    Tasks: depstesseract; Check: ShouldInstallAny('tesseract')
; Download the recommended model via the just-installed knaif (its own progress bar shows in a
; console). Skipped when the GGUF is already in the shared store. Non-fatal if the download fails.
Filename: "{app}\bin\knaif.exe"; Parameters: "models pull {#DefaultModel}"; \
    StatusMsg: "Downloading the {#DefaultModel} AI model (~2.5 GB, one time)..."; Flags: waituntilterminated; \
    Tasks: "getmodel"; Check: NeedsModel
; The CUDA backend, through the SAME command a user would run by hand — so there is one install
; path with one set of checksums and one atomic swap, not an installer-specific copy of it.
;
; NON-FATAL BY CONSTRUCTION, and that is the point. A [Run] entry's exit code does not fail the
; installation, so a timed-out or refused 618 MB download leaves a fully working knaif behind
; rather than rolling one back over an optional GPU extra. The user is told they can re-run it; the
; command is identical, so nothing about the retry is second-class.
Filename: "{app}\bin\knaif.exe"; Parameters: "backend install cuda"; \
    StatusMsg: "Downloading the NVIDIA CUDA backend (~618 MB, one time)..."; Flags: waituntilterminated; \
    Tasks: "cudabackend"

[Code]
const
  EnvKey = 'Environment';
  UninstallRoot = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\';

// The uninstall key Inno derives from AppId. Built from the same AppIdGuid define as [Setup], so an
// overridden (scratch) build probes ITS OWN key and never inspects or acts on the production
// install. In '{{#AppIdGuid}}_is1' the preprocessor expands the inner {#AppIdGuid}, leaving the
// literal braces that Inno's key name carries — this is a plain Pascal string, not a constant, so
// nothing expands it further at runtime.
function UninstallKeyPath: string;
begin
  Result := UninstallRoot + '{{#AppIdGuid}}_is1';
end;

function InstallIsRegistered: Boolean;
begin
  Result := RegKeyExists(HKCU, UninstallKeyPath) or RegKeyExists(HKLM, UninstallKeyPath);
end;

// Rescue an install whose uninstall key is gone while its tree is still on disk (F1). In that state
// Add/Remove Programs shows no row and an upgrade degrades to the "folder already exists" warning,
// with no way out from the UI. Inno writes the key at install and removes it at the END of an
// uninstall, so a cancelled uninstall leaves it behind — reaching this state normally means the key
// was deleted by hand or by a registry cleaner. This is recovery, not prevention.
//
// LIMITATION: only the DEFAULT directory is discoverable. Once the key is gone nothing records a
// custom /DIR=, so a non-default install in this state must be removed by hand.
//
// The orphaned uninstaller is the one already on disk, built from an OLDER script — it still
// carries whatever data-deletion default it shipped with, which for <=1.0.1 is DELETE. So it runs
// INTERACTIVELY (never /SILENT) and the prompt says to answer No, rather than us silently invoking
// a binary that can erase a 2.5 GB model store. Defaults to No so an unattended run skips it.
function InitializeSetup: Boolean;
var
  Orphan: string;
  Code: Integer;
begin
  Result := True;
  if InstallIsRegistered then
    Exit;
  Orphan := ExpandConstant('{#DefaultDir}\unins000.exe');
  if not FileExists(Orphan) then
    Exit;
  if SuppressibleMsgBox(
       'A previous knaif installation was found at' + #13#10#13#10 +
       ExpandConstant('{#DefaultDir}') + #13#10#13#10 +
       'but it is not registered in Add/Remove Programs, so setup cannot upgrade it in place.'
       + #13#10#13#10 +
       'Run the old uninstaller now to clear it?' + #13#10#13#10 +
       'It will ask whether to delete your downloaded AI models — answer No to keep them.',
       mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    Exec(Orphan, '', '', SW_SHOW, ewWaitUntilTerminated, Code);
end;

{ Dependency detection below mirrors the runtime probe in
  native/crates/knaif-core/src/deps.rs (resolve_command / which / executable_extensions). Any
  divergence means the installer offers a winget install the runtime considers unnecessary, or
  skips one it needs. The three behaviours that must match: PATHEXT suffixes (not just `.exe`),
  the $KNAIF_<CMD>_BIN override, and `all_required` vs alias satisfaction. }

{ Is Cmd in Dir under any PATHEXT suffix? The bare name is tried first, as the runtime does. }
function FoundInDir(Dir, Cmd: string): Boolean;
var
  Exts, Ext: string;
  P: Integer;
begin
  Result := False;
  Exts := GetEnv('PATHEXT');
  if Exts = '' then
    Exts := '.COM;.EXE;.BAT;.CMD';
  { Leading ';' yields an empty first token = the bare, extensionless name. }
  Exts := ';' + Exts + ';';
  while Pos(';', Exts) > 0 do
  begin
    P := Pos(';', Exts);
    Ext := Copy(Exts, 1, P - 1);
    Delete(Exts, 1, P);
    if FileExists(AddBackslash(Dir) + Cmd + Ext) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ PATH scan only — no env override. Used for winget itself, which is not a knaif dependency. }
function OnPath(Cmd: string): Boolean;
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
    if (Dir <> '') and FoundInDir(Dir, Cmd) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ One command, resolved the runtime's way: $KNAIF_<CMD>_BIN wins outright — resolve_command()
  returns it without probing, so a user who set it is considered satisfied — else scan PATH. }
function CommandPresent(Cmd: string): Boolean;
begin
  if GetEnv('KNAIF_' + Uppercase(Cmd) + '_BIN') <> '' then
    Result := True
  else
    Result := OnPath(Cmd);
end;

{ `all_required: false` (the default): the commands are alternative names for one binary, so any
  one satisfies — e.g. gs / gswin64c / gswin32c. }
function AnyPresent(List: string): Boolean;
var
  Cmd: string;
  P: Integer;
begin
  Result := False;
  List := List + ',';
  while Pos(',', List) > 0 do
  begin
    P := Pos(',', List);
    Cmd := Copy(List, 1, P - 1);
    Delete(List, 1, P);
    if (Cmd <> '') and CommandPresent(Cmd) then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

{ `all_required: true`: the commands are distinct binaries and EVERY one must resolve — e.g. the
  ffmpeg skill needs ffmpeg AND ffprobe, so ffmpeg alone does not satisfy it. }
function AllPresent(List: string): Boolean;
var
  Cmd: string;
  P: Integer;
begin
  Result := True;
  List := List + ',';
  while Pos(',', List) > 0 do
  begin
    P := Pos(',', List);
    Cmd := Copy(List, 1, P - 1);
    Delete(List, 1, P);
    if (Cmd <> '') and not CommandPresent(Cmd) then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

{ Install only when winget is available AND the dependency is not already satisfied. }
function ShouldInstallAny(List: string): Boolean;
begin
  Result := OnPath('winget') and not AnyPresent(List);
end;

function ShouldInstallAll(List: string): Boolean;
begin
  Result := OnPath('winget') and not AllPresent(List);
end;

// True when the recommended model GGUF isn't already in the shared store (~/.knaif/models).
// Inno env-var constant is {%NAME} (NOT the cmd-style {%NAME%}, which silently mis-resolves).
function NeedsModel: Boolean;
begin
  Result := not FileExists(ExpandConstant('{%USERPROFILE}\.knaif\models\{#DefaultModelFile}'));
end;

// --- The opt-in CUDA backend offer ------------------------------------------------------------
//
// Mirrors the runtime gate in knaif-models' `cuda_offer`, but has to answer the same question
// BEFORE knaif is installed, so it cannot call the CLI and probes nvidia-smi directly. Two
// deliberate simplifications against the runtime version: it does not read compute capability (the
// wizard has one offer, not two strengths), and it detects an existing payload by file presence
// rather than by receipt (the CLI does the authoritative check and will simply re-install).
//
// Probed ONCE and cached: `Check:` is evaluated repeatedly — per wizard page and again at install
// time — and spawning nvidia-smi each time would make the wizard visibly stutter.
var
  NvidiaProbed: Boolean;
  NvidiaDriverMajorCached: Integer;

// Major version of the installed NVIDIA driver, or 0 when there is no NVIDIA GPU, no nvidia-smi,
// or the output cannot be parsed. Zero always means "do not offer" — on a box we cannot read, the
// safe answer is silence, since `knaif backend install cuda` still works by hand.
function NvidiaDriverMajor: Integer;
var
  TmpFile, Line: string;
  Lines: TArrayOfString;
  ResultCode, DotPos: Integer;
begin
  if NvidiaProbed then
  begin
    Result := NvidiaDriverMajorCached;
    Exit;
  end;
  NvidiaProbed := True;
  NvidiaDriverMajorCached := 0;
  Result := 0;

  TmpFile := ExpandConstant('{tmp}\knaif-nvsmi.txt');
  { Routed through cmd /C because Exec cannot redirect on its own. 2>nul keeps a missing
    nvidia-smi from flashing a console error at a user who simply has no NVIDIA card. }
  if not Exec(ExpandConstant('{cmd}'),
              '/C nvidia-smi --query-gpu=driver_version --format=csv,noheader > "' + TmpFile + '" 2>nul',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Exit;
  if ResultCode <> 0 then
    Exit;
  if not LoadStringsFromFile(TmpFile, Lines) then
    Exit;
  if GetArrayLength(Lines) = 0 then
    Exit;

  Line := Trim(Lines[0]);
  DotPos := Pos('.', Line);
  if DotPos > 1 then
    Line := Copy(Line, 1, DotPos - 1);
  NvidiaDriverMajorCached := StrToIntDef(Line, 0);
  Result := NvidiaDriverMajorCached;
end;

// True when the payload is already sitting in the backends dir. That directory lives outside {app}
// (see KnaifDataDir) precisely so it survives upgrades, which is exactly why an upgrade must not
// re-offer a 618 MB download the user already has.
function CudaBackendInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{%USERPROFILE}\.knaif\backends\{#CudaBackendFile}'));
end;

// Offer only when all three hold: NVIDIA present, driver at or above the CUDA 13 floor, payload
// absent. Below the floor the download would succeed and the library would then fail to load.
function CudaOfferable: Boolean;
begin
  Result := (NvidiaDriverMajor >= {#MinNvidiaDriver}) and not CudaBackendInstalled;
end;

{ Pre-select "I accept the agreement" on the license page. Inno has no directive for this — the
  radio defaults to "I do not accept" and can only be flipped from code, each time the page is
  shown (so it also re-applies after Back). The license is Apache-2.0, a permissive grant that
  requires no click-through assent; the page is informational, and defaulting to refusal just adds
  a step every user has to undo. }
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpLicense then
    WizardForm.LicenseAcceptedRadio.Checked := True;
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

  { Offer to remove the data dir after the files are gone. KEEPING IS THE DEFAULT (changed
    2026-07-26, W2). The default is also what SuppressibleMsgBox answers under /SILENT and
    /VERYSILENT, so the old IDYES meant any unattended uninstall silently destroyed a 2.5 GB model
    store — including a deployment tool doing uninstall-then-reinstall, and any scripted teardown
    that forgot the flag. Deleting is recoverable only by re-downloading 2.5 GB; keeping costs
    disk space the user can reclaim whenever they like, so the asymmetry decides it.
    A user who genuinely wants the data gone is asked here and can still say Yes.
    TWO SEPARATE DEFAULTS, both needed: MB_DEFBUTTON2 focuses "No" in the dialog, so Enter keeps
    the data; SuppressibleMsgBox's last argument is only the answer used when message boxes are
    SUPPRESSED. Setting one without the other leaves the other path on the destructive answer. }
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := KnaifDataDir;
    if DirExists(DataDir) then
      if SuppressibleMsgBox(
           'Also delete downloaded AI models and knaif data?' + #13#10#13#10 +
           DataDir + #13#10#13#10 +
           'This includes the AI model (~2.5 GB) and any optional GPU backends you added.' + #13#10 +
           'Choose No to keep them — that avoids re-downloading the model if you reinstall.',
           mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
