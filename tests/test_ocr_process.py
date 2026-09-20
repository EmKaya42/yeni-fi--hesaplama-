import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import ocr_process, paddle_ocr
from services.document_ocr import RetryableOCRError


def test_cgroup_memory_and_oom_counter(tmp_path):
    (tmp_path / 'memory.max').write_text('536870912')
    (tmp_path / 'memory.current').write_text('400000000')
    (tmp_path / 'memory.events').write_text('oom 2\noom_kill 1\n')
    assert ocr_process.memory_snapshot(tmp_path) == {
        'limit_bytes': 536870912, 'current_bytes': 400000000, 'oom_kill': 1}
    (tmp_path / 'memory.max').write_text('max')
    assert ocr_process.memory_snapshot(tmp_path)['limit_bytes'] is None


@pytest.mark.parametrize('code,stderr,before,after,reason,retry', [
    (-9, b'', {}, {}, 'OCR_KILLED', True),
    (-9, b'', {'oom_kill': 0}, {'oom_kill': 1}, 'OCR_MEMORY', True),
    (1, b'bad_alloc', {}, {}, 'OCR_MEMORY', True),
    (1, b'ModuleNotFoundError', {}, {}, 'OCR_DEPENDENCY', False),
    (-4, b'', {}, {}, 'OCR_CPU', False),
    (-11, b'', {}, {}, 'OCR_NATIVE_CRASH', False),
    (0, b'', {}, {}, 'OCR_RESULT_MISSING', False),
])
def test_failure_classification_does_not_log_document_text(code, stderr, before, after, reason, retry, caplog):
    message, actual_retry = ocr_process.process_failure(code, stderr + b' PRIVATE_DOCUMENT', before, after, 'PRIVATE_STAGE')
    assert f'[{reason}]' in message
    assert actual_retry is retry
    assert 'PRIVATE' not in caplog.text
    assert '"stage": "unknown"' in caplog.text


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(paddle_ocr, 'verified_model_paths', lambda: {})
    monkeypatch.setattr(paddle_ocr, 'memory_snapshot', lambda: {})
    monkeypatch.delenv('OCR_COMPACT', raising=False)


def test_memory_kill_retries_compact_within_same_deadline(worker, monkeypatch, tmp_path):
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(returncode=-9, stderr=b'')
        assert kwargs['env']['OCR_COMPACT'] == '1'
        assert 0 < kwargs['timeout'] <= calls[0]['timeout'] <= 240
        Path(command[-1]).write_text(json.dumps({'issues': ['Uncertain payment'], 'total_amount': '120.00'}))
        return SimpleNamespace(returncode=0, stderr=b'')
    monkeypatch.setattr(paddle_ocr.subprocess, 'run', run)
    result = paddle_ocr.read_document(tmp_path / 'source.jpg', 0, 'receipts', 1)
    assert len(calls) == 2
    assert result['issues'] == ['Uncertain payment']


def test_repeated_memory_kill_is_bounded(worker, monkeypatch, tmp_path):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=-9, stderr=b'')
    monkeypatch.setattr(paddle_ocr.subprocess, 'run', run)
    with pytest.raises(RetryableOCRError, match='OCR_KILLED'):
        paddle_ocr.read_document(tmp_path / 'source.jpg', 0, 'receipts', 1)
    assert len(calls) == 2


@pytest.mark.parametrize('content,code,expected', [
    ('{', 0, 'OCR_RESULT_INVALID'),
    ('[]', 0, 'OCR_RESULT_INVALID'),
    ('{}', 0, 'OCR_RESULT_INVALID'),
    ('{"issues": []}', -11, 'OCR_NATIVE_CRASH'),
    ('{"error": "Model missing"}', 1, 'Model missing'),
])
def test_incomplete_or_failed_worker_never_becomes_success(worker, monkeypatch, tmp_path, content, code, expected):
    def run(command, **kwargs):
        Path(command[-1]).write_text(content)
        return SimpleNamespace(returncode=code, stderr=b'')
    monkeypatch.setattr(paddle_ocr.subprocess, 'run', run)
    with pytest.raises((RuntimeError, RetryableOCRError), match=expected):
        paddle_ocr.read_document(tmp_path / 'source.jpg', 0, 'receipts', 1)
