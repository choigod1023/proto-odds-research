from pathlib import Path
import random
import shutil
import subprocess
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto_null_9999 import read_archive,write_archive,slim
from lotto_null_resume import durable_archive,durable_json


def test_exact_native_predicate_boundaries_and_random_inputs(tmp_path):
    compiler=shutil.which('g++')
    if not compiler: pytest.skip('g++ unavailable')
    exe=tmp_path/'fast.exe'; source=Path(__file__).resolve().parents[1]/'src/lotto_seed_scan_fast.cpp'
    subprocess.run([compiler,'-O3','-std=c++17','-pthread','-static',str(source),'-o',str(exe)],check=True)
    rng=random.Random(20261016); n=8145060; mask=2**64-1
    pairs=[(rng.getrandbits(64),rng.randrange(n)) for _ in range(50000)]
    for target in [0,1,2,3,4,n-1]+[rng.randrange(n) for _ in range(1000)]:
        maximum=(mask-target)//n
        for q in (0,1,maximum,maximum//2):
            for delta in (-4,-1,0,1,4):
                value=q*n+target+delta
                if 0<=value<=mask: pairs.append((value,target))
        pairs.extend([(0,target),(mask,target)])
    result=subprocess.run([str(exe),'--predicate'],input=''.join(f'{v} {t}\n' for v,t in pairs),text=True,capture_output=True,check=True)
    assert list(map(int,result.stdout.split()))==[int(v%n==t) for v,t in pairs]


def test_checkpoint_roundtrip_and_summary_preserves_metrics(tmp_path):
    rows=[{'history_index':0,'pair_count':2}, {'history_index':1,'pair_count':4,'maximum_training_hits':4,
        'validation':{'status':'evaluated','mean':.8,'z_against_matched_controls':.1,'control_means':[.8]},
        'final':{'status':'no_survivors'}}]
    path=tmp_path/'rows.jsonl.gz'; write_archive(path,rows)
    assert read_archive(path)==rows
    assert slim(rows[0])==rows[0]
    assert slim(rows[1])['validation']['mean']==.8
    assert 'control_means' not in slim(rows[1])['validation']


def test_durable_checkpoint_is_readable_and_replaces_incomplete_metadata(tmp_path):
    import json
    rows=[{'history_index':4800,'value':'fixed'}]
    archive=tmp_path/'recovered.jsonl.gz'
    durable_archive(archive,rows)
    assert read_archive(archive)==rows
    metadata=tmp_path/'manifest.json'; metadata.write_bytes(b'\x00'*20)
    durable_json(metadata,{'last':4800})
    assert json.loads(metadata.read_text())=={'last':4800}
