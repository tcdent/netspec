"""netspec core: typed pins, bundles with roles, disciplines, and rules that run themselves.
Verilog heritage: bundles ~ SystemVerilog interface+modport, disciplines ~ Verilog-AMS, crossings ~ UPF."""
from enum import Enum
from .units import *

class K(Enum):
    PWR_OUT="pwr_out"; PWR_IN="pwr_in"; GND="gnd"
    TX="tx"; RX="rx"; CLK_OUT="clk_out"; CLK_IN="clk_in"
    OUT="out"; IN="in"; OD="open_drain"; BIDIR="bidir"; ANALOG="analog"
    PASSIVE="passive"; NC="nc"; DNC="do_not_connect"
MATE={K.TX:K.RX, K.CLK_OUT:K.CLK_IN}
DIFF=set(MATE)|set(MATE.values())
LOGIC={K.OUT,K.IN,K.OD,K.BIDIR}
DRIVERS={K.OUT,K.OD,K.BIDIR,K.PASSIVE,K.PWR_OUT,K.GND}
_DISC={K.PWR_OUT:"rail",K.PWR_IN:"rail",K.GND:"gnd",K.TX:"pcie",K.RX:"pcie",K.CLK_OUT:"hcsl",K.CLK_IN:"hcsl",K.ANALOG:"analog"}

class Pin:
    def __init__(s,part,num,name,kind,volts=None,vmin=None,vmax=None,bus=False,imax=None,lane=None,pol=None,vio=None,may_float=False,disc=None):
        if kind in LOGIC and vio is None: raise ValueError(f"{part.ref}.{num} {name}: logic pins must declare vio")
        if kind in DIFF and (lane is None or pol not in ("P","N")): raise ValueError(f"{part.ref}.{num}: diff pins need lane and pol")
        s.part,s.num,s.name,s.kind,s.net=part,str(num),name,kind,None
        s.volts,s.vmin,s.vmax,s.bus,s.imax,s.lane,s.pol,s.vio,s.may_float=volts,vmin,vmax,bus,imax,lane,pol,vio,may_float
        s.nc_ok=False; s.disc=disc or (f"logic {vio}" if kind in LOGIC else _DISC.get(kind))
    def __repr__(s): return f"{s.part.ref}.{s.num}({s.name})"

class BundleType:
    """signals: name -> (kind seen at role[0], kind seen at role[1])"""
    def __init__(s,name,roles,signals): s.name,s.roles,s.signals=name,roles,signals
class Bundle:
    def __init__(s,btype,role,part,pins): s.type,s.role,s.part,s.pins=btype,role,part,pins

class Part:
    def __init__(s,ref,value="",mpn="",dnp=False,power_pass=False,rating=None,complete=True,source="",verified=False,**attrs):
        s.ref,s.value,s.mpn,s.dnp,s.power_pass,s.rating,s.complete,s.source,s.verified=ref,value,mpn,dnp,power_pass,rating,complete,source,verified
        s.attrs,s.pins,s.bundles,s.rules,s.loads,s.converter=attrs,[],{},[],{},None
    def pin(s,num,name,kind,**a): p=Pin(s,num,name,kind,**a); s.pins.append(p); return p
    def __getitem__(s,key):
        key=str(key); hit=[p for p in s.pins if p.num==key] or [p for p in s.pins if p.name==key]
        if len(hit)!=1: raise KeyError(f"{s.ref}[{key}] matched {len(hit)} pins")
        return hit[0]
    def all(s,name): return [p for p in s.pins if p.name==name]
    def __getattr__(s,name):
        b=s.__dict__.get("bundles",{})
        if name in b: return b[name]
        raise AttributeError(name)
    def bundle(s,name,btype,role,pins):
        """Double entry: the pin kinds typed from the pin table must agree with what the role expects."""
        idx=btype.roles.index(role)
        if set(pins)!=set(btype.signals): raise ValueError(f"{s.ref}.{name}: signals {set(pins)^set(btype.signals)} missing/extra")
        for sig,p in pins.items():
            if p.kind is not btype.signals[sig][idx]:
                raise ValueError(f"{s.ref}.{name}: {sig} as {role} must be {btype.signals[sig][idx].value}, but {p} is typed {p.kind.value}")
        s.bundles[name]=Bundle(btype,role,s,pins)

class Net:
    def __init__(s,name): s.name,s.volts,s.pins,s.disc,s.ipk=name,None,[],None,None
class Circuit:
    def __init__(s,name): s.name,s.parts,s.nets,s.rules=name,{},{},[]
    def add(s,part): assert part.ref not in s.parts,f"duplicate {part.ref}"; s.parts[part.ref]=part; return part
    def net(s,name): return s.nets.setdefault(name,Net(name))
    def connect(s,net,*pins):
        n=s.net(net) if isinstance(net,str) else net
        for p in pins:
            for q in (p if isinstance(p,(list,tuple)) else [p]):
                assert q.net is None,f"{q} already on {q.net.name}"; q.net=n; n.pins.append(q)
    def nc(s,*pins):
        for p in pins:
            for q in (p if isinstance(p,(list,tuple)) else [p]): q.nc_ok=True
    def link(s,a,b,prefix):
        if a.type is not b.type: raise ValueError(f"link {prefix}: {a.type.name} vs {b.type.name}")
        if a.role==b.role: raise ValueError(f"link {prefix}: both ends are '{a.role}' ({a.part.ref}, {b.part.ref})")
        for sig in a.type.signals: s.connect(f"{prefix}_{sig}",a.pins[sig],b.pins[sig])
    def move(s,pin,net):                                   # used by the mutation engine
        if pin.net: pin.net.pins.remove(pin)
        pin.net=None
        if net is not None: s.connect(net,pin)

# ---- standard bundle types
def _pcie(n):
    sig={}
    for l in range(n):
        for pol in "PN": sig[f"H2D{l}_{pol}"]=(K.TX,K.RX); sig[f"D2H{l}_{pol}"]=(K.RX,K.TX)      # _P/_N suffix: KiCad pairs these automatically
    sig.update({"REFCLK_P":(K.CLK_OUT,K.CLK_IN),"REFCLK_N":(K.CLK_OUT,K.CLK_IN),"PERST#":(K.OUT,K.IN)})
    return BundleType(f"pcie_x{n}",("host","device"),sig)
PCIE_X4=_pcie(4)

# ---- reusable part-level rules (attach in part definitions; they run without the designer asking)
def _res_between(c,net,other):
    if net is None or other is None: return []
    return [p.part for p in net.pins if "ohms" in p.part.attrs and not p.part.dnp and [q for q in p.part.pins if q is not p][0].net is other]
def feedback_divider(fb,vout,vref,tol=0.02):
    def rule(c,part,E):
        n=part[fb].net; top=_res_between(c,n,part[vout].net); bot=_res_between(c,n,c.nets.get("GND"))
        if len(top)!=1 or len(bot)!=1: E.append(f"{part.ref}: feedback divider not found on {n.name if n else None}"); return
        v=vref*(1+top[0].attrs["ohms"]/bot[0].attrs["ohms"]); want=part[vout].volts
        if abs(v-want)/want>tol: E.append(f"{part.ref}: divider {top[0].ref}/{bot[0].ref} sets {v}, part declares {want}")
    return rule
def config_resistor(pin,value,tol=0.011):
    def rule(c,part,E):
        r=_res_between(c,part[pin].net,c.nets.get("GND")) if part[pin].net else []
        if len(r)!=1: E.append(f"{part.ref}: {pin} needs one resistor to GND"); return
        if abs(r[0].attrs["ohms"]-value)/value>tol: E.append(f"{part.ref}: {pin} resistor {r[0].ref} is {r[0].attrs['ohms']}, configuration needs {value}")
    return rule
def requires_cap(pin,min_f):
    def rule(c,part,E):
        n=part.all(pin)[0].net; g=c.nets.get("GND")
        tot=sum((p.part.attrs["farads"] for p in (n.pins if n else []) if "farads" in p.part.attrs and not p.part.dnp and [q for q in p.part.pins if q is not p][0].net is g),Q(0,"F"))
        if tot<min_f: E.append(f"{part.ref}: {pin} needs >= {min_f} to GND, found {tot}")
    return rule

def _other(p): return [q for q in p.part.pins if q is not p][0].net
def fb_divider_to_rail(fb,vref,want,tol=0.02):
    """Discrete converters: the divider's top end is on the output rail, not on a pin of the IC."""
    def rule(c,part,E):
        n=part[fb].net; g=c.nets.get("GND")
        rs=[p for p in (n.pins if n else []) if "ohms" in p.part.attrs and not p.part.dnp]
        top=[p.part for p in rs if _other(p) is not g]; bot=[p.part for p in rs if _other(p) is g]
        if len(top)!=1 or len(bot)!=1: E.append(f"{part.ref}: feedback divider not found on {fb}"); return
        v=vref*(1+top[0].attrs["ohms"]/bot[0].attrs["ohms"])
        if abs(v-want)/want>tol: E.append(f"{part.ref}: divider {top[0].ref}/{bot[0].ref} sets {v}, design wants {want}")
    return rule
def ratio_divider(pin,top_pin,lo,hi,what):
    """A pin strapped by a divider from another pin's net to GND; the ratio must fall in [lo,hi]."""
    def rule(c,part,E):
        n=part[pin].net; g=c.nets.get("GND"); t=part[top_pin].net
        rt=_res_between(c,n,t); rb=_res_between(c,n,g)
        if len(rt)!=1 or len(rb)!=1: E.append(f"{part.ref}: {pin} needs a divider from {top_pin} to GND"); return
        f=rb[0].attrs["ohms"]/(rb[0].attrs["ohms"]+rt[0].attrs["ohms"])
        if not lo<=f<=hi: E.append(f"{part.ref}: {pin} divider ratio {f:.1%} outside {lo:.0%}-{hi:.0%} required for {what}")
    return rule
def threshold_divider(pin,src_pin,v_on,v_max):
    """Enable-style input fed from a supply through a divider: must clear the threshold and respect abs-max."""
    def rule(c,part,E):
        n=part[pin].net; g=c.nets.get("GND"); src=part.all(src_pin)[0].net
        rt=_res_between(c,n,src); rb=_res_between(c,n,g)
        if len(rt)!=1 or len(rb)!=1 or src is None or src.volts is None: E.append(f"{part.ref}: {pin} needs a divider from {src_pin} (floating = disabled)"); return
        v=src.volts*(rb[0].attrs["ohms"]/(rb[0].attrs["ohms"]+rt[0].attrs["ohms"]))
        if v<v_on*1.2: E.append(f"{part.ref}: {pin} sits at {v}, needs >= {v_on*1.2} to turn on with margin")
        if v>v_max: E.append(f"{part.ref}: {pin} sits at {v}, above its {v_max} limit")
    return rule
def output_filter(sw_pin,l_nom,c_min,c_max,cff=None,fb=None,l_tol=0.25):
    """Internally compensated converters are only stable inside the datasheet's L and C window."""
    def rule(c,part,E):
        sw=part.all(sw_pin)[0].net; g=c.nets.get("GND")
        L=[p for p in (sw.pins if sw else []) if "henries" in p.part.attrs and not p.part.dnp]
        if len(L)!=1: E.append(f"{part.ref}: expected exactly one inductor on {sw_pin}"); return
        h=L[0].part.attrs["henries"]; out=_other(L[0])
        if abs(h-l_nom)/l_nom>l_tol: E.append(f"{part.ref}: inductor {L[0].part.ref} is {h}, datasheet window is {l_nom} +/-{l_tol:.0%}")
        tot=sum((p.part.attrs["farads"] for p in out.pins if "farads" in p.part.attrs and not p.part.dnp and _other(p) is g),Q(0,"F"))
        if not c_min<=tot<=c_max: E.append(f"{part.ref}: output capacitance {tot} outside {c_min} to {c_max}")
        if cff:
            ff=[p.part.attrs["farads"] for p in out.pins if "farads" in p.part.attrs and not p.part.dnp and _other(p) is part[fb].net]
            if len(ff)!=1 or not cff[0]<=ff[0]<=cff[1]: E.append(f"{part.ref}: needs one feed-forward cap {cff[0]} to {cff[1]} across the top feedback resistor")
    return rule

def cap_between(pin_a,pin_b,nominal,tol=0.5):
    def rule(c,part,E):
        a,b=part.all(pin_a)[0].net,part.all(pin_b)[0].net
        caps=[p.part for p in (a.pins if a else []) if "farads" in p.part.attrs and not p.part.dnp and _other(p) is b]
        if len(caps)!=1 or abs(caps[0].attrs["farads"]-nominal)/nominal>tol: E.append(f"{part.ref}: needs one {nominal} capacitor between {pin_a} and {pin_b}")
    return rule

class Result:
    def __init__(s): s.E,s.W,s.I=[],[],[]
    @property
    def ok(s): return not s.E

def check(c,cap_derate=0.8):
    r=Result(); E,W,I=r.E,r.W,r.I
    live=lambda n:[p for p in n.pins if not p.part.dnp]
    unv=[p.ref for p in c.parts.values() if not p.verified and len(p.pins)>2]
    if unv: I.append(f"part definitions not yet human-verified against datasheet: {', '.join(unv)}")
    for part in c.parts.values():
        if not part.complete: W.append(f"{part.ref}: pin numbers incomplete - netlist export blocked")
        if part.dnp: continue
        for p in part.pins:
            if p.kind is K.DNC and p.net: E.append(f"{p}: DO-NOT-CONNECT pin is on {p.net.name}")
            elif p.kind is K.NC and p.net: W.append(f"{p}: NC pin is on {p.net.name}")
            elif p.net is None and p.kind not in (K.NC,K.DNC) and not (p.nc_ok or p.may_float): E.append(f"{p}: {p.kind.value} pin unconnected")
            elif p.net is not None and p.nc_ok: E.append(f"{p}: declared nc() but is on {p.net.name}")
    for n in c.nets.values():                                  # a net with one pin connects nothing: a dangling end (this is
        if len(n.pins)==1 and n.pins[0].kind is not K.NC:      # also how a bypassed fuse shows up - its far pin is left alone).
            E.append(f"net {n.name}: only {n.pins[0]} is on it - it connects nothing")   # A net that ends at a DNP option is deliberate.
    for n in c.nets.values():
        lp=live(n)
        if len(lp)<=1 and n.pins: (I if len(n.pins)>len(lp) else W).append(f"net {n.name}: {len(lp)} live pin(s)"+(" (stub behind DNP part)" if len(n.pins)>len(lp) else ""))
        # discipline: every non-passive pin on a net must speak the same electrical language
        ds={p.disc for p in lp if p.disc}; n.disc=next(iter(ds)) if len(ds)==1 else None
        if len(ds)>1: E.append(f"net {n.name}: mixed disciplines {sorted(ds)} e.g. {[p for p in lp if p.disc][:3]}")
        if n.name=="GND" and ds-{"gnd"}: E.append(f"GND: non-ground pins present")
    # power: sources propagate through power_pass parts
    src={n.name:[p for p in live(n) if p.kind is K.PWR_OUT] for n in c.nets.values()}
    ch=True
    while ch:
        ch=False
        for part in c.parts.values():
            if part.power_pass and not part.dnp:
                ns=[p.net for p in part.pins if p.net]
                for a in ns:
                    for b in ns:
                        for sp in src[a.name]:
                            if sp not in src[b.name]: src[b.name].append(sp); ch=True
    for n in c.nets.values():
        sinks=[p for p in live(n) if p.kind is K.PWR_IN]; s=src[n.name]
        if not sinks and not s: continue
        if not s: E.append(f"net {n.name}: power inputs {sinks[:3]} have no source"); continue
        if len({p.volts for p in s})>1: E.append(f"net {n.name}: sources at different voltages {s[:4]}"); continue
        if len({p.part.ref for p in s})>1 and not all(p.bus for p in s): E.append(f"net {n.name}: multiple non-bus sources {s[:4]}")
        n.volts=s[0].volts
        for p in sinks:
            if not (p.vmin<=n.volts<=p.vmax): E.append(f"{p}: needs {p.vmin} to {p.vmax}, net {n.name} is {n.volts}")
        g=c.nets.get("GND")
        if sinks and not [p for p in live(n) if "farads" in p.part.attrs and [q for q in p.part.pins if q is not p][0].net is g]:
            E.append(f"net {n.name}: power inputs with no decoupling capacitor to GND")
    for part in c.parts.values():
        vr=part.attrs.get("v_rating")
        if vr is not None and not part.dnp:
            vs=[p.net.volts for p in part.pins if p.net and p.net.volts is not None]
            if vs and max(vs)>vr*cap_derate: E.append(f"{part.ref}: {vr} rating on {max(vs)} net exceeds {int(cap_derate*100)}% derating")
    # differential: direction, polarity, lane, pair integrity
    for n in c.nets.values():
        d=[p for p in live(n) if p.kind in DIFF]
        if not d: continue
        if len(d)!=2: E.append(f"net {n.name}: differential net needs exactly 2 endpoints, has {len(d)}"); continue
        a,b=d if d[0].kind in MATE else d[::-1]
        if MATE.get(a.kind) is not b.kind: E.append(f"net {n.name}: {a} [{a.kind.value}] wired to {b} [{b.kind.value}]")
        if a.pol!=b.pol: E.append(f"net {n.name}: polarity {a} vs {b}")
        if a.lane!=b.lane: E.append(f"net {n.name}: lane {a.lane} of {a.part.ref} wired to lane {b.lane} of {b.part.ref}")
    for part in c.parts.values():
        g={}
        for p in part.pins:
            if p.pol and p.net: g.setdefault((p.kind,p.lane),{})[p.pol]=p
        partners=set()
        for (kind,lane),pr in g.items():
            if set(pr)!={"P","N"}: E.append(f"{part.ref}: {kind.value} lane {lane} has only {list(pr)} connected"); continue
            m=[[q for q in live(pr[x].net) if q is not pr[x] and q.pol] for x in "PN"]
            if m[0] and m[1]:
                partners|={m[0][0].part.ref,m[1][0].part.ref}
                if m[0][0].part is not m[1][0].part: E.append(f"{part.ref}: pair {kind.value}{lane} splits across parts")
        if len(partners)>1: E.append(f"{part.ref}: one link is spread across {sorted(partners)}")
    # logic: drivers, contention
    for n in c.nets.values():
        lp=live(n); ins=[p for p in lp if p.kind in (K.IN,K.ANALOG)]
        if ins and len(lp)>1 and not [p for p in lp if p.kind in DRIVERS]: E.append(f"net {n.name}: inputs {ins} have no driver or bias")
        if len([p for p in lp if p.kind is K.OUT])>1: E.append(f"net {n.name}: multiple push-pull drivers")
    # domain crossing (UPF idea): a plain 2-pin passive may not bridge two different logic voltages
    for part in c.parts.values():
        if len(part.pins)==2 and all(p.kind is K.PASSIVE and p.net for p in part.pins) and not part.attrs.get("level_shifter"):
            a,b=(p.net.disc for p in part.pins)
            if a and b and a!=b and a.startswith("logic") and b.startswith("logic"):
                (I if part.dnp else E).append(f"{part.ref}: bridges {a} and {b} without a level shifter"+(" (safe only while DNP)" if part.dnp else ""))
    for part in c.parts.values():
        if not part.dnp:
            for rule in part.rules:
                try: rule(c,part,E)
                except Exception as e: E.append(f"{part.ref}: rule could not be evaluated ({type(e).__name__}: {e})")
    for rule in c.rules: rule(c,E)
    return r

def report(c,r,verbose=True):
    print(f"== {c.name}: {len(c.parts)} parts, {len(c.nets)} nets -> {len(r.E)} errors, {len(r.W)} warnings, {len(r.I)} notes")
    for tag,L in (("ERROR",r.E),("warn ",r.W),("note ",r.I)):
        for m in (L if verbose or tag=="ERROR" else []): print(f"  {tag} {m}")
