"""The five original placement heuristics used for the RPC study."""
import heapq
import numpy as np

def _lpt(sizes, W):
    order = np.argsort(-sizes, kind="stable")
    heap = [(0, w) for w in range(W)]; heapq.heapify(heap)
    out = np.empty(len(sizes), np.int32)
    for u in order:
        load, w = heapq.heappop(heap); out[u] = w
        heapq.heappush(heap, (load + int(sizes[u]), w))
    return out


def part_single(g, W, seed):
    return np.zeros(g["n"], np.int32)


def part_component(g, W, seed):
    sizes = np.bincount(g["comp"], minlength=g["C"])
    return _lpt(sizes, W)[g["comp"]]


def part_random(g, W, seed):
    rng = np.random.default_rng(10_000 + seed)
    perm = rng.permutation(g["n"])
    p = np.empty(g["n"], np.int32); p[perm] = np.arange(g["n"]) % W
    return p


def _scc_labels(g):
    if "_scc" not in g:
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import connected_components
        A = csr_matrix((np.ones(len(g["src"]), np.int8), (g["src"], g["dst"])), shape=(g["n"], g["n"]))
        _, labels = connected_components(A, directed=True, connection="strong")
        g["_scc"] = labels.astype(np.int32)
    return g["_scc"]


def part_scc(g, W, seed):
    """Keep every SCC inside one partition; fill partitions contiguously in
    node order (nodes are component-contiguous) so locality is preserved."""
    labels = _scc_labels(g)
    n = g["n"]
    first = np.full(labels.max() + 1, n, np.int64)
    np.minimum.at(first, labels, np.arange(n))
    sizes = np.bincount(labels)
    order = np.argsort(first, kind="stable")
    cum = np.cumsum(sizes[order])
    target = n / W
    unit_part = np.empty(len(sizes), np.int32)
    unit_part[order] = np.minimum(((cum - sizes[order]) // target).astype(np.int32), W - 1)
    return unit_part[labels]


def _undirected_csr(g):
    if "_und" not in g:
        n = g["n"]
        us = np.concatenate([g["src"], g["dst"]]); ud = np.concatenate([g["dst"], g["src"]])
        o = np.argsort(us, kind="stable")
        us, ud = us[o], ud[o]
        indptr = np.searchsorted(us, np.arange(n + 1))
        g["_und"] = (indptr, ud)
    return g["_und"]


def part_comm_aware(g, W, seed):
    """Component (LPT) init, then 2 passes of capacity-constrained
    neighbourhood-majority refinement (Linear Deterministic Greedy style).
    A node moves only if the move strictly reduces its own cut degree and the
    target partition is below capacity, so the total cut never increases."""
    p = part_component(g, W, seed).copy()
    if W == 1:
        return p
    n = g["n"]
    indptr, nbr = _undirected_csr(g)
    cap = int(np.ceil(n / W * 1.03))
    load = np.bincount(p, minlength=W).astype(np.int64)
    rng = np.random.default_rng(20_000 + seed)

    # Phase 1 - balancing: over-full partitions evict their cheapest boundary
    # nodes (largest gain = neighbours in target - neighbours at home).
    M = np.zeros((n, W), np.int32)
    us = np.repeat(np.arange(n), np.diff(indptr))
    np.add.at(M, (us, p[nbr]), 1)
    for P in np.argsort(-load):
        excess = int(load[P] - cap)
        if excess <= 0:
            continue
        nodes = np.flatnonzero(p == P)
        Mn = M[nodes].copy()
        Mn[:, load >= cap] = -1
        best = Mn.argmax(axis=1)
        gain = Mn[np.arange(len(nodes)), best] - M[nodes, P]
        for i in np.argsort(-gain):
            if excess == 0:
                break
            t = int(best[i])
            if load[t] >= cap:
                Mn_i = M[nodes[i]].copy(); Mn_i[load >= cap] = -1; t = int(np.argmax(Mn_i))
                if Mn_i[t] < 0:
                    continue
            v = nodes[i]
            p[v] = t; load[P] -= 1; load[t] += 1; excess -= 1
            np.add.at(M, (nbr[indptr[v]:indptr[v + 1]], t), 1)
            np.add.at(M, (nbr[indptr[v]:indptr[v + 1]], P), -1)
    del M

    # Phase 2 - refinement: strict cut-reducing moves within capacity.
    for _ in range(3):
        for v in rng.permutation(n):
            a, b = indptr[v], indptr[v + 1]
            if b - a == 0:
                continue
            cur = p[v]
            cnt = np.bincount(p[nbr[a:b]], minlength=W)
            cur_cnt = int(cnt[cur])
            cnt[load >= cap] = -1
            best = int(np.argmax(cnt))
            if best != cur and cnt[best] > cur_cnt:
                load[cur] -= 1; load[best] += 1; p[v] = best
    return p


PARTITIONERS = {
    "Single": part_single, "Component": part_component, "Random": part_random,
    "SCC-aware": part_scc, "Communication-aware": part_comm_aware,
}

