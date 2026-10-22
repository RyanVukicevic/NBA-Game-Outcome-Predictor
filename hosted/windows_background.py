"""Console-free Windows entry points for scheduled work and the dashboard."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import time
import webbrowser


def decrypt_key(path):
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    encrypted = bytes.fromhex(Path(path).read_text().strip())
    buffer = ctypes.create_string_buffer(encrypted)
    source = Blob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size).decode('utf-16-le')
    finally:
        kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def load_key(directory):
    if os.environ.get('ODDS_API_KEY'):
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as registry:
            value, _ = winreg.QueryValueEx(registry, 'ODDS_API_KEY')
            if value:
                os.environ['ODDS_API_KEY'] = value
                return
    except FileNotFoundError:
        pass
    path = directory / 'odds-api-key.dpapi'
    if path.exists():
        os.environ['ODDS_API_KEY'] = decrypt_key(path)


def dashboard_ready():
    try:
        with socket.create_connection(('127.0.0.1', 8765), timeout=1):
            return True
    except OSError:
        return False


def open_dashboard():
    if not dashboard_ready():
        subprocess.run(['schtasks.exe', '/Run', '/TN', 'Courtside Dashboard'],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        for _ in range(120):
            if dashboard_ready():
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Dashboard did not start; inspect the local dashboard log.')
    webbrowser.open('http://127.0.0.1:8765')


def main():
    mode = sys.argv[1]
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    directory = Path(os.environ['LOCALAPPDATA']) / 'Courtside'
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / f'background-{mode}.log'
    if log.exists() and log.stat().st_size > 2_000_000:
        log.replace(directory / f'background-{mode}.previous.log')
    # pythonw has no standard streams; HTTP server logging needs real handles.
    original_streams = sys.stdout, sys.stderr
    with log.open('a', encoding='utf-8', buffering=1) as stream:
        sys.stdout = sys.stderr = stream
        try:
            if mode == 'worker':
                load_key(directory)
                runpy.run_module('hosted.local_worker', run_name='__main__')
            elif mode == 'dashboard':
                sys.argv = ['dashboard.server', '--host', '127.0.0.1', '--port', '8765']
                runpy.run_module('dashboard.server', run_name='__main__')
            elif mode == 'open':
                open_dashboard()
            else:
                raise ValueError('Expected worker, dashboard, or open mode.')
        except Exception:
            import traceback
            traceback.print_exc()
            raise SystemExit(1)
        finally:
            sys.stdout, sys.stderr = original_streams


if __name__ == '__main__':
    main()
