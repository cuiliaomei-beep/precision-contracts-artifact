"""Simplified SC remote access with local completion and explicit epoch order.

This event model does not implement the complete MPI memory model. Read data is
sampled and bound at the access event; completion is tracked independently.
"""
from ..core.ir import Program, MemoryRegion
from dataclasses import replace


class RMAInstantiation:
    @staticmethod
    def _owner(program, region, target):
        if target not in program.components: raise ValueError(f'Unknown memory owner {target}')
        current=program.initial_state.memory_regions.get(region)
        if current is None:
            program.initial_state=replace(program.initial_state,memory_regions={**program.initial_state.memory_regions,region:MemoryRegion(region,target,{})})
            return
        if current is not None:
            if current.owner not in ('',target): raise ValueError(f'Inconsistent owner for {region}: {current.owner}, {target}')
            program.initial_state=replace(program.initial_state,memory_regions={**program.initial_state.memory_regions,region:MemoryRegion(region,target,current.values)})
    @staticmethod
    def put(program,origin,target,region,var,value,**attrs):
        RMAInstantiation._owner(program,region,target)
        return program.add_remote_write(origin,region,var,value,target=target,**attrs)
    @staticmethod
    def get(program,origin,target,region,var,destination=None,**attrs):
        RMAInstantiation._owner(program,region,target)
        return program.add_remote_read(origin,region,var,destination=destination,target=target,**attrs)
    @staticmethod
    def fence(program,component): return program.add_sync(component,kind='fence')
    @staticmethod
    def wait(program,component): return program.add_sync(component,kind='wait')
    @staticmethod
    def synchronized_get(program,origin,target,region,var,destination=None):
        fence=RMAInstantiation.fence(program,origin)
        get=RMAInstantiation.get(program,origin,target,region,var,destination)
        return [fence,get]


class LitmusTestRMA:
    def __init__(self,name,description=''):
        self.name,self.description,self.program=name,description,None
        self.expected_observations={};self.expected_pending=[]
    def build(self): raise NotImplementedError
    def get_program(self):
        if self.program is None: self.program=self.build()
        return self.program


class RMA_01(LitmusTestRMA):
    def __init__(self):
        super().__init__('RMA-01','Write -> writer fence -> explicit coordinated epoch edge -> reader Get')
        self.expected_observations={'origin::read':{'values':[10],'may_tainted':True}}
    def build(self):
        p=Program(['target','origin']);p.initial_state=p.initial_state.update_memory('R','x',0,owner='target')
        RMAInstantiation.put(p,'target','target','R','x',10,tainted=True)
        fence=RMAInstantiation.fence(p,'target')
        get=RMAInstantiation.get(p,'origin','target','R','x',destination='read')
        p.initial_state=p.initial_state.add_constraint(fence.id,get.id)
        RMAInstantiation.wait(p,'origin');p.add_observe('origin','read')
        return p


class RMA_02(LitmusTestRMA):
    def __init__(self):
        super().__init__('RMA-02','Reader races with two program-ordered writes; initial value remains possible')
        self.expected_observations={'origin::read':{'values':[0,10,20],'may_tainted':True}}
    def build(self):
        p=Program(['target','origin']);p.initial_state=p.initial_state.update_memory('R','x',0,owner='target')
        RMAInstantiation.put(p,'target','target','R','x',10,tainted=True)
        RMAInstantiation.put(p,'target','target','R','x',20)
        RMAInstantiation.get(p,'origin','target','R','x',destination='read')
        RMAInstantiation.wait(p,'origin');p.add_observe('origin','read')
        return p


class RMA_03(LitmusTestRMA):
    def __init__(self):
        super().__init__('RMA-03','Read captures42 and own wait completes it before observation')
        self.expected_observations={'origin::read':{'values':[42],'may_tainted':False}}
    def build(self):
        p=Program(['origin','target']);p.initial_state=p.initial_state.update_memory('R','x',42,owner='target')
        RMAInstantiation.get(p,'origin','target','R','x',destination='read')
        RMAInstantiation.wait(p,'origin');p.add_observe('origin','read')
        return p


class RMA_04(LitmusTestRMA):
    def __init__(self):
        super().__init__('RMA-04','Read captures42 while completion remains pending (separate safety fact)')
        self.expected_observations={'origin::read':{'values':[42],'may_tainted':False}}
        self.expected_pending=['e0']
    def build(self):
        p=Program(['origin','target']);p.initial_state=p.initial_state.update_memory('R','x',42,owner='target')
        RMAInstantiation.get(p,'origin','target','R','x',destination='read');p.add_observe('origin','read')
        return p


class RMA_05(LitmusTestRMA):
    def __init__(self):
        super().__init__('RMA-05','A writes C-owned cell while B reads it, exposing logical A-to-B dependence')
        self.expected_observations={'b::read':{'values':[0,100],'may_tainted':True}}
    def build(self):
        p=Program(['a','b','c']);p.initial_state=p.initial_state.update_memory('R','x',0,owner='c')
        RMAInstantiation.put(p,'a','c','R','x',100,tainted=True)
        RMAInstantiation.get(p,'b','c','R','x',destination='read');RMAInstantiation.wait(p,'b');p.add_observe('b','read')
        return p


class RMA_06(LitmusTestRMA):
    def __init__(self):
        super().__init__('RMA-06','Same-origin ordered Puts then Get; last write30 must be read')
        self.expected_observations={'origin::read':{'values':[30],'may_tainted':False}}
    def build(self):
        p=Program(['origin','target']);p.initial_state=p.initial_state.update_memory('R','x',0,owner='target')
        for val in [10,20,30]: RMAInstantiation.put(p,'origin','target','R','x',val)
        RMAInstantiation.get(p,'origin','target','R','x',destination='read');RMAInstantiation.wait(p,'origin');p.add_observe('origin','read')
        return p


class RMA_07(LitmusTestRMA):
    def __init__(self):
        super().__init__('RMA-07','Explicitly coordinated read-transform-write chain through C-owned cells')
        self.expected_observations={'c::read':{'values':[20],'may_tainted':True}}
    def build(self):
        p=Program(['a','b','c']);p.initial_state=p.initial_state.update_memory('R','x',0,owner='c')
        p.initial_state=p.initial_state.update_memory('R','y',0,owner='c')
        write=RMAInstantiation.put(p,'a','c','R','x',10,tainted=True)
        read=RMAInstantiation.get(p,'b','c','R','x',destination='first')
        p.initial_state=p.initial_state.add_constraint(write.id,read.id)
        RMAInstantiation.wait(p,'b')
        p.add_local_assign('b','doubled',{'op':'mul','args':[{'var':'first'},2]})
        write2=RMAInstantiation.put(p,'b','c','R','y',{'var':'doubled'})
        read2=RMAInstantiation.get(p,'c','c','R','y',destination='read')
        p.initial_state=p.initial_state.add_constraint(write2.id,read2.id)
        RMAInstantiation.wait(p,'c');p.add_observe('c','read')
        return p
