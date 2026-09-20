"""Read a .kicad_pcb back and hold it to the spec. Independent of KiCad's own DRC: this checks that the board
that got routed is the board that was specified. Parser is a plain s-expression reader (no KiCad needed).
Reads boards saved by KiCad 9 through 10.99 (net names with or without numeric codes) as well as its own synthetic ones."""
import math, re
from .units import *

def sexpr(text):
    tok=re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+',text); st=[[]]
    for t in tok:
        if t=="(": st.append([])
        elif t==")": x=st.pop(); st[-1].append(x)
        else: st[-1].append(t[1:-1] if t.startswith('"') else t)
    return st[0][0]
def _f(node,key): return next((x for x in node[1:] if isinstance(x,list) and x and x[0]==key),None)
def _all(node,key): return [x for x in node[1:] if isinstance(x,list) and x and x[0]==key]

class Board:
    def __init__(s,text):
        t=sexpr(text); s.nets={n[1]:n[2] for n in _all(t,"net") if len(n)>2}   # code table: KiCad <=10 only
        def net(g):                              # (net 5 "X") and (net 5) up to KiCad 10; (net "X") from 11 on
            n=_f(g,"net"); name=None if not n else n[2] if len(n)>2 else s.nets.get(n[1],n[1])
            return None if name and name.startswith("unconnected-(") else name     # KiCad's name for a lone pin: no net
        s.cu=[l[1] for l in _f(t,"layers")[1:] if isinstance(l,list) and l[1].endswith(".Cu")] if _f(t,"layers") else []
        s.segs,s.vias,s.zones,s.pads={}, {}, set(), {}
        for g in _all(t,"segment"):
            a,b=_f(g,"start"),_f(g,"end"); L=math.dist((float(a[1]),float(a[2])),(float(b[1]),float(b[2])))
            s.segs.setdefault(net(g),[]).append((mm(L),mm(float(_f(g,"width")[1])),_f(g,"layer")[1]))
        for g in _all(t,"via"): n=net(g); s.vias[n]=s.vias.get(n,0)+1
        for g in _all(t,"zone"): nm=_f(g,"net_name"); s.zones.add(nm[1] if nm else net(g))
        for fp in _all(t,"footprint"):
            ref=next(p[2] for p in _all(fp,"property") if p[1]=="Reference")
            for pad in _all(fp,"pad"):
                s.pads[(ref,pad[1])]=net(pad)
    def length(s,net): return sum((x[0] for x in s.segs.get(net,[])),mm(0))

def verify_layout(c,board,rails,pairs,fab,stub_min=mm(0.25)):
    E=[]
    if len(board.cu)!=fab.layers: E.append(f"layout has {len(board.cu)} copper layers, fab spec says {fab.layers}")
    for part in c.parts.values():                               # netlist equivalence, pad by pad (DNP parts still have footprints)
        for p in part.pins:
            want=p.net.name if p.net else None; got=board.pads.get((part.ref,p.num),"<no pad>")
            if got!=want: E.append(f"layout pad {part.ref}.{p.num} is on {got}, spec says {want}")
    for r in rails:
        if r.plane:
            if r.net not in board.zones: E.append(f"rail {r.net} carries {r.I}: needs a zone/plane, none found")
            thin=[w for _,w,_ in board.segs.get(r.net,[]) if w<stub_min]
        else: thin=[w for _,w,_ in board.segs.get(r.net,[]) if w<r.width and r.net not in board.zones]
        if thin: E.append(f"rail {r.net}: {len(thin)} segment(s) down to {min(thin)}, need {stub_min if r.plane else r.width}")
    for pr in pairs:
        lp,ln=board.length(pr.p),board.length(pr.n)
        if pr.p not in board.segs or pr.n not in board.segs: E.append(f"pair {pr.name}: not routed"); continue
        if abs(lp-ln)>fab.skew: E.append(f"pair {pr.name}: skew {abs(lp-ln)} exceeds {fab.skew} (P {lp}, N {ln})")
        vp,vn=board.vias.get(pr.p,0),board.vias.get(pr.n,0)
        if vp!=vn: E.append(f"pair {pr.name}: via count differs P={vp} N={vn}")
        if max(vp,vn)>pr.max_vias: E.append(f"pair {pr.name}: {max(vp,vn)} vias, limit {pr.max_vias}")
        for net in (pr.p,pr.n):
            bad={l for _,_,l in board.segs[net] if l not in pr.layers}
            if bad: E.append(f"pair {pr.name}: {net} routed on {sorted(bad)}, allowed {list(pr.layers)}")
            if pr.geom and [w for _,w,_ in board.segs[net] if abs(w-pr.geom[0])>um(1)]: E.append(f"pair {pr.name}: {net} width differs from fab geometry {pr.geom[0]}")
    return E

def synthetic_pcb(c,rails,pairs,fab,pair_len=mm(30),hs_width=mm(0.15)):
    """A stand-in layout that satisfies the spec, so the verifier (and its mutants) can be exercised without KiCad."""
    ids={n.name:i+1 for i,n in enumerate(c.nets.values())}
    cu=["F.Cu"]+[f"In{i}.Cu" for i in range(1,fab.layers-1)]+["B.Cu"]
    o=['(kicad_pcb (version 20240108) (generator "netspec-synthetic")','  (layers '+" ".join(f'({i} "{l}" signal)' for i,l in enumerate(cu))+")"]
    o+=[f'  (net {i} "{n}")' for n,i in ids.items()]
    for part in c.parts.values():
        pads="".join(f'\n    (pad "{p.num}" smd rect (at 0 0) (size 1 1) (layers "F.Cu")'+(f' (net {ids[p.net.name]} "{p.net.name}")' if p.net else "")+")" for p in part.pins)
        o.append(f'  (footprint "x:{part.ref}" (layer "F.Cu") (at 0 0)\n    (property "Reference" "{part.ref}" (at 0 0))'+pads+")")
    seg=lambda net,L,w,layer="F.Cu": o.append(f'  (segment (start 0 0) (end {float(L)*1e3:.4f} 0) (width {float(w)*1e3:.4f}) (layer "{layer}") (net {ids[net]}))')
    for r in rails:
        if r.plane: o.append(f'  (zone (net {ids[r.net]}) (net_name "{r.net}") (layer "In2.Cu"))'); seg(r.net,mm(2),mm(0.4))
        else: seg(r.net,mm(10),r.width+mm(0.01))
    for pr in pairs:
        w=pr.geom[0] if pr.geom else hs_width; seg(pr.p,pair_len,w); seg(pr.n,pair_len+mil(2),w)
    return "\n".join(o)+"\n)\n"

def layout_mutation_test(c,rails,pairs,fab):
    """Same idea as the schematic mutants: damage a good layout every way we know; the verifier must notice."""
    base=synthetic_pcb(c,rails,pairs,fab)
    assert not verify_layout(c,Board(base),rails,pairs,fab),"synthetic baseline should be clean"
    n=k=0; gaps=[]
    def trial(desc,text):
        nonlocal n,k; n+=1
        if verify_layout(c,Board(text),rails,pairs,fab): k+=1
        else: gaps.append(desc)
    lines=base.split("\n")
    for i,l in enumerate(lines):
        if "(segment" in l:
            m=re.search(r'\(end ([\d.]+) 0\) \(width ([\d.]+)\) \(layer "([^"]+)"\) \(net (\d+)\)',l); L,w,layer,net=float(m[1]),float(m[2]),m[3],m[4]
            rep=lambda new: "\n".join(lines[:i]+[new]+lines[i+1:])
            pr=next((p for p in pairs for x in (p.p,p.n) if f'(net {net} "{x}")' in base),None)
            if pr:
                trial(f"lengthen net {net} by 1 mm",rep(l.replace(f"(end {m[1]} 0)",f"(end {L+1:.4f} 0)")))
                off=next(x for x in ("B.Cu","In1.Cu","In2.Cu") if x not in pr.layers)       # a layer this pair may not use
                trial(f"move net {net} to {off}",rep(l.replace(f'"{layer}"',f'"{off}"')))
                via=f"\n  (via (at 0 0) (size 0.5) (drill 0.25) (layers \"F.Cu\" \"B.Cu\") (net {net}))"
                trial(f"{pr.max_vias+1} vias on net {net}",rep(l+via*(pr.max_vias+1)))
                trial(f"delete routing of net {net}",rep(""))
            else:
                trial(f"neck down net {net}",rep(l.replace(f"(width {m[2]})","(width 0.1000)")))
        if "(zone" in l: trial("delete zone",("\n".join(lines[:i]+lines[i+1:])))
        if "(pad " in l and "(net " in l and n%7==0:
            trial("pad moved to another net",("\n".join(lines[:i]+[re.sub(r'\(net \d+ "[^"]+"\)','(net 1 "GND")' if '"GND"' not in l else '(net 2 "X")',l)]+lines[i+1:])))
    trial("2-layer board",base.replace('(1 "In1.Cu" signal) (2 "In2.Cu" signal) ',""))
    print(f"-- layout mutation test: {k}/{n} damaged layouts caught")
    for g in gaps[:10]: print(f"  RULE GAP  {g}")
    return not gaps
