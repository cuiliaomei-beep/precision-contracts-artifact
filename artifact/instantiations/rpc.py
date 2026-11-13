"""Synchronous unary RPC: explicit matched request and response payloads."""
from ..core.ir import Program

_NO_RETURN = object()


class UnaryRPCInstantiation:
    @staticmethod
    def rpc_call(program, caller, callee, func_name, args=None, returns=None, return_expr=_NO_RETURN):
        args = {} if args is None else dict(args)
        call_id = f'rpc{program.event_counter}_{caller}_{callee}_{func_name}'
        if return_expr is _NO_RETURN:
            if not args: raise ValueError('No-argument RPC requires an explicit return_expr')
            return_expr = {'var': next(iter(args))}
        result_variable = f'_return_{call_id}'
        destination = returns or f'_rpc_result_{call_id}'
        request_id, response_id = call_id + ':request', call_id + ':response'
        send_req = program.add_send(caller, callee, 'REQ_' + func_name, data=args, message_id=request_id, call_id=call_id)
        recv_req = program.add_receive(callee, caller, 'REQ_' + func_name, message_id=request_id,
                                       bindings={name: name for name in args}, call_id=call_id)
        local_exec = program.add_local_assign(callee, result_variable, return_expr)
        local_exec.attributes['call_id'] = call_id
        send_resp = program.add_send(callee, caller, 'RESP_' + func_name,
                                     data={'result': {'var': result_variable}}, message_id=response_id, call_id=call_id)
        recv_resp = program.add_receive(caller, callee, 'RESP_' + func_name, message_id=response_id,
                                       bindings={'result': destination}, call_id=call_id)
        events = [send_req, recv_req, local_exec, send_resp, recv_resp]
        for left, right in zip(events, events[1:]):
            program.initial_state = program.initial_state.add_constraint(left.id, right.id)
        return events

    @staticmethod
    def simple_rpc(program, caller, callee, arg_var):
        return UnaryRPCInstantiation.rpc_call(program, caller, callee, 'call',
                                              args={'argument': {'var': arg_var}}, returns=arg_var)[-1]


class LitmusTest:
    def __init__(self, name, description=''):
        self.name, self.description, self.program = name, description, None
        self.expected_observations = {}
        self.expected_pending = []
    def build(self): raise NotImplementedError
    def get_program(self):
        if self.program is None: self.program = self.build()
        return self.program


class RPC_01(LitmusTest):
    def __init__(self):
        super().__init__('RPC-01', 'Tainted actual argument reaches callee and actual echo response')
        self.expected_observations = {'target::x': {'values':[10], 'may_tainted':True}, 'origin::result': {'values':[10], 'may_tainted':True}}
    def build(self):
        p=Program(['origin','target'])
        p.add_local_assign('origin','x',10,tainted=True)
        UnaryRPCInstantiation.rpc_call(p,'origin','target','process',args={'x':{'var':'x'}},returns='result')
        p.add_observe('target','x');p.add_observe('origin','result')
        return p


class RPC_02(LitmusTest):
    def __init__(self):
        super().__init__('RPC-02', 'Two actual parameters retain distinct clean/tainted provenance')
        self.expected_observations = {'target::x': {'values':[10], 'may_tainted':False}, 'target::y': {'values':[20], 'may_tainted':True}, 'origin::result': {'values':[20], 'may_tainted':True}}
    def build(self):
        p=Program(['origin','target'])
        p.add_local_assign('origin','x',10);p.add_local_assign('origin','y',20,tainted=True)
        UnaryRPCInstantiation.rpc_call(p,'origin','target','process',args={'x':{'var':'x'},'y':{'var':'y'}},returns='result',return_expr={'var':'y'})
        p.add_observe('target','x');p.add_observe('target','y');p.add_observe('origin','result')
        return p


class RPC_03(LitmusTest):
    def __init__(self):
        super().__init__('RPC-03', 'Explicit no-argument server return binds actual caller destination')
        self.expected_observations = {'client::received': {'values':[42], 'may_tainted':False}}
    def build(self):
        p=Program(['client','server'])
        p.add_local_assign('server','x',42)
        UnaryRPCInstantiation.rpc_call(p,'client','server','get_value',returns='received',return_expr={'var':'x'})
        p.add_observe('client','received')
        return p


class RPC_04(LitmusTest):
    def __init__(self):
        super().__init__('RPC-04', 'Two-hop actual payload A to B to C')
        self.expected_observations = {'c::x': {'values':[10], 'may_tainted':True}}
    def build(self):
        p=Program(['a','b','c']);p.add_local_assign('a','x',10,tainted=True)
        UnaryRPCInstantiation.rpc_call(p,'a','b','forward',args={'x':{'var':'x'}},returns='response')
        UnaryRPCInstantiation.rpc_call(p,'b','c','process',args={'x':{'var':'x'}},returns='response')
        p.add_observe('c','x')
        return p


class RPC_05(LitmusTest):
    def __init__(self):
        super().__init__('RPC-05', 'Actual RPC return selects guarded branch; explicit-flow taint only')
        self.expected_observations = {'client::result': {'values':[10], 'may_tainted':True}, 'client::z': {'values':[999], 'may_tainted':False}}
    def build(self):
        p=Program(['client','server']);p.add_local_assign('server','result',10,tainted=True)
        UnaryRPCInstantiation.rpc_call(p,'client','server','get_result',returns='result',return_expr={'var':'result'})
        p.add_branch('client',{'op':'gt','args':[{'var':'result'},5]},decision='return_gt_5')
        p.add_local_assign('client','z',999,guard={'decision':'return_gt_5','value':True})
        p.add_local_assign('client','z',111,guard={'decision':'return_gt_5','value':False})
        p.add_observe('client','result');p.add_observe('client','z')
        return p
