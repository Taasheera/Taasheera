import os
from pathlib import Path
import subprocess
import sys

import pytest

from qa_mailbox import FileResetSender, messages


def test_preview_mail_is_private_and_local_only(tmp_path):
    directory = tmp_path / 'outbox'
    sender = FileResetSender(directory)
    sender.send_reset('synthetic@example.com', '123456')
    assert messages(directory)[0]['code'] == '123456'
    assert len(list(directory.iterdir())) == 1
    if os.name != 'nt':
        assert directory.stat().st_mode & 0o777 == 0o700
        assert next(directory.iterdir()).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('environment', ['production', ''])
def test_qa_mail_factory_refuses_production_even_with_a_directory(tmp_path, environment):
    result = subprocess.run([sys.executable, '-c', 'import qa_app'], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'APP_ENV': environment, 'TAASHEERA_QA_MAIL_DIR': str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'explicit isolated development preview' in result.stderr
