import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from hosted.windows_background import main, open_dashboard


class BackgroundTests(unittest.TestCase):
    def test_worker_has_log_streams_and_restores_them(self):
        original = sys.stdout, sys.stderr
        with tempfile.TemporaryDirectory() as directory:
            def run_module(*args, **kwargs):
                self.assertIsNotNone(sys.stdout)
                self.assertIs(sys.stdout, sys.stderr)
                print('worker completed')
            with patch.dict(os.environ, {'LOCALAPPDATA': directory}), \
                 patch.object(sys, 'argv', ['background', 'worker']), \
                 patch('hosted.windows_background.os.chdir'), \
                 patch('hosted.windows_background.load_key'), \
                 patch('hosted.windows_background.runpy.run_module', side_effect=run_module):
                main()
            self.assertEqual((sys.stdout, sys.stderr), original)
            self.assertIn('worker completed', (Path(directory) / 'Courtside' / 'background-worker.log').read_text())

    def test_opening_running_dashboard_does_not_start_another_server(self):
        with patch('hosted.windows_background.dashboard_ready', return_value=True), \
             patch('hosted.windows_background.subprocess.run') as run, \
             patch('hosted.windows_background.webbrowser.open') as browser:
            open_dashboard()
            run.assert_not_called()
            browser.assert_called_once_with('http://127.0.0.1:8765')
