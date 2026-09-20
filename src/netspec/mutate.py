"""Emergent testing. Nobody writes test cases: the library breaks the design in every way it knows how and
demands that the rules notice. A must-catch mutant that survives is a RULE GAP (a bug in the library).
A may-survive mutant that survives is reported as coverage: 'nothing in the spec cares about this'."""
from .core import K, DIFF, check
from .power import derive

def _analyze(c):
    r=check(c)
    try: derive(c,r.E)
    except Exception as e: r.E.append(f"power derivation failed: {e}")
    return r

def _swap(c,p,q): a,b=p.net,q.net; c.move(p,None); c.move(q,None); c.move(p,b); c.move(q,a)

def _mutants(c):
    """Yield (operator, must_catch, description, fn(circuit)) - addressed by ref/pin so they apply to a fresh build."""
    P=lambda ref,num: (lambda cc: cc.parts[ref][num])
    rails=sorted([n for n in c.nets.values() if n.volts is not None],key=lambda n:-float(n.volts))
    for part in c.parts.values():
        if part.dnp:
            yield ("populate_dnp",False,f"populate {part.ref}",lambda cc,r=part.ref: setattr(cc.parts[r],"dnp",False)); continue
        groups={}
        for p in part.pins:
            if p.net is not None:
                yield ("drop_pin",True,f"disconnect {p}",lambda cc,g=P(part.ref,p.num): cc.move(g(cc),None))
                if p.kind is not K.PASSIVE and rails:
                    tgt=rails[0] if p.net is not rails[0] else rails[-1]
                    yield ("short_to_rail",True,f"{p} -> {tgt.name}",lambda cc,g=P(part.ref,p.num),t=tgt.name: cc.move(g(cc),t))
            if p.kind is K.DNC:
                yield ("connect_dnc",True,f"{p} -> GND",lambda cc,g=P(part.ref,p.num): cc.move(g(cc),"GND"))
            if p.pol and p.net: groups.setdefault((p.kind,p.lane),{})[p.pol]=p.num
        for (kind,lane),g in groups.items():
            if set(g)=={"P","N"}:
                yield ("swap_polarity",True,f"{part.ref} {kind.value}{lane} P<->N",lambda cc,r=part.ref,g=g: _swap(cc,cc.parts[r][g["P"]],cc.parts[r][g["N"]]))
        for (kind,lane),g in groups.items():
            if kind is K.TX and (K.RX,lane) in groups:
                h=groups[(K.RX,lane)]
                yield ("swap_tx_rx",True,f"{part.ref} lane {lane} TX<->RX",lambda cc,r=part.ref,g=g,h=h: [_swap(cc,cc.parts[r][g[x]],cc.parts[r][h[x]]) for x in "PN"])
            if isinstance(lane,int) and (kind,lane+1) in groups:
                h=groups[(kind,lane+1)]
                yield ("swap_lanes",True,f"{part.ref} {kind.value} lane {lane}<->{lane+1}",lambda cc,r=part.ref,g=g,h=h: [_swap(cc,cc.parts[r][g[x]],cc.parts[r][h[x]]) for x in "PN"])
        for key in ("ohms","farads","henries"):
            if key in part.attrs:
                for f in (10,0.1):
                    yield ("scale_value",False,f"{part.ref} {key} x{f}",lambda cc,r=part.ref,k=key,f=f: cc.parts[r].attrs.__setitem__(k,cc.parts[r].attrs[k]*f))
        if "v_rating" in part.attrs:
            vs=[p.net.volts for p in part.pins if p.net and p.net.volts is not None]
            if vs: yield ("underrate",True,f"{part.ref} rating -> {max(vs)*0.9}",lambda cc,r=part.ref,v=max(vs)*0.9: cc.parts[r].attrs.__setitem__("v_rating",v))
        if part.rating is not None:
            yield ("underrate",True,f"{part.ref} rating /4",lambda cc,r=part.ref: setattr(cc.parts[r],"rating",cc.parts[r].rating*0.25))

def mutation_test(build,show_survivors=12):
    base=build(); r=_analyze(base)
    if not r.ok: print("  mutation test skipped: baseline has errors"); return False
    stats,gaps,surv={}, [], []
    for op,must,desc,fn in list(_mutants(base)):
        c=build(); fn(c); caught=not _analyze(c).ok
        s=stats.setdefault(op,[0,0,must]); s[0]+=1; s[1]+=caught
        if not caught: (gaps if must else surv).append(f"{op}: {desc}")
    print(f"-- mutation test: {sum(s[0] for s in stats.values())} mutants generated from the design")
    for op,(n,k,must) in stats.items(): print(f"  {op:14s} {k:4d}/{n:<4d} caught  {'(must catch)' if must else '(coverage only)'}")
    for g in gaps: print(f"  RULE GAP  {g}")
    if surv:
        vals={}; other=[]
        for x in surv:
            if x.startswith("scale_value: "): ref,_,f=x[13:].split(" "); vals.setdefault(ref,[]).append(f)
            else: other.append(x)
        free=[r for r,f in vals.items() if len(f)==2]; half=[f"{r} ({f[0]} ok)" for r,f in vals.items() if len(f)==1]
        print(f"  coverage: values no rule constrains: {', '.join(free) or 'none'}")
        if half: print(f"  coverage: constrained in one direction only: {', '.join(half)}")
        for x in other[:show_survivors]: print(f"  coverage: survives: {x}")
    return not gaps
