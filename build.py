"""Build the portable application using project-local build dependencies."""
from pathlib import Path
import argparse,sys,shutil,ssl,struct,os
ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--dist-dir',type=Path,default=ROOT/'work/package',help='Portable application output directory')
parser.add_argument('--work-dir',type=Path,default=ROOT/'work/pyinstaller',help='PyInstaller temporary build directory')
parser.add_argument('--runtime-dir',type=Path,default=ROOT/'work/runtime',help='Runtime built for the target operating system')
args=parser.parse_args()
if struct.calcsize('P') != 8: raise SystemExit('FlightRadar24 requires a 64-bit Python build.')
if os.name == 'nt':
    console_helper = args.runtime_dir / 'bin/cygwin-console-helper.exe'
    if not console_helper.is_file():
        raise SystemExit('Missing runtime/bin/cygwin-console-helper.exe. Prepare the complete Windows runtime before packaging.')
    # Dependency discovery must not pick unrelated toolkit DLLs from the host's
    # PATH. Qt uses Windows' ICU API; Poppler's identically named, versioned ICU
    # exports are incompatible. This changes only the build process environment.
    windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    os.environ['PATH'] = os.pathsep.join(map(str, (Path(sys.executable).parent,
                                                 windows/'System32', windows)))
dist=args.dist_dir.resolve(); work=args.work_dir.resolve()
work.mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(ROOT/'work/pythonlibs'),str(ROOT/'work/desktoplibs'),str(ROOT/'work/applibs')]
from PyInstaller.__main__ import run
extra_data=[]
# Some bundled Python distributions load this stdlib trust resource in SSLContext.
# Keep their certificate policy intact in the frozen application.
trust_resource=Path(ssl.__file__).with_name('codex-rejected-cas.pem')
if trust_resource.is_file(): extra_data=['--add-data',str(trust_resource)+';.']
run([str(ROOT/'src/launcher.py'),'--name','FlightRadar24','--onedir','--windowed','--noconfirm','--clean',
     '--distpath',str(dist),'--workpath',str(work),
     '--specpath',str(work),'--paths',str(ROOT/'src'),'--paths',str(ROOT/'work/applibs'),
     '--paths',str(ROOT/'work/desktoplibs'),
     '--add-data',str(ROOT/'src/frw/web')+';frw/web',
     '--add-data',str(ROOT/'src/frw/data')+';frw/data',
     '--collect-all','pyModeS','--hidden-import','serial',
     '--exclude-module','PySide6.QtWebEngineCore','--exclude-module','PySide6.QtWebEngineWidgets',
     '--exclude-module','PySide6.QtWebEngineQuick','--exclude-module','PySide6.QtQml',
     '--exclude-module','PySide6.QtQuick','--noupx']+extra_data)
dest=dist/'FlightRadar24'
shutil.copytree(args.runtime_dir,dest/'runtime',dirs_exist_ok=True)
if not (dest/'runtime/licenses/qt').is_dir():
    raise RuntimeError('Missing Qt/PySide redistribution notices in runtime/licenses/qt.')
for name in ('README.md','LICENSE','COPYRIGHT','THIRD-PARTY-NOTICES.md','BUILDING.md','CONTRIBUTING.md','CHANGELOG.md'):
    if (ROOT/name).exists():shutil.copyfile(ROOT/name,dest/name)
if (ROOT/'docs').is_dir():
    shutil.copytree(ROOT/'docs',dest/'docs',dirs_exist_ok=True)
print('Built:',dest)
