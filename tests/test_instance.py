from pathlib import Path
import os, subprocess, sys, tempfile, textwrap, unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from frw.instance import ProfileInstance

HELPER=textwrap.dedent('''\
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from frw.instance import ProfileInstance
    instance = ProfileInstance(sys.argv[2])
    owner = instance.acquire()
    print("OWNER" if owner else "DUPLICATE", flush=True)
    if not owner:
        print("SIGNALLED" if instance.request_activation() else "NO_EVENT", flush=True)
        instance.close()
    else:
        try:
            sys.stdin.readline()
        finally:
            instance.close()
''')


@unittest.skipUnless(os.name=='nt','ProfileInstance uses native Windows synchronization objects.')
class ProfileInstanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.profile=self.root/'profile-that-must-not-be-created'
        self.processes=[]
        self.instances=[]

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream and not stream.closed:stream.close()
        for instance in self.instances:
            try: instance.close()
            except RuntimeError: pass
        self.temporary.cleanup()

    def instance(self,path=None):
        value=ProfileInstance(path or self.profile)
        self.instances.append(value)
        return value

    def child(self,path=None):
        process=subprocess.Popen(
            [sys.executable,'-c',HELPER,str(ROOT/'src'),str(path or self.profile)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,bufsize=1,creationflags=0x08000000)
        self.processes.append(process)
        return process

    def test_one_owner_and_activation_event(self):
        owner=self.instance()
        self.assertTrue(owner.acquire())
        process=self.child()
        self.assertEqual(process.stdout.readline().strip(),'DUPLICATE')
        self.assertEqual(process.stdout.readline().strip(),'SIGNALLED')
        self.assertEqual(process.wait(timeout=5),0)
        self.assertTrue(owner.activation_requested())
        self.assertFalse(owner.activation_requested(),'Auto-reset event should consume one request.')
        self.assertFalse(self.profile.exists(),'Mutex creation must not create the profile directory.')

    def test_different_profile_paths_are_independent(self):
        first=self.instance(self.root/'profile-a')
        second=self.instance(self.root/'profile-b')
        self.assertTrue(first.acquire())
        self.assertTrue(second.acquire())
        self.assertNotEqual(first._mutex_name,second._mutex_name)
        self.assertFalse((self.root/'profile-a').exists())
        self.assertFalse((self.root/'profile-b').exists())

    def test_process_exit_abandons_mutex_and_next_instance_recovers(self):
        observer=self.instance()
        process=self.child()
        self.assertEqual(process.stdout.readline().strip(),'OWNER')
        self.assertFalse(observer.acquire(),'The child owns the profile mutex while alive.')
        process.kill()
        process.wait(timeout=5)
        self.assertTrue(observer.acquire(),'A terminated owner must not strand its profile.')
        self.assertTrue(observer.abandoned,'WAIT_ABANDONED is reported for startup recovery.')
        self.assertFalse(self.profile.exists())

    def test_close_is_idempotent_and_requests_fail_cleanly_after_close(self):
        requester=self.instance()
        self.assertTrue(requester.acquire())
        self.assertTrue(requester.request_activation())
        self.assertTrue(requester.activation_requested())
        requester.close()
        requester.close()
        self.assertFalse(requester.request_activation())
        self.assertFalse(requester.activation_requested())
        with self.assertRaises(RuntimeError):requester.acquire()


if __name__=='__main__':unittest.main(verbosity=2)
