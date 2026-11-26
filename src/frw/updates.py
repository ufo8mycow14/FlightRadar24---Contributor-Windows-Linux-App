"""Public GitHub release discovery and verified Windows update downloads."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

REPOSITORY = 'ufo8mycow14/FlightRadar24---Contributor-Windows-Linux-App'
RELEASES_URL = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
CHECK_INTERVAL = 12 * 60 * 60
MAX_INSTALLER_BYTES = 256 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
DEFAULTS = {'check_automatically': True, 'install_automatically': False,
            'last_check': 0, 'auto_attempted_version': ''}


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})', value):
        raise ValueError('The release version is not a stable three-part version.')
    return tuple(map(int, value.removeprefix('v').split('.')))


def _trusted_url(url):
    p = urllib.parse.urlsplit(url)
    return (p.scheme == 'https' and p.hostname in ('api.github.com', 'github.com', 'release-assets.githubusercontent.com')
            and p.port in (None, 443) and not p.username and not p.password)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl):
            raise ValueError('Update download redirected outside GitHub HTTPS.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url):
    if not _trusted_url(url):
        raise ValueError('Untrusted update address.')
    request = urllib.request.Request(url, headers={'User-Agent':'FlightRadar24-update-check',
                                                   'Accept':'application/vnd.github+json'})
    return urllib.request.build_opener(_Redirects()).open(request, timeout=20)


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    sha256: str
    page: str


def parse_release(data, current_version):
    if not isinstance(data, dict) or data.get('draft') is not False or data.get('prerelease') is not False:
        raise ValueError('GitHub did not return a published stable release.')
    tag = data.get('tag_name')
    newer = version_tuple(tag)
    if newer <= version_tuple(current_version):
        return None
    version = tag.removeprefix('v')
    if tag != 'v' + version:
        raise ValueError('Unexpected release tag.')
    name = f'FlightRadar24-{version}-Windows-x64-Setup.exe'
    matches = [a for a in data.get('assets', []) if isinstance(a, dict) and a.get('name') == name]
    if len(matches) != 1:
        raise ValueError('The release has no unique Windows installer yet. Check again after publication finishes.')
    asset = matches[0]
    expected_url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    page = f'https://github.com/{REPOSITORY}/releases/tag/{tag}'
    if asset.get('browser_download_url') != expected_url or data.get('html_url') != page:
        raise ValueError('The update does not belong to this application repository.')
    size, digest = asset.get('size'), asset.get('digest')
    if type(size) is not int or not 0 < size <= MAX_INSTALLER_BYTES:
        raise ValueError('The installer size is invalid.')
    if not isinstance(digest, str) or not re.fullmatch('sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('GitHub has not supplied a SHA-256 digest for the installer.')
    return Release(version, expected_url, size, digest[7:].lower(), page)


def check_release(current_version):
    with _open(RELEASES_URL) as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError('Release information exceeded the size limit.')
    return parse_release(json.loads(raw), current_version)


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_installer(path, release):
    path = Path(path)
    if path.stat().st_size != release.size or file_sha256(path) != release.sha256:
        raise ValueError('The downloaded installer failed its size or SHA-256 check.')


def download_release(release, directory, progress=None):
    # Revalidate even when a caller supplies a Release rather than API JSON.
    checked = parse_release({'tag_name':'v'+release.version,'draft':False,'prerelease':False,
        'html_url':release.page,'assets':[{'name':Path(urllib.parse.urlsplit(release.url).path).name,
        'browser_download_url':release.url,'size':release.size,'digest':'sha256:'+release.sha256}]}, '0.0.0')
    if checked != release:
        raise ValueError('Invalid release download.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'FlightRadar24-{release.version}-Windows-x64-Setup.exe'
    if target.is_file():
        try:
            verify_installer(target, release)
            return target
        except ValueError:
            pass
    temporary = target.with_suffix('.part')
    try:
        total, digest = 0, hashlib.sha256()
        with _open(release.url) as response, temporary.open('wb') as output:
            while block := response.read(256 * 1024):
                total += len(block)
                if total > release.size:
                    raise ValueError('The download exceeded the published installer size.')
                output.write(block)
                digest.update(block)
                if progress:
                    progress(total, release.size)
        if total != release.size or digest.hexdigest() != release.sha256:
            raise ValueError('The downloaded installer failed its size or SHA-256 check.')
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)


class UpdatePreferences:
    def __init__(self, directory):
        self.path = Path(directory) / 'updates.json'
        self.value = dict(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            for key in ('check_automatically', 'install_automatically'):
                if type(raw.get(key)) is bool:
                    self.value[key] = raw[key]
            stamp = raw.get('last_check')
            if type(stamp) in (float, int) and math.isfinite(stamp) and stamp >= 0:
                self.value['last_check'] = stamp
            if isinstance(raw.get('auto_attempted_version'), str) and len(raw['auto_attempted_version']) <= 24:
                self.value['auto_attempted_version'] = raw['auto_attempted_version']
        except (OSError, ValueError, AttributeError):
            pass
        if not self.value['check_automatically']:
            self.value['install_automatically'] = False

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.path, self.value)

    def due(self, now=None):
        now = time.time() if now is None else now
        stamp = self.value['last_check']
        return self.value['check_automatically'] and (stamp <= 0 or stamp > now or now-stamp >= CHECK_INTERVAL)


def installation_supported():
    return os.name == 'nt' and getattr(sys, 'frozen', False) and Path(sys.executable).name.lower() == 'flightradar24.exe'


def prepare_update(release, installer, profile, resume_receiver):
    if not installation_supported():
        raise ValueError('In-app installation requires the packaged Windows application.')
    verify_installer(installer, release)
    directory = Path(profile).resolve() / 'updates'
    directory.mkdir(parents=True, exist_ok=True)
    helper = directory / 'Apply-Update.ps1'
    shutil.copy2(Path(__file__).parent/'data/Apply-Update.ps1', helper)
    request = directory / 'install-request.json'
    _atomic_json(request, {'installer':str(Path(installer).resolve()),'sha256':release.sha256,'size':release.size,
        'executable':str(Path(sys.executable).resolve()),'profile':str(Path(profile).resolve()),
        'resume_receiver':bool(resume_receiver),'version':release.version})
    return {'helper':str(helper),'request':str(request)}


def launch_update(plan):
    """Called only after successful receiver shutdown and profile lock release."""
    powershell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.Popen([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                      '-File',plan['helper'],'-Request',plan['request'],'-ParentPid',str(os.getpid())],
                     creationflags=0x08000000, close_fds=True)
