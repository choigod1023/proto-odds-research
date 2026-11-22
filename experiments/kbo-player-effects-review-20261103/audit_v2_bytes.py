"""Check corrected frozen checkout hashes under both CRLF policies."""
import hashlib,subprocess,tempfile
from pathlib import Path
from audit_v2_results import WORKER,RESULT

HERE=Path(__file__).resolve().parent
FILES={'experiments/kbo-player-effects-20261103/v2/correction-contract.json':'c368b227534310a67a46a7f80b510867ae14e3bb936d3dd8eed03f085d9f06f6','experiments/kbo-player-effects-20261103/v2/selection.json':'9fa9c300ce4e74c938eefcbdcb745a06fed3006f79fc59855f33fe557e0ff611','experiments/kbo-player-effects-20261103/v2/model.json':'a4602fecc037cbe5bc480a48b0c0c20bba4f5a7dc12262aae313732169b3568f','experiments/kbo-player-effects-20261103/v2/predictions.json':'6d3dd2cc15d4a4681335e1984c76e8526bf94aae9f46fe372d5b4c10b1abced5','experiments/kbo-player-effects-20261103/v2/results.json':'1b120334063c0ff535d3ff74c3ab0f55dc01a3c4bb878204b745b05f2c0d1b55'}
def main():
    with tempfile.TemporaryDirectory(prefix='v2-bytes-audit-',dir=HERE) as root:
        for mode in ('true','false'):
            clone=Path(root)/mode;subprocess.run(['git','clone','--quiet','--local','--shared','--no-checkout',str(WORKER),str(clone)],check=True)
            subprocess.run(['git','-C',str(clone),'-c','core.autocrlf='+mode,'checkout',RESULT,'--','.gitattributes'],check=True)
            subprocess.run(['git','-C',str(clone),'-c','core.autocrlf='+mode,'checkout',RESULT,'--',*FILES],check=True)
            for path,expected in FILES.items():assert hashlib.sha256((clone/path).read_bytes()).hexdigest()==expected,(mode,path)
    print('PASS: corrected contract, selection, model, predictions and results under autocrlf=true and false')
if __name__=='__main__':main()
