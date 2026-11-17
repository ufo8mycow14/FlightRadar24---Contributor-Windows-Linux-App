"""Portable native entry point; HTTP control is an explicit headless test mode."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path


def _write_session(path, value):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(value), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_owned_session(path):
    if path is None:
        return
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict) and value.get('pid') == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        # Never remove an unreadable or replaced session whose owner is unknown.
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='FlightRadar24 — native ADS-B receiver and feeder controller')
    parser.add_argument('--port', type=int, default=8754, help='Loopback port for explicit headless mode')
    parser.add_argument('--headless', '--no-browser', dest='headless', action='store_true',
                        help='Run the authenticated loopback control API without a desktop window')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--start-receiver', action='store_true',
                        help='Start reception for this launch without changing the saved startup preference')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--practice', action='store_true',
                        help='Open isolated dummy-data practice without a receiver or profile')
    parser.add_argument('--session-file', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parent
    if not getattr(sys, 'frozen', False):
        sys.path[:0] = [str(source.parent / 'work/desktoplibs'), str(source.parent / 'work/applibs')]

    if args.practice:
        if args.headless or args.start_receiver:
            parser.error('--practice cannot start a receiver or headless server')
        from PySide6.QtWidgets import QApplication
        from frw.desktop import apply_desktop_style
        from frw.native_support import PracticeDialog
        app = QApplication.instance() or QApplication([])
        apply_desktop_style(app)
        dialog = PracticeDialog()
        dialog.show()
        return app.exec()

    from frw.instance import ProfileInstance
    from frw.platform_support import data_directory

    # Preserve the established profile name. Acquire ownership before any profile
    # files, Settings or Receiver objects are opened or created.
    directory = args.data_dir or data_directory('FlightRadarWindows')
    instance = ProfileInstance(directory)
    receiver = None
    server = None
    acquired = False
    try:
        acquired = instance.acquire()
        if not acquired:
            instance.request_activation()
            return 0

        from frw.config import Settings
        from frw.runtime import Receiver
        settings = Settings(directory)
        bundle = args.bundle or (Path(sys.executable).parent / 'runtime' if getattr(sys, 'frozen', False)
                                 else source.parent / 'work/runtime')
        receiver = Receiver(settings, bundle)
        if args.headless:
            from frw.server import ControlServer
            server = ControlServer(('127.0.0.1', args.port), receiver, source / 'frw/web')
            _write_session(args.session_file, {'url': server.url + '#' + server.token,
                                              'pid': os.getpid(), 'mode': 'headless'})
            if args.start_receiver or settings.value['autostart_receiver']:
                try:
                    receiver.start()
                except Exception as error:
                    receiver.log.add('app', 'Automatic start failed: ' + str(error))
            try:
                server.serve_forever(poll_interval=.3)
            except KeyboardInterrupt:
                pass
            return 0

        from frw.desktop import run_desktop
        ready_errors = []
        def ready():
            try:
                _write_session(args.session_file, {'pid': os.getpid(), 'mode': 'desktop', 'ready': True})
            except Exception as error:
                ready_errors.append(error)
                # Exceptions cannot propagate back through a Qt exec callback.
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    app.exit(1)
        result = run_desktop(receiver, instance, ready, start_receiver=args.start_receiver)
        if ready_errors:
            raise ready_errors[0]
        return result or 0
    finally:
        # Release every resource even if an earlier cleanup operation fails. The
        # Windows mutex must be closed on its acquiring main thread.
        cleanup_errors = []
        if receiver is not None:
            for operation in (receiver.feeder.signup_stop, receiver.stop):
                try:
                    operation()
                except Exception as error:
                    cleanup_errors.append(error)
        if server is not None:
            try:
                server.server_close()
            except Exception as error:
                cleanup_errors.append(error)
        if receiver is not None:
            for error in cleanup_errors:
                try:
                    receiver.log.add('app', 'Shutdown cleanup failed: ' + str(error))
                except Exception:
                    pass
            try:
                receiver.log.close()
            except Exception as error:
                cleanup_errors.append(error)
        if acquired:
            _remove_owned_session(args.session_file)
        try:
            instance.close()
        except Exception as error:
            cleanup_errors.append(error)
        if cleanup_errors and sys.exc_info()[0] is None:
            raise RuntimeError('Application shutdown could not finish: ' + str(cleanup_errors[0]))


def report_startup_error(error, *, headless=False):
    message = 'FlightRadar24 could not start.\n\n' + str(error)
    if os.name == 'nt' and not headless:
        ctypes.windll.user32.MessageBoxW(None, message, 'FlightRadar24', 0x10)
    else:
        print(message, file=sys.stderr)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        report_startup_error(error, headless=any(arg in ('--headless', '--no-browser') for arg in sys.argv[1:]))
        raise SystemExit(1)
