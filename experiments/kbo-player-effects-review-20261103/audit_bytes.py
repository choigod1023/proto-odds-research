"""Verify checkout bytes in independent temporary clones under both CRLF modes."""
import hashlib
import subprocess
import tempfile
from pathlib import Path

SOURCE=Path('C:/Users/user/Documents/Codex/kbeffects')
HERE=Path(__file__).resolve().parent
COMMIT='2156ec6b2c7c54a104c051c5cddc5a9d0b9d46f7'
FILES={'experiments/kbo-player-effects-20261103/contract.json':'6ffdf7947559df052c248a1f9fb5e7ad871321f52e7920126934bf21771465ed','experiments/kbo-player-effects-20261103/artifacts/selection.json':'27c995c0d66bbc9a1e9c1a741735540adb21355ba76fbfac4eb4eff8a650eebb','experiments/kbo-player-effects-20261103/artifacts/predictions.json':'ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9','experiments/kbo-player-effects-20261103/artifacts/results.json':'211b89d365136a166fd13026df5f6358e4b7ecda5db50b4459a2f42e75f19cd8'}

def main():
    with tempfile.TemporaryDirectory(prefix='bytes-audit-',dir=HERE) as root:
        root=Path(root)
        for mode in ('true','false'):
            target=root/mode
            subprocess.run(['git','clone','--quiet','--local','--shared','--no-checkout',str(SOURCE),str(target)],check=True)
            subprocess.run(['git','-C',str(target),'-c','core.autocrlf='+mode,'checkout',COMMIT,'--','.gitattributes'],check=True)
            subprocess.run(['git','-C',str(target),'-c','core.autocrlf='+mode,'checkout',COMMIT,'--',*FILES],check=True)
            for path,expected in FILES.items():
                b=(target/path).read_bytes();assert hashlib.sha256(b).hexdigest()==expected,(mode,path)
    print('PASS: pinned contract, selection, predictions and results bytes match under autocrlf=true and false')
if __name__=='__main__':main()
