from pathlib import Path
import subprocess
import sys
root=Path(__file__).resolve().parents[1]
for script in ("src/lotto_quantum_million.py","scripts/verify_lotto_quantum_million.py"):
    subprocess.run([sys.executable,"-u",str(root/script)],cwd=root,check=True)
print("COMPLETED_AND_VERIFIED",flush=True)
