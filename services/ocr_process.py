"""Small, document-free diagnostics for an isolated OCR worker."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def memory_snapshot(root=Path('/sys/fs/cgroup')):
    def number(path):
        try:
            value = path.read_text().strip()
            return int(value) if value.isdigit() and int(value) < 2**60 else None
        except OSError:
            return None
    snapshot = {'limit_bytes': number(root / 'memory.max'),
                'current_bytes': number(root / 'memory.current'), 'oom_kill': None}
    try:
        events = dict(line.split() for line in (root / 'memory.events').read_text().splitlines())
        snapshot['oom_kill'] = int(events.get('oom_kill', '0'))
    except (OSError, ValueError):
        pass
    if not (root / 'memory.max').exists():
        snapshot['limit_bytes'] = number(root / 'memory/memory.limit_in_bytes')
        snapshot['current_bytes'] = number(root / 'memory/memory.usage_in_bytes')
    return snapshot


def report_stage(stage):
    target = os.getenv('OCR_STATUS_FILE')
    if target:
        try:
            Path(target).write_text(stage, encoding='utf-8')
        except OSError:
            pass  # Diagnostics must not prevent a document from being read.


def process_failure(returncode, stderr, before, after, stage='unknown'):
    """SIGKILL alone is not proof of OOM. Never log raw OCR or stderr text."""
    error = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else str(stderr or '')
    oom = (before.get('oom_kill') is not None and after.get('oom_kill') is not None
           and after['oom_kill'] > before['oom_kill'])
    code = int(returncode or 0)
    unsigned = code & 0xffffffff
    if oom or 'bad_alloc' in error or 'Failed to allocate memory' in error or unsigned in {0xc0000017, 0xc000012d}:
        reason, retry = 'OCR_MEMORY', True
        message = 'OCR için yeterli bellek ayrılamadı veya sunucunun RAM sınırına ulaşıldı. Sunucunun kullanılabilir RAM miktarını kontrol edin.'
    elif code in {-9, 137}:
        reason, retry = 'OCR_KILLED', True
        message = 'OCR işlemi sunucu tarafından sonlandırıldı. Bellek sınırı veya servis kesintisi olabilir; sunucu kayıtlarını kontrol edin.'
    elif 'ModuleNotFoundError' in error or 'ImportError' in error:
        reason, retry = 'OCR_DEPENDENCY', False
        message = 'Sunucuda OCR bağımlılığı yüklenemedi. Docker derlemesi ve sunucu kayıtları kontrol edilmeli.'
    elif code in {-4, 132} or unsigned == 0xc000001d:
        reason, retry = 'OCR_CPU', False
        message = 'OCR çalışma ortamı sunucunun işlemcisiyle uyumlu değil. Sunucu kayıtları kontrol edilmeli.'
    elif code in {-11, 139} or unsigned == 0xc0000005:
        reason, retry = 'OCR_NATIVE_CRASH', False
        message = 'OCR motoru beklenmedik şekilde kapandı. Sunucu kayıtlarında işlem aşaması ve çıkış kodu bulunuyor.'
    else:
        reason, retry = 'OCR_RESULT_MISSING', False
        message = 'OCR sonuç dosyası oluşturulamadı. Sunucu kayıtlarında işlem aşaması ve çıkış kodu bulunuyor.'
    logger.error('OCR worker failure %s', json.dumps({'reason': reason, 'returncode': code,
                 'stage': stage if stage in {'starting','model_loading','image_loading','reading','verifying','writing'} else 'unknown',
                 'memory_before': before, 'memory_after': after}, sort_keys=True))
    return message + ' [' + reason + ']', retry
