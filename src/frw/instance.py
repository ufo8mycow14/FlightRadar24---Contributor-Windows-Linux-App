"""Windows per-profile single-instance coordination and window activation."""
from __future__ import annotations

import ctypes
import hashlib
import os
import platform
from ctypes import wintypes
from pathlib import Path


WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
ERROR_INSUFFICIENT_BUFFER = 122
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
SDDL_REVISION_1 = 1


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', wintypes.DWORD)]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [('User', _SID_AND_ATTRIBUTES)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ('nLength', wintypes.DWORD),
        ('lpSecurityDescriptor', ctypes.c_void_p),
        ('bInheritHandle', wintypes.BOOL),
    ]


def _win_api():
    if os.name != 'nt':
        raise OSError('Profile instance coordination is available on Windows only.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)

    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentThreadId.argtypes = []
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    kernel.CreateMutexW.argtypes = [ctypes.POINTER(_SECURITY_ATTRIBUTES), wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CreateEventW.argtypes = [ctypes.POINTER(_SECURITY_ATTRIBUTES), wintypes.BOOL, wintypes.BOOL,
                                    wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.SetEvent.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    return kernel, advapi


def _current_user_sid(kernel, advapi):
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(needed))
        if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER or not needed.value:
            raise ctypes.WinError(ctypes.get_last_error())
        storage = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, TOKEN_USER, storage, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid_pointer = ctypes.cast(storage, ctypes.POINTER(_TOKEN_USER)).contents.User.Sid
        rendered = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(rendered)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return rendered.value
        finally:
            kernel.LocalFree(ctypes.cast(rendered, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


class ProfileInstance:
    """Coordinate one app instance per Windows user and canonical profile path.

    The mutex and auto-reset activation event use the Global namespace so two
    interactive sessions of the same user cannot start the same profile twice.
    Their DACL grants full access only to that user and LocalSystem. A low
    mandatory label lets the same user's elevated and unelevated sessions share
    the objects. No profile file or directory is created or modified here.

    ``acquire`` and ``close`` must run on the same thread because Windows mutex
    ownership is thread-bound. A false return means another process owns the
    profile; ``request_activation`` signals that process's event.
    """

    def __init__(self, directory: str | os.PathLike[str]):
        self.directory = Path(directory).expanduser().resolve(strict=False)
        if os.name != 'nt':
            if platform.system() != 'Linux':
                raise OSError('Profile instance coordination is currently supported on Windows and Linux.')
            self._init_posix()
            return
        self._kernel, self._advapi = _win_api()
        sid = _current_user_sid(self._kernel, self._advapi)
        self._sid = sid
        identity = (sid + '\0' + os.path.normcase(os.path.normpath(str(self.directory)))).encode('utf-8')
        digest = hashlib.sha256(identity).hexdigest()
        self._mutex_name = 'Global\\FlightRadar24-' + digest + '-mutex'
        self._event_name = 'Global\\FlightRadar24-' + digest + '-activate'
        self._mutex = None
        self._event = None
        self._owner_thread = None
        self._acquired = False
        self.abandoned = False
        self._closed = False
        self._create_objects()

    def _init_posix(self):
        import socket
        import tempfile
        uid = os.getuid()
        identity = f'{uid}\0{os.path.normcase(str(self.directory))}'.encode('utf-8')
        digest = hashlib.sha256(identity).hexdigest()[:32]
        runtime = os.environ.get('XDG_RUNTIME_DIR')
        if runtime:
            candidate = Path(runtime)
            try:
                info = candidate.stat()
                if info.st_uid == uid and info.st_mode & 0o077 == 0:
                    base = candidate
                else:
                    base = Path(tempfile.gettempdir())
            except OSError:
                base = Path(tempfile.gettempdir())
        else:
            base = Path(tempfile.gettempdir())
        if base == Path(tempfile.gettempdir()):
            base = base / f'flightradar24-{uid}'
            base.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = base.stat()
            if info.st_uid != uid or info.st_mode & 0o077:
                raise PermissionError('Cannot create a private per-user instance directory.')
        self._posix_lock_path = base / f'flightradar24-{digest}.lock'
        self._posix_socket_path = base / f'flightradar24-{digest}.sock'
        self._posix_lock = None
        self._posix_socket = None
        self._posix_socket_type = socket
        self._acquired = False
        self._closed = False
        self.abandoned = False

    def _acquire_posix(self):
        import fcntl
        if self._closed: raise RuntimeError('Profile instance handle is closed.')
        if self._acquired: return True
        fd = os.open(self._posix_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(self._posix_lock_path, 0o600)
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._posix_lock = fd
        sock = self._posix_socket_type.socket(self._posix_socket_type.AF_UNIX,
                                               self._posix_socket_type.SOCK_DGRAM)
        try:
            self._posix_socket_path.unlink(missing_ok=True)
            sock.bind(str(self._posix_socket_path))
            os.chmod(self._posix_socket_path, 0o600)
            sock.setblocking(False)
        except Exception:
            sock.close()
            fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd); self._posix_lock = None
            raise
        self._posix_socket = sock
        self._acquired = True
        return True

    def _create_handle(self, name: str, event: bool):
        descriptor = ctypes.c_void_p()
        revision = wintypes.DWORD()
        sddl = f'D:P(A;;GA;;;{self._sid})(A;;GA;;;SY)S:(ML;;NW;;;LW)'
        if not self._advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            attributes = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
            if event:
                handle = self._kernel.CreateEventW(ctypes.byref(attributes), False, False, name)
            else:
                handle = self._kernel.CreateMutexW(ctypes.byref(attributes), False, name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            return handle
        finally:
            self._kernel.LocalFree(descriptor)

    def _create_objects(self):
        try:
            self._mutex = self._create_handle(self._mutex_name, event=False)
            self._event = self._create_handle(self._event_name, event=True)
        except Exception:
            self._close_handles()
            raise

    def acquire(self) -> bool:
        if os.name != 'nt': return self._acquire_posix()
        if self._closed:
            raise RuntimeError('Profile instance handle is closed.')
        if self._acquired:
            return True
        result = self._kernel.WaitForSingleObject(self._mutex, 0)
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            self._acquired = True
            self.abandoned = result == WAIT_ABANDONED
            self._owner_thread = self._kernel.GetCurrentThreadId()
            return True
        if result == WAIT_TIMEOUT:
            return False
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        raise OSError(f'Unexpected Windows mutex wait result: 0x{result:08X}')

    def request_activation(self) -> bool:
        """Signal the profile's auto-reset event; return False if no handle exists."""
        if os.name != 'nt':
            sock = self._posix_socket_type.socket(self._posix_socket_type.AF_UNIX,
                                                   self._posix_socket_type.SOCK_DGRAM)
            try:
                sock.sendto(b'activate', str(self._posix_socket_path))
                return True
            except OSError:
                return False
            finally: sock.close()
        if self._closed or not self._event:
            return False
        if not self._kernel.SetEvent(self._event):
            error = ctypes.get_last_error()
            if error in (2, 6):
                return False
            raise ctypes.WinError(error)
        return True

    def activation_requested(self) -> bool:
        """Consume one pending activation request without waiting."""
        if os.name != 'nt':
            if not self._posix_socket: return False
            try: return self._posix_socket.recv(64) == b'activate'
            except BlockingIOError: return False
        if self._closed or not self._event:
            return False
        result = self._kernel.WaitForSingleObject(self._event, 0)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        raise OSError(f'Unexpected Windows event wait result: 0x{result:08X}')

    def _close_handles(self):
        for attribute in ('_event', '_mutex'):
            handle = getattr(self, attribute, None)
            if handle:
                self._kernel.CloseHandle(handle)
                setattr(self, attribute, None)

    def close(self):
        if os.name != 'nt':
            if self._closed: return
            if self._posix_socket:
                self._posix_socket.close(); self._posix_socket = None
                self._posix_socket_path.unlink(missing_ok=True)
            if self._posix_lock is not None:
                import fcntl
                fcntl.flock(self._posix_lock, fcntl.LOCK_UN)
                os.close(self._posix_lock); self._posix_lock = None
            self._acquired = False; self._closed = True
            return
        if self._closed:
            return
        if self._acquired:
            thread_id = self._kernel.GetCurrentThreadId()
            if thread_id != self._owner_thread:
                raise RuntimeError('Profile mutex must be released on the acquiring thread.')
            if not self._kernel.ReleaseMutex(self._mutex):
                raise ctypes.WinError(ctypes.get_last_error())
            self._acquired = False
            self._owner_thread = None
        self._close_handles()
        self._closed = True

    def __enter__(self):
        if not self.acquire():
            self.close()
            raise RuntimeError('Another FlightRadar24 instance owns this profile.')
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

