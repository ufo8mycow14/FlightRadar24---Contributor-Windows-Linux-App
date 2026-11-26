import os
import subprocess
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from frw.updates import (CHECK_INTERVAL, REPOSITORY, UpdatePreferences, check_release,
                         download_release, parse_release, version_tuple, _Redirects)
from urllib.request import Request


def release_fixture(version='0.8.0', payload=b'fictional installer'):
    name = f'FlightRadar24-{version}-Windows-x64-Setup.exe'
    return {'tag_name':'v'+version,'draft':False,'prerelease':False,
            'html_url':f'https://github.com/{REPOSITORY}/releases/tag/v{version}',
            'assets':[{'name':name,'browser_download_url':f'https://github.com/{REPOSITORY}/releases/download/v{version}/{name}',
                       'size':len(payload),'digest':'sha256:'+hashlib.sha256(payload).hexdigest()}]}


class UpdateTests(unittest.TestCase):
    def test_version_order_avoids_downgrades_and_prereleases(self):
        self.assertGreater(version_tuple('0.10.0'),version_tuple('0.9.9'))
        for version in ('0.8.0','1.0.0'):
            self.assertIsNone(parse_release(release_fixture(),version))
        for value in ('v1.0.0-beta','1.0','1.00.1','../0.8.0','9999999.0.0',None):
            with self.assertRaises(ValueError): version_tuple(value)
        data = release_fixture(); data['prerelease'] = True
        with self.assertRaises(ValueError): parse_release(data,'0.7.1')

    def test_metadata_requires_unique_bounded_exact_repo_installer_and_digest(self):
        self.assertEqual(parse_release(release_fixture(),'0.7.1').version,'0.8.0')
        variants = []
        for key,value in [('browser_download_url','https://example.org/Setup.exe'),('digest',None),('size',False),('size',10**10)]:
            d=release_fixture();d['assets'][0][key]=value;variants.append(d)
        d=release_fixture();d['assets']*=2;variants.append(d)
        d=release_fixture();d['assets']=[];variants.append(d)
        d=release_fixture();d['html_url']='https://github.com/other/repo';variants.append(d)
        for d in variants:
            with self.assertRaises(ValueError): parse_release(d,'0.7.1')

    def test_redirect_rejects_http_and_other_hosts(self):
        handler=_Redirects()
        request=Request('https://github.com/example')
        for url in ('http://github.com/file','https://github.com.evil.test/file','https://user@github.com/file','file:///C:/test'):
            with self.assertRaises(ValueError): handler.redirect_request(request,None,302,'',{},url)
        self.assertEqual(handler.redirect_request(request,None,302,'',{},'https://release-assets.githubusercontent.com/file').host,
                         'release-assets.githubusercontent.com')

    def test_verified_download_reuses_cache_and_rejects_truncation_and_tampering(self):
        payload=b'fictional installer'
        release=parse_release(release_fixture(payload=payload),'0.7.1')
        with tempfile.TemporaryDirectory() as directory:
            with patch('frw.updates._open',return_value=io.BytesIO(payload)):
                installer=download_release(release,directory)
            with patch('frw.updates._open',side_effect=AssertionError('No network needed')):
                self.assertEqual(download_release(release,directory),installer)
            installer.write_bytes(b'tampered')
            for bad in (payload[:-1],payload+b'extra',b'x'*len(payload)):
                with patch('frw.updates._open',return_value=io.BytesIO(bad)):
                    with self.assertRaises(ValueError): download_release(release,directory)
                self.assertEqual(list(Path(directory).glob('*.part')),[])

    def test_twice_daily_schedule_survives_restart_disable_and_clock_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            prefs=UpdatePreferences(directory)
            self.assertFalse(prefs.value['install_automatically'])
            self.assertTrue(prefs.due(100))
            prefs.value['last_check']=100;prefs.save()
            prefs=UpdatePreferences(directory)
            self.assertFalse(prefs.due(100+CHECK_INTERVAL-1))
            self.assertTrue(prefs.due(100+CHECK_INTERVAL))
            self.assertTrue(prefs.due(99))
            prefs.value.update(check_automatically=False,install_automatically=True);prefs.save()
            self.assertFalse(UpdatePreferences(directory).due(100+CHECK_INTERVAL))
            self.assertFalse(UpdatePreferences(directory).value['install_automatically'])

    @unittest.skipUnless(os.name == 'nt','Windows helper validation')
    def test_update_helper_accepts_verified_payload_and_rejects_tampering_without_execution(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='frw update ') as temporary:
            folder=Path(temporary)
            payload=b'fictional installer, never executed'
            installer=folder/'FlightRadar24-0.8.0-Windows-x64-Setup.exe'
            installer.write_bytes(payload)
            request=folder/'request.json'
            request.write_text(json.dumps({'version':'0.8.0','installer':str(installer),
                'executable':str(folder/'FlightRadar24.exe'),'profile':str(folder/'profile with spaces'),
                'sha256':hashlib.sha256(payload).hexdigest(),'size':len(payload),'resume_receiver':False}),encoding='utf-8')
            command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                     '-File',str(root/'src/frw/data/Apply-Update.ps1'),'-Request',str(request),
                     '-ParentPid',str(os.getpid()),'-ValidateOnly']
            result=subprocess.run(command,capture_output=True,timeout=20,creationflags=0x08000000)
            self.assertEqual(result.returncode,0,result.stderr)
            installer.write_bytes(b'changed')
            result=subprocess.run(command,capture_output=True,timeout=20,creationflags=0x08000000)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((folder/'install-result.json').exists())

    def test_metadata_read_has_size_limit(self):
        with patch('frw.updates._open',return_value=io.BytesIO(b'x'*(1024*1024+1))):
            with self.assertRaises(ValueError):check_release('0.7.1')
