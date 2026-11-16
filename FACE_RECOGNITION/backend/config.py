"""Local camera demo limits; enrollment model settings come from face_config.json."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend'


class Config:
    HOST = '127.0.0.1'
    PORT = 5055
    MAX_CONTENT_LENGTH = 65 * 1024 * 1024
    MAX_FRAME_DIMENSION = 960
    CLIENT_FRAME_DIMENSION = 640
    FRAME_INTERVAL_MS = 150
    REQUEST_TIMEOUT_MS = 120000
    TRUSTED_HOSTS = ['localhost', '127.0.0.1', '[::1]']
    TESTING = False
    AUTO_ATTENDANCE_ENABLED = False
    # Photo upload/manual capture enrollment is retired in favour of live camera
    # enrollment (backend/enrollment_routes.py). Kept only for legacy QA scripts.
    LEGACY_PHOTO_UPLOAD_ENABLED = False
    AUTO_ATTENDANCE_EVIDENCE = ROOT / 'outputs' / 'automatic_attendance_evaluation.json'
    AUTO_ATTENDANCE_ACTIVATION = ROOT / 'outputs' / 'automation_activation.json'
