"""Power budget, after github.com/tcdent/psucalc - but the tree is DERIVED from the netlist, never declared.
Parts carry the facts (loads, converter efficiency, fuse rating, pin limits); the wiring supplies the topology."""
from .units import *
from .core import K

class Load:
    def __init__(s,A=None,W=None,duty=1.0): s.A,s.W,s.duty=A,W,duty
class Converter:
    def __init__(s,vin,vout,effc,imax,frac=0.8,theta_ja=None,tamb=degC(45),tj_max=degC(110),fsw=None,ocl_max=None):
        s.vin,s.vout,s.effc,s.imax,s.frac,s.theta,s.tamb,s.tj_max,s.fsw,s.ocl_max=vin,vout,effc,imax,frac,theta_ja,tamb,tj_max,fsw,ocl_max

def _lim(node,E,what,val,limit,frac):
    node["info"]+=f"  {what} {val} of {limit} ({val/limit:.0%})"
    if val/limit>frac: E.append(f"{node['name']}: {what} {val} exceeds {frac:.0%} of {limit}")

def _demand(c,net,came,E,claimed,seen,W=None):
    node=dict(name=f"net {net.name}",V=net.volts,Ipk=A(0),Iavg=A(0),info="",subs=[])
    if net.name in seen or net.volts is None: return node
    seen=seen|{net.name}
    for part in c.parts.values():
        if part.dnp or part is came: continue
        for pname,ld in part.loads.items():
            pins=[p for p in part.all(pname) if p.net is net]
            if not pins: continue
            claimed.update(pins)
            ipk=ld.A if ld.A is not None else ld.W/net.volts
            sub=dict(name=f"{part.ref} {part.attrs.get('load_name',pname)}",V=net.volts,Ipk=ipk,Iavg=ipk*ld.duty,info="",subs=[])
            if pins[0].imax is not None: _lim(sub,E,"per pin",ipk/len(pins),pins[0].imax,1.0)
            node["subs"].append(sub)
        cv=part.converter
        if cv and part.all(cv.vin)[0].net is net:
            claimed.update(part.all(cv.vin)); out=part.all(cv.vout)[0].net
            inner=_demand(c,out,part,E,claimed,seen,W) if out else dict(Ipk=A(0),Iavg=A(0),subs=[])
            vo=part.all(cv.vout)[0].volts; pk=vo*inner["Ipk"]/cv.effc/net.volts; av=vo*inner["Iavg"]/cv.effc/net.volts
            sub=dict(name=f"{part.ref} buck -> {vo}",V=vo,Ipk=pk,Iavg=av,info="",subs=inner["subs"])
            _lim(sub,E,"out",inner["Ipk"],cv.imax,cv.frac)
            loss=vo*inner["Ipk"]*(1/cv.effc-1)
            if cv.theta is not None:
                tj=cv.tamb+loss*cv.theta; sub["info"]+=f"  loss {loss}  Tj {tj}"
                if tj>cv.tj_max: E.append(f"{part.ref}: junction {tj} exceeds {cv.tj_max}")
            ind=[p.part for p in (out.pins if out else []) if "henries" in p.part.attrs and not p.part.dnp]
            if cv.fsw is not None and ind:
                L=ind[0]; vi,vof=float(net.volts),float(vo)
                ripple=A((vi-vof)*vof/(vi*float(L.attrs["henries"])*float(cv.fsw))); pk=inner["Ipk"]+ripple*0.5
                sub["info"]+=f"  ripple {ripple}  L peak {pk} of Isat {L.attrs['isat']}"
                if pk*1.2>L.attrs["isat"]: E.append(f"{L.ref}: inductor peak {pk} leaves <20% margin to Isat {L.attrs['isat']}")
                if cv.ocl_max is not None and W is not None and cv.ocl_max+ripple>L.attrs["isat"]:
                    W.append(f"{L.ref}: Isat {L.attrs['isat']} is below the worst-case current-limit peak {cv.ocl_max+ripple} (acceptable for a soft-saturating core; TI suggests exceeding it)")
            node["subs"].append(sub)
        if part.power_pass and net in [p.net for p in part.pins]:
            for other in {p.net for p in part.pins if p.net and p.net is not net}:
                inner=_demand(c,other,part,E,claimed,seen,W)
                sub=dict(name=f"{part.ref} {part.value}",V=net.volts,Ipk=inner["Ipk"],Iavg=inner["Iavg"],info="",subs=inner["subs"])
                if part.rating is not None: _lim(sub,E,"peak",inner["Ipk"],part.rating,0.75)
                node["subs"].append(sub)
    node["Ipk"]=sum((x["Ipk"] for x in node["subs"]),A(0)); node["Iavg"]=sum((x["Iavg"] for x in node["subs"]),A(0))
    net.ipk=node["Ipk"]
    return node

def derive(c,E,W=None):
    """Call after check() (needs net voltages). Returns root nodes, one per externally fed net."""
    roots,claimed=[],set()
    for n in c.nets.values():
        feeds=[p for p in n.pins if p.kind is K.PWR_OUT and p.bus and not p.part.dnp]
        if not feeds: continue
        node=_demand(c,n,None,E,claimed,frozenset(),W)
        lim=[p.imax for p in feeds if p.imax is not None]
        if lim: _lim(node,E,"per feed pin",node["Ipk"]/len(feeds),min(lim),1.0)
        roots.append(node)
    for part in c.parts.values():
        for p in part.pins:
            if p.kind is K.PWR_IN and not part.dnp and p.net is not None and p not in claimed:
                E.append(f"{p}: draws power but its part declares no load - cannot budget")
    return roots

def stack(c,root,qty,feed_limit,bus_pins,bus_pin_limit,E,bus_frac=0.5):
    """System level: qty identical boards; the fed board passes (qty-1) boards' current through its bus pins."""
    tot_pk,tot_av=root["Ipk"]*qty,root["Iavg"]*qty; thru=root["Ipk"]*(qty-1)/bus_pins
    print(f"  stack x{qty}: {tot_pk} pk / {tot_av} avg at {root['V']} = {root['V']*tot_pk} pk | feed {tot_pk/feed_limit:.0%} of {feed_limit} | bus {thru}/pin of {bus_pin_limit}")
    if tot_pk>feed_limit: E.append(f"stack: {tot_pk} exceeds feed limit {feed_limit}")
    c.nets[root["name"][4:]].ipk=tot_pk                    # the fed board's bus copper carries the whole stack
    if thru/bus_pin_limit>bus_frac: E.append(f"stack: bus pin {thru} exceeds {bus_frac:.0%} of {bus_pin_limit}")

def show(n,d=0):
    print(f"  {'   '*d}{n['name']}: {n['V']}  {n['Ipk']} pk / {n['Iavg']} avg{n['info']}")
    for x in n["subs"]: show(x,d+1)
