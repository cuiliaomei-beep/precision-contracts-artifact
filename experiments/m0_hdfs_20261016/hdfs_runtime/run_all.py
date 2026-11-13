"""Reproduce original, holder-check variant and both server-off negative checks."""
from pathlib import Path
import json
import subprocess
import sys
import time

BASE=Path(__file__).resolve().parent

def main():
    batch=BASE/'execution_logs'/time.strftime('%Y%m%dT%H%M%S',time.gmtime())
    batch.mkdir(parents=True,exist_ok=False)
    stages=[];selected={}
    commands=[('original',[sys.executable,str(BASE/'run_runtime.py')]),
              ('build_mutant',[sys.executable,str(BASE/'build_mutant.py')]),
              ('mutant',[sys.executable,str(BASE/'run_runtime.py'),'--mutant-dir',str(BASE/'mutation/classes')])]
    for name,cmd in commands:
        print('Running',name,flush=True)
        with (batch/(name+'.log')).open('w',encoding='utf-8') as log:
            process=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
        stages.append({'stage':name,'command':cmd,'exit_code':process.returncode,'log':str(batch/(name+'.log'))})
        (batch/'stages.json').write_text(json.dumps(stages,indent=2)+'\n',encoding='utf-8')
        if process.returncode:raise SystemExit(process.returncode)
        if name in ('original','mutant'):
            result=json.loads((BASE/'runtime_result_latest.json').read_text())
            selected[name]=Path(result['folder'])/'run_record.json'
    from summarize import summarize
    summarize(selected['original'],selected['mutant'])

if __name__=='__main__':main()
