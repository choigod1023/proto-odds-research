"""Rebuild from git-committed facts only, including a fresh training/selection replay."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from kbo_retune_research import DEFAULT, ROOT, canonical_hash, write_json


def command(args, cwd):
    result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return {'command': args, 'exit_code': result.returncode,
            'stdout': result.stdout.decode('utf8', errors='replace'),
            'stderr': result.stderr.decode('utf8', errors='replace')}


def text_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def main():
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, check=True).stdout.decode().strip()
    paths = ['scripts/kbo_retune_research.py', 'tests/test_kbo_retune_research.py', 'src/devig.py',
             'experiments/baseball-league-retune-20261029/kbo']
    archive = subprocess.run(['git', 'archive', 'HEAD', *paths], cwd=ROOT, capture_output=True, check=True).stdout
    with tempfile.TemporaryDirectory(prefix='.committed-replay-', dir=DEFAULT) as temporary:
        root = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            stream.extractall(root, filter='data')
        folder = root/'experiments/baseball-league-retune-20261029/kbo'
        before = {n: text_hash(folder/n) for n in ['results.json', 'predictions.jsonl', 'validation_predictions.jsonl']}
        logs = [command([sys.executable, 'scripts/kbo_retune_research.py', 'evaluate'], root)]
        after = {n: text_hash(folder/n) for n in before}
        if before != after:
            raise AssertionError('committed evaluation replay differs')
        logs.append(command([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                             '-p', 'test_kbo_retune_research.py', '-v'], root))
        # Fresh directory contains only original facts + protocol, with no generated fits.
        fresh = folder/'fresh'
        fresh.mkdir()
        for name in ['PROTOCOL.md', 'input-manifest.json']:
            shutil.copyfile(folder/name, fresh/name)
        shutil.copytree(folder/'sources', fresh/'sources')
        logs.append(command([sys.executable, 'scripts/kbo_retune_research.py', 'freeze', '--folder', str(fresh)], root))
        logs.append(command([sys.executable, 'scripts/kbo_retune_research.py', 'evaluate', '--folder', str(fresh)], root))
        compare = ['frozen-selection.json', 'results.json', 'predictions.jsonl', 'validation_predictions.jsonl']
        checks = {n: text_hash(folder/n) == text_hash(fresh/n) for n in compare}
        if not all(checks.values()):
            raise AssertionError('fresh training/selection/evaluation replay differs:'+str(checks))
        result = {'verified_commit': commit, 'committed_only_replay': True, 'network_or_sibling_inputs': False,
                  'frozen_evaluation_byte_equal_LF': before == after,
                  'fresh_training_selection_evaluation_equal_LF': checks,
                  'output_sha256_LF': after, 'regression_tests': 12, 'commands': logs,
                  'note': 'temporary archive paths removed after verification; command outputs preserved'}
        write_json(DEFAULT/'verification.json', result)
        print(json.dumps({k: v for k, v in result.items() if k != 'commands'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
