import io
import tarfile
from pathlib import Path

from research.kbo_forward import bootstrap_runtime as b


def test_pinned_archive_carries_model_code_weights_and_optional_separate_state(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    files = ['research/m1_service/model.py', 'research/m1_service/frozen-models.json', 'research/m1_service/observer.py']
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        for name in files:
            body = (root/name).read_bytes(); member = tarfile.TarInfo(name); member.size = len(body)
            tar.addfile(member, io.BytesIO(body))
    def git(command, **kwargs):
        if 'status' in command:
            return ''
        if 'rev-parse' in command:
            return 'a'*40+'\n'
        assert 'archive' in command and 'research/m1_service' in command
        return stream.getvalue()
    monkeypatch.setattr(b.subprocess, 'check_output', git)
    receipt = b.bootstrap(tmp_path/'runtime state', tmp_path/'separate M1 state')
    assert receipt['m1_state_root'] == str((tmp_path/'separate M1 state').resolve())
    for name in files:
        assert (Path(receipt['runtime_root'])/name).read_bytes() == (root/name).read_bytes()
