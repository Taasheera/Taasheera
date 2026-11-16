"""Preview-only factory with an isolated file mail sink; production imports app.main."""
import os
from pathlib import Path

from app.main import create_app
from qa_mailbox import FileResetSender

directory = os.environ.get('TAASHEERA_QA_MAIL_DIR', '')
if os.environ.get('APP_ENV') != 'development' or not directory or not Path(directory).is_absolute():
    raise RuntimeError('QA email capture requires an explicit isolated development preview.')
app = create_app(reset_sender=FileResetSender(directory))
