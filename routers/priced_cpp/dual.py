"""Sparse per-sink duals and net bids from lpbound's alpha NPZ interface."""
from dataclasses import dataclass
import hashlib
from pathlib import Path

import numpy as np


@dataclass
class DualPrices:
    offsets: np.ndarray
    vertices: np.ndarray
    values: np.ndarray
    capacity: np.ndarray
    bids: dict
    sha256: str


def load(inst, path):
    path = Path(path)
    count = sum(len(n.sinks) for n in inst.nets)
    size = inst.width*inst.height*inst.layers
    with np.load(path,allow_pickle=False) as data:
        if "alpha" in data:
            alpha = data["alpha"]
            if alpha.shape != (count,size):
                raise ValueError("dual shape does not match instance sinks/grid")
            if not np.all(np.isfinite(alpha)) or np.any(alpha < 0):
                raise ValueError("dual prices must be finite and nonnegative")
            rows, vertices = np.nonzero(alpha)
            values = alpha[rows,vertices]
            offsets = np.r_[0,np.cumsum(np.bincount(rows,minlength=count))]
        else:
            offsets,vertices,values = (data[k].copy() for k in ("off","vert","val"))
    if (offsets.ndim != 1 or len(offsets) != count+1 or
            not np.issubdtype(offsets.dtype,np.integer) or offsets[0] != 0 or
            np.any(np.diff(offsets) < 0) or offsets[-1] != len(vertices)):
        raise ValueError("invalid dual sink offsets")
    if (vertices.ndim != 1 or values.ndim != 1 or len(vertices) != len(values) or
            not np.issubdtype(vertices.dtype,np.integer) or
            np.any(vertices < 0) or np.any(vertices >= size)):
        raise ValueError("invalid dual vertex indices")
    if not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError("dual prices must be finite and nonnegative")
    for a,b in zip(offsets[:-1],offsets[1:]):
        if np.any(np.diff(vertices[a:b]) <= 0):
            raise ValueError("dual vertices must be unique and sorted within each sink")
    capacity, bids, sink = np.zeros(size), {}, 0
    for net in inst.nets:
        start,end = offsets[sink],offsets[sink+len(net.sinks)]
        vv, inverse = np.unique(vertices[start:end],return_inverse=True)
        aa = np.bincount(inverse,weights=values[start:end],minlength=len(vv))
        bids[net.id] = vv,aa
        capacity[vv] = np.maximum(capacity[vv],aa)
        sink += len(net.sinks)
    return DualPrices(offsets,vertices,values,capacity,bids,
                      hashlib.sha256(path.read_bytes()).hexdigest())


def diagnose(inst, submission, dual):
    """Recompute the dual value and split a legal route's gap into slacks."""
    from controller import NativeSession
    from m3d.checker import check
    from route import Grid
    result = check(inst,submission)
    if not result.legal:
        raise ValueError("dual diagnosis requires legal routes")
    grid = Grid(inst)
    routes = {r.net:r for r in submission.routes}
    pins = {p.id:grid.vid(p.vertex()) for p in inst.pins}
    held = np.zeros(len(dual.capacity))
    all_used, rows, shortest_sum, sink_index = set(), [], 0., 0
    with NativeSession(inst) as worker:
        for net in inst.nets:
            adjacency = {}
            for a,b in routes[net.id].edges:
                u,v = grid.vid(a),grid.vid(b)
                adjacency.setdefault(u,[]).append(v)
                adjacency.setdefault(v,[]).append(u)
            driver = pins[net.driver]
            parent, distance, stack = {driver:None},{driver:0},[driver]
            for u in stack:
                for v in adjacency.get(u,[]):
                    if v not in parent:
                        parent[v] = u
                        distance[v] = distance[u]+(inst.via_delay if grid.coord(u)[2] != grid.coord(v)[2]
                                                  else inst.layer_delay[grid.coord(u)[2]])
                        stack.append(v)
            all_used.update(parent)
            path_slack = 0.
            for sink in net.sinks:
                a,b = dual.offsets[sink_index:sink_index+2]
                alpha = dict(zip(dual.vertices[a:b].tolist(),dual.values[a:b].tolist()))
                options = worker.request(net.id,prices=alpha,k=1,diversity=0,strategy="spt",
                                         pins=[net.driver,sink])
                if not options:
                    raise RuntimeError("dual sink is unreachable")
                shortest = options[0]["priced_cost"]
                shortest_sum += shortest
                priced = float(distance[pins[sink]])
                v = pins[sink]
                while v is not None:
                    value = alpha.get(v,0.)
                    priced += value; held[v] += value
                    v = parent[v]
                path_slack += priced-shortest
                sink_index += 1
            rows.append(dict(net=net.id,path_slack=path_slack,vertices=list(parent)))
    slack = dual.capacity-held
    if np.min(slack) < -1e-6 or any(r["path_slack"] < -1e-6 for r in rows):
        raise RuntimeError("negative dual slack")
    for row in rows:
        row["price_slack"] = float(slack[row.pop("vertices")].sum())
        row["priority"] = row["path_slack"]+row["price_slack"]
    value = shortest_sum-float(dual.capacity.sum())
    path_total, price_total = sum(r["path_slack"] for r in rows),float(slack.sum())
    if abs(result.total_delay-value-path_total-price_total) > 1e-5:
        raise RuntimeError("dual gap decomposition differs from official delay")
    return dict(total_delay=result.total_delay,dual_value=value,path_slack=path_total,
                price_slack=price_total,unused_price_slack=float(sum(slack[v] for v in range(len(slack)) if v not in all_used)),
                nets=sorted(rows,key=lambda r:-r["priority"]),dual_sha256=dual.sha256)


def main():
    import argparse,json
    from route import Instance,Submission
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite",type=Path,required=True)
    parser.add_argument("--route-dir",type=Path,required=True)
    parser.add_argument("--dual-dir",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--cases",nargs="+")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True,exist_ok=True)
    for case in json.loads((args.suite/"suite.json").read_text())["cases"]:
        if args.cases and case["name"] not in args.cases:
            continue
        inst = Instance.load(str(args.suite/case["instance_file"]))
        sub = Submission.load(str(args.route_dir/(inst.name+".sol.json")))
        result = diagnose(inst,sub,load(inst,args.dual_dir/(inst.name+".alpha.npz")))
        (args.out_dir/(inst.name+".json")).write_text(json.dumps(result,indent=2)+"\n")
        print(f"{inst.name}: delay={result['total_delay']} dual={result['dual_value']:.6f} "
              f"path_slack={result['path_slack']:.3f} price_slack={result['price_slack']:.3f}",flush=True)


if __name__ == "__main__":
    main()
