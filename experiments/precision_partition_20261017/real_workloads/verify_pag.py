"""Ordinary four-rule least-fixed-point recomputation; no partition heuristic."""
from pathlib import Path
import argparse,json,time,hashlib

def solve(d):
    objects=[x['id'] for x in d['allocations']];oi={x:i for i,x in enumerate(objects)}
    def bits(xs):
        r=0
        for x in xs:r|=1<<oi[x]
        return r
    def members(b):
        while b:
            low=b&-b;yield objects[low.bit_length()-1];b^=low
    mask={t:bits(xs) for t,xs in d['allowed_by_type'].items()}
    pts={x['id']:0 for x in d['nodes']};vmask={x['id']:mask[str(x['type']) if x['type'] is not None else 'null'] for x in d['nodes']}
    heap={};rounds=0;transfers=0
    for obj,var in d['alloc']:pts[var]|=(1<<oi[obj])&vmask[var]
    changed=True
    while changed:
        changed=False;rounds+=1
        for src,dst in d['assign']:
            transfers+=1;new=pts[dst]|(pts[src]&vmask[dst])
            if new!=pts[dst]:pts[dst]=new;changed=True
        for src,base,field in d['store']:
            for obj in members(pts[base]):
                transfers+=1;key=(obj,field);old=heap.get(key,0);new=old|pts[src]
                if new!=old:heap[key]=new;changed=True
        for base,field,dst in d['load']:
            add=0
            for obj in members(pts[base]):transfers+=1;add|=heap.get((obj,field),0)
            new=pts[dst]|(add&vmask[dst])
            if new!=pts[dst]:pts[dst]=new;changed=True
    var_result={v:sorted(members(b)) for v,b in pts.items()}
    heap_result={(a,f):sorted(members(b)) for (a,f),b in heap.items() if b}
    return var_result,heap_result,rounds,transfers

def main():
    a=argparse.ArgumentParser();a.add_argument('pag',type=Path);args=a.parse_args()
    d=json.loads(args.pag.read_text());t=time.perf_counter();v,h,rounds,transfers=solve(d);elapsed=time.perf_counter()-t
    expected=d['spark_variable_points_to']
    hd={(x['base'],x['field']):x['points_to'] for x in d['spark_heap_points_to'] if x['points_to']}
    mismatches=[{'variable':k,'expected':expected[k],'actual':v[k]} for k in expected if sorted(expected[k])!=v[k]]
    hm=[{'base':k[0],'field':k[1],'expected':hd.get(k,[]),'actual':h.get(k,[])} for k in set(hd)|set(h) if sorted(hd.get(k,[]))!=h.get(k,[])]
    result={'success':not mismatches and not hm,'variable_count':len(v),'allocation_count':len(d['allocations']),
      'variable_fact_count':sum(map(len,v.values())),'nonempty_heap_slots':len(h),'heap_fact_count':sum(map(len,h.values())),
      'rounds':rounds,'transfer_visits':transfers,'elapsed_seconds':elapsed,
      'variable_mismatches':mismatches,'heap_mismatches':hm,
      'pag_sha256':hashlib.sha256(args.pag.read_bytes()).hexdigest(),
      'solver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
      'scope':'Complete exported four-rule PAG least fixed point from allocation seeds; no final-points-to input is used in solve.'}
    (args.pag.parent/'recomputed_points_to.json').write_text(json.dumps({'variables':v,'heap':[{'base':a,'field':f,'points_to':xs} for (a,f),xs in sorted(h.items())]},separators=(',',':')))
    (args.pag.parent/'verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:val for k,val in result.items() if not k.endswith('mismatches')}))
    if mismatches:print('variable mismatch sample',mismatches[:2])
    if hm:print('heap mismatch sample',hm[:2])
    raise SystemExit(0 if result['success'] else 1)
if __name__=='__main__':main()
