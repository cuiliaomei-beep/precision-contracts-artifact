"""Generic finite relational collecting interpreter over extracted structured Jimple.

This is a restricted reference frontend, not a general JVM verifier. Decisions,
calls, throws, returns and catch/finally flow come from actual classfile IR.
Only explicit heap/library contracts below are trusted; unknown calls fail closed.
No expected Outcome relation or runtime answer is an input to this module.
"""
from __future__ import annotations
from pathlib import Path
import argparse,copy,json,hashlib

HERE=Path(__file__).resolve().parent
NN='org.apache.hadoop.hdfs.server.namenode.'
ENTRY=f'<{NN}NameNodeRpcServer: void fsync(java.lang.String,long,java.lang.String,long)>'
def ref(ident):return {'ref':ident}
def fq(cls,typ,name):return f'<{NN}{cls}: {typ} {name}>'
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def canon(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def sha(x):return hashlib.sha256(canon(x).encode()).hexdigest()

class Unsupported(RuntimeError):pass
class JavaThrow(Exception):
    def __init__(self,obj):self.obj=obj

def make_heap(fixture,owners=None):
    owners=owners or [fixture['owner_a'],fixture['owner_b']]
    heap={
      'rpc':{'type':NN+'NameNodeRpcServer',fq('NameNodeRpcServer',NN+'FSNamesystem','namesystem'):ref('ns')},
      'ns':{'type':NN+'FSNamesystem',fq('FSNamesystem',NN+'FSDirectory','dir'):ref('dir')},
      'dir':{'type':NN+'FSDirectory',fq('FSDirectory',NN+'INodeMap','inodeMap'):ref('inodeMap')},
      'inodeMap':{'type':NN+'INodeMap','table':{}},
      'logger':{'type':'org.slf4j.Logger'},'pc':{'type':NN+'FSPermissionChecker'},
      'editLog':{'type':NN+'FSEditLog'},
      'root':{'type':NN+'INodeDirectory','id':1,'parent':None,'is_root':True,'children':{}},
      'parent':{'type':NN+'INodeDirectory','id':2,'parent':ref('root'),'is_root':False,'name':'audit-parent','children':{}},
    }
    heap['root']['children']['audit-parent']=ref('parent')
    for i,suffix in enumerate(['a','b']):
        ident='inode_'+suffix;feature='uc_'+suffix;path=fixture['file_'+suffix];fileid=fixture['file_id_'+suffix]
        heap['inodeMap']['table'][str(fileid)]=ref(ident)
        heap[ident]={'type':NN+'INodeFile','id':fileid,'name':path.rsplit('/',1)[-1],
            'path':path,'parent':ref('root'),'features':{NN+'FileUnderConstructionFeature':ref(feature)},'snapshot':False}
        heap['root']['children'][heap[ident]['name']]=ref(ident)
        heap[feature]={'type':NN+'FileUnderConstructionFeature',fq('FileUnderConstructionFeature','java.lang.String','clientName'):owners[i]}
    return heap

class Interpreter:
    def __init__(self,ir,heap,step_budget=10000):
        self.methods={m['signature']:m for m in ir['methods']};self.heap=copy.deepcopy(heap)
        self.trace=[];self.contract_calls=[];self.steps=0;self.budget=step_budget;self.allocations=0;self.persisted=[]
    def object(self,obj):
        if not isinstance(obj,dict) or set(obj)!={'ref'} or obj['ref'] not in self.heap:raise Unsupported(f'Invalid/unknown object reference {obj}')
        return self.heap[obj['ref']]
    def allocate(self,typ,**fields):
        self.allocations+=1;key='alloc_'+str(self.allocations);self.heap[key]={'type':typ,**fields};return ref(key)
    def before_step(self,m,env,pc):
        """Observation/projection extension point; default reference retains all locals."""
        pass
    def expr(self,e,env,args,this,caught):
        op=e['op']
        if op=='local':
            if e['name'] not in env:raise Unsupported('Undefined IR local '+e['name'])
            return env[e['name']]
        if op=='const':return e['value']
        if op=='parameter':return args[e['index']]
        if op=='this':return this
        if op=='caught_exception':return caught
        if op=='class_literal':return e['name'].removeprefix('L').removesuffix(';')
        if op=='new':return self.allocate(e['type'])
        if op=='cast':return self.expr(e['value'],env,args,this,caught)
        if op=='field':
            obj=self.object(self.expr(e['base'],env,args,this,caught))
            if e['signature'] not in obj:raise Unsupported('Absent concrete heap field '+e['signature'])
            return obj[e['signature']]
        if op=='static_field':
            sig=e['signature']
            if sig.endswith(' boolean $assertionsDisabled>'):return 1
            if sig==fq('NameNode','org.slf4j.Logger','stateChangeLog'):return ref('logger')
            if 'OperationCategory WRITE>' in sig:return 'WRITE'
            if 'FSDirectory$DirOp WRITE>' in sig:return 'WRITE'
            raise Unsupported('Uncontracted static field '+sig)
        if op=='binary':
            a=self.expr(e['left'],env,args,this,caught);b=self.expr(e['right'],env,args,this,caught);operator=e['operator']
            if operator=='==':return int(a==b)
            if operator=='!=':return int(a!=b)
            if operator=='cmp':return (a>b)-(a<b)
            if operator=='<':return int(a<b)
            if operator=='<=':return int(a<=b)
            if operator=='>':return int(a>b)
            if operator=='>=':return int(a>=b)
            if operator=='+':return a+b
            if operator=='-':return a-b
            raise Unsupported('Unsupported binary operator '+operator)
        if op=='invoke':
            base=self.expr(e['base'],env,args,this,caught) if e['base'] is not None else None
            values=[self.expr(v,env,args,this,caught) for v in e['args']]
            return self.call(e['signature'],base,values)
        if op=='length':return len(self.expr(e['value'],env,args,this,caught))
        if op=='array_read':return self.expr(e['base'],env,args,this,caught)[self.expr(e['index'],env,args,this,caught)]
        if op=='new_array':return [None]*self.expr(e['size'],env,args,this,caught)
        raise Unsupported('Unsupported expression '+str(e))
    def call(self,sig,this,args):
        if sig in self.methods:
            if 'FSDirWriteFileOp: void persistBlocks(' in sig:self.persisted.append(dict(inode=args[2],path=args[1],call='extracted persistBlocks entry'))
            return self.method(self.methods[sig],this,args)
        self.contract_calls.append(dict(signature=sig,base=this,args=args))
        owner=sig[1:sig.index(':')];name=sig.split(' ',2)[2].split('(')[0]
        if owner=='java.lang.StringBuilder':
            obj=self.object(this)
            if name=='<init>':obj['parts']=[] if not args else [args[0]];return None
            if name=='append':obj.setdefault('parts',[]).append(args[0]);return this
            if name=='toString':return ''.join('null' if x is None else str(x) for x in obj.get('parts',[]))
        if owner=='java.lang.String' and name=='equals':return int(isinstance(args[0],str) and this==args[0])
        if name=='<init>' and (owner.endswith('Exception') or owner.endswith('Error')):
            self.object(this)['message']=args[0] if args else '';return None
        if owner=='org.slf4j.Logger':
            if name in ['info','debug']:return None
            if name=='isDebugEnabled':return 0
        if owner=='org.apache.hadoop.util.Preconditions' or owner=='com.google.common.base.Preconditions' or owner.endswith('.Preconditions'):
            if name in ['checkArgument','checkState']:
                if not args[0]:raise JavaThrow(self.allocate('java.lang.IllegalArgumentException'))
                return None
        if owner==NN+'INodeMap' and name=='get':return self.object(this)['table'].get(str(args[0]))
        if owner==NN+'INodesInPath':
            if name=='fromINode':
                inode=self.object(args[0]);return self.allocate(owner,inode=args[0],path=inode['path'])
            if name=='fromComponents':return self.allocate(owner,inode=None,path='/'.join(args[0]))
            if name=='getPath':return self.object(this)['path']
            if name=='getINode' and args==[-1]:return self.object(this)['inode']
        if owner.startswith(NN+'INode'):
            if name=='getPathComponents':return args[0].split('/')
            obj=self.object(this)
            if name=='getFeature':return obj.get('features',{}).get(args[0])
            if name=='isFile':return int(obj['type']==NN+'INodeFile')
            if name=='asFile':
                if obj['type']!=NN+'INodeFile':raise Unsupported('asFile precondition violated')
                return this
            if name=='getId':return obj['id']
            if name=='getParent':return obj['parent']
            if name=='getLocalNameBytes':return obj['name']
            if name=='getChild':
                if args[1]!=2147483646:raise Unsupported('Snapshot version outside extracted CURRENT_STATE_ID=2147483646')
                return obj['children'].get(args[0])
            if name=='isRoot':return int(obj['is_root'])
            if name=='equals':return int(self.object(args[0])['id']==obj['id'])
            if name=='isWithSnapshot':
                if obj.get('snapshot',False):raise Unsupported('Snapshot inode outside declared heap contract')
                return 0
        if owner==NN+'NameNodeRpcServer' and name=='checkNNStartup':return None
        if owner==NN+'FSNamesystem':
            if name in ['checkOperation','checkNameNodeSafeMode','writeLock','writeUnlock']:return None
            if name=='getPermissionChecker':return ref('pc')
            if name=='getEditLog':return ref('editLog')
            if name in ['hasReadLock','hasWriteLock']:return 1
            if name=='leaseExceptionString':return '<diagnostic text outside query; total/pure in contract>'
        if owner==NN+'FSDirectory':
            if name=='getFSNamesystem':return ref('ns')
            if name=='getEditLog':return ref('editLog')
        if owner==NN+'FSPermissionChecker' and name=='setOperationType':return None
        if owner==NN+'FSEditLog' and name in ['logUpdateBlocks','logSync']:return None
        raise Unsupported('Uncontracted invocation '+sig)
    def method(self,m,this,args):
        env={};pc=0;caught=None;units=m['units'];last_pc=-1
        while 0<=pc<len(units):
            self.steps+=1
            if self.steps>self.budget:raise Unsupported('Finite interpreter step budget exhausted')
            u=units[pc]
            self.before_step(m,env,pc)
            record=dict(method=m['signature'],unit=pc,line=u['line'],kind=u['kind'],backedge=pc<=last_pc)
            self.trace.append(record);last_pc=pc;next_pc=pc+1
            try:
                if u['kind'] in ['identity','assign']:
                    value=self.expr(u['right'],env,args,this,caught)
                    if u['left']['op']!='local':raise Unsupported('Heap assignment not supported by current extracted slice')
                    env[u['left']['name']]=value
                elif u['kind']=='invoke':self.expr(u['value'],env,args,this,caught)
                elif u['kind']=='if':
                    condition=self.expr(u['condition'],env,args,this,caught);record['condition_result']=bool(condition)
                    if condition:next_pc=u['target']
                elif u['kind']=='goto':next_pc=u['target']
                elif u['kind']=='return':return self.expr(u['value'],env,args,this,caught)
                elif u['kind']=='return_void':return None
                elif u['kind']=='throw':raise JavaThrow(self.expr(u['value'],env,args,this,caught))
                elif u['kind']!='nop':raise Unsupported('Unsupported unit '+str(u))
            except JavaThrow as error:
                typ=self.object(error.obj)['type'];record['propagating_exception']=typ
                handlers=[t for t in m['traps'] if t['begin']<=pc<t['end_exclusive'] and t['exception'] in ['java.lang.Throwable',typ]]
                if not handlers:raise
                caught=error.obj;next_pc=handlers[0]['handler'];record['caught_by_unit']=next_pc
            pc=next_pc
        raise Unsupported('Method fell off extracted CFG '+m['signature'])

def predict(ir,input_data,interpreter_factory=None):
    output=[]
    for family in input_data['families']:
        for request in family['requests']:
            vm=(interpreter_factory or Interpreter)(ir,family['heap']);error=None;exception=None;status='unknown'
            try:
                if request['holder'] is None or request['file_id']==0 or request.get('last_block_length',-1)>0:
                    raise Unsupported('Input outside declared nonnull-holder, non-grandfather-id, nonpositive-lastBlockLength contract')
                vm.call(ENTRY,ref('rpc'),[request['path'],request['file_id'],request['holder'],request.get('last_block_length',-1)])
                status='normal'
            except JavaThrow as thrown:
                exception=vm.object(thrown.obj)['type']
                status='leaseError' if exception.endswith('.LeaseExpiredException') else 'notFound' if exception=='java.io.FileNotFoundException' else 'otherException'
            except (Unsupported,KeyError,TypeError,ValueError) as unsupported:error=f'{type(unsupported).__name__}: {unsupported}'
            output.append(dict(family=family['id'],request_ctx=request['id'],file_id=request['file_id'],holder=request['holder'],path=request['path'],
                status=status,exception_class=exception,unsupported=error,reached_persistBlocks=bool(vm.persisted),
                persisted_objects=vm.persisted,steps=vm.steps,trace=vm.trace,contract_calls=vm.contract_calls))
    return dict(schema='finite-source-derived-hdfs-query-v1',input_sha256=sha(input_data),ir_sha256=sha(ir),
        query_schema=['family','request_ctx','file_id','holder','status','exception_class','reached_persistBlocks'],
        query_relation=[[r[k] for k in ['family','request_ctx','file_id','holder','status','exception_class','reached_persistBlocks']] for r in output],
        results=output,unknown_count=sum(r['status']=='unknown' for r in output),
        scope='Finite collecting reference over actual extracted Java CFG, relative to explicit finite heap/library/protocol contracts; not general JVM/whole-HDFS soundness')

def main():
    p=argparse.ArgumentParser();p.add_argument('--ir',type=Path,default=HERE/'extracted_original.json');p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=predict(json.loads(a.ir.read_text()),json.loads(a.input.read_text()));dump(a.output,result)
    print(json.dumps({'rows':len(result['results']),'unknown':result['unknown_count'],'outcomes':[{k:r[k] for k in ['family','request_ctx','status','exception_class','reached_persistBlocks','unsupported']} for r in result['results']]},indent=2))
    return 0 if result['unknown_count']==0 else 2
if __name__=='__main__':raise SystemExit(main())
