"""KiCad backend: netlist, schematic, project file and board. Component IDs are derived from the reference
designator, so re-exporting after a spec change updates a layout in place instead of replacing footprints.
Files are written in KiCad 9 format; `kicad-cli pcb upgrade` / `sch upgrade` bring them to the running version
(exercised against 10.99). The netlist import path (File > Import > Netlist) is the one output not yet tried in KiCad."""
import uuid, datetime
NS=uuid.UUID("6f1c0e2a-5b0d-4e57-9a53-6e6574737065")
q=lambda s: '"'+str(s).replace('\\','\\\\').replace('"','\\"')+'"'

def export_netlist(c,path):
    msgs=[f"{p.ref}: pin numbers incomplete" for p in c.parts.values() if not p.complete]
    msgs+=[f"{p.ref}: no footprint assigned" for p in c.parts.values() if not p.attrs.get("footprint")]
    if msgs: return ["BLOCKED - "+m for m in msgs]
    o=['(export (version "E")',f'  (design (source {q(c.name)}) (date {q(datetime.date.today().isoformat())}) (tool "netspec"))','  (components']
    for p in c.parts.values():
        u=uuid.uuid5(NS,p.ref)
        o.append(f'    (comp (ref {q(p.ref)}) (value {q(p.value)}) (footprint {q(p.attrs["footprint"])})\n      (fields (field (name "MPN") {q(p.mpn)})'
                 +(' (field (name "DNP") "1")' if p.dnp else '')+f')\n      (sheetpath (names "/") (tstamps "/")) (tstamps {q(u)}))')
    o.append('  )\n  (nets')
    for i,n in enumerate(sorted(c.nets.values(),key=lambda n:n.name),1):
        o.append(f'    (net (code {q(i)}) (name {q(n.name)})'+"".join(f'\n      (node (ref {q(x.part.ref)}) (pin {q(x.num)}) (pinfunction {q(x.name)}))' for x in n.pins)+')')
    o.append('  )\n)')
    open(path,"w").write("\n".join(o)+"\n")
    custom=sorted({p.attrs["footprint"] for p in c.parts.values() if p.attrs["footprint"].startswith("netspec:")})
    return [f"wrote {path}: {len(c.parts)} components, {len(c.nets)} nets"]+[f"custom footprint still to draw: {f}" for f in custom]

# ---- board file ---------------------------------------------------------------------------------------------
# The .kicad_pcb is a build output: library footprints are embedded verbatim, pads get their nets from the spec,
# and every footprint carries the same ref-derived ID as the netlist. It is written in the KiCad 9 file format;
# run `kicad-cli pcb upgrade` on it so KiCad itself rewrites it in whatever format the installed version uses.
import re, math, pathlib
_TOK=re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+')
def _parse(text):                                # like layout.sexpr, but strings keep their quotes so it round-trips
    st=[[]]
    for t in _TOK.findall(text):
        if t=="(": st.append([])
        elif t==")": x=st.pop(); st[-1].append(x)
        else: st[-1].append(t)
    return st[0][0]
def _dump(n,d=0):
    if not isinstance(n,list): return n
    flat="("+" ".join(_dump(x) for x in n)+")"
    if len(flat)<=110 or not any(isinstance(x,list) for x in n): return flat
    i=next(i for i,x in enumerate(n) if isinstance(x,list))
    return "("+" ".join(n[:i])+"".join("\n"+"\t"*(d+1)+_dump(x,d+1) for x in n[i:])+"\n"+"\t"*d+")"

_CU={2:["F.Cu","B.Cu"],4:["F.Cu","In1.Cu","In2.Cu","B.Cu"],6:["F.Cu","In1.Cu","In2.Cu","In3.Cu","In4.Cu","B.Cu"]}
_CUNUM={"F.Cu":0,"B.Cu":2,"In1.Cu":4,"In2.Cu":6,"In3.Cu":8,"In4.Cu":10}
_TECH=[(9,"F.Adhes"),(11,"B.Adhes"),(13,"F.Paste"),(15,"B.Paste"),(5,"F.SilkS"),(7,"B.SilkS"),(1,"F.Mask"),(3,"B.Mask"),
       (17,"Dwgs.User"),(19,"Cmts.User"),(25,"Edge.Cuts"),(27,"Margin"),(31,"F.CrtYd"),(29,"B.CrtYd"),(35,"F.Fab"),(33,"B.Fab")]

def _rotate(body,rot):
    """KiCad stores pad and text angles as absolute board angles, so a rotated footprint adds its angle to each."""
    if not rot: return
    for e in body:
        if isinstance(e,list) and e[0] in ("pad","property","fp_text"):
            at=next((x for x in e if isinstance(x,list) and x[0]=="at"),None)
            if at is not None:
                ang=(float(at[3]) if len(at)>3 and at[3] not in ("unlocked",) else 0.0)+rot
                at[3:4]=[f"{ang%360:g}"] if len(at)>3 else []; 
                if len(at)==3: at.append(f"{ang%360:g}")

def _mirror(n,top=True):
    """Mirror a footprint body top-bottom in its own frame, which is how KiCad stores a part flipped to the other side:
    y and angles change sign. (A left-right flip is this plus 180 degrees of placement angle.) The 3D model is left
    alone - KiCad turns it over itself."""
    for e in n:
        if not isinstance(e,list) or (top and e[0]=="model"): continue
        if e[0] in ("at","start","end","mid","center","xy") and len(e)>2:
            e[2]=f"{-float(e[2]):g}"
            if e[0]=="at" and len(e)>3 and e[3] not in ("unlocked",): e[3]=f"{-float(e[3])%360:g}"
        _mirror(e,False)

def _to_back(n):
    """Swap a (mirrored) footprint's layers front for back; text on the back reads mirrored."""
    if isinstance(n,list):
        out=[_to_back(x) for x in n]
        if out and out[0]=="effects":
            j=next((x for x in out if isinstance(x,list) and x[0]=="justify"),None)
            if j is None: out.append(["justify","mirror"])
            elif "mirror" not in j: j.append("mirror")
        return out
    m=re.fullmatch(r'"([FB])\.(\w+)"',n)
    return f'"{"B" if m[1]=="F" else "F"}.{m[2]}"' if m else n

def find_footprint(name,roots):
    lib,_,fp=name.partition(":")
    for r in roots:
        f=pathlib.Path(r)/f"{lib}.pretty"/f"{fp}.kicad_mod"
        if f.exists(): return f

def _ref_to_fab(body):
    """Move a footprint's reference designator from the silkscreen to the fabrication layer."""
    for e in body:
        if isinstance(e,list) and ((e[0]=="property" and e[1]=='"Reference"') or (e[0]=="fp_text" and e[1]=="reference")):
            for x in e:
                if isinstance(x,list) and x[0]=="layer" and x[1]=='"F.SilkS"': x[1]='"F.Fab"'

def export_board(c,path,fab,fp_roots,pitch=20.0,placement=None,outline=None,mechanical=(),notes=(),silk=(),zones=(),tracks=(),vias=(),keepouts=(),quiet_refs=("0402","0603","MountingHole","TestPoint"),ref_pos=None,overwrite=False):
    """Write a .kicad_pcb holding every part whose footprint can be found under fp_roots (directories of .pretty
    libraries, searched in order). Parts without a position in `placement` {ref:(x,y,rot)} land on a grid beside
    the origin, the way a netlist import drops them. `outline` (x0,y0,x1,y1) draws the board edge; `mechanical`
    [(ref,footprint,x,y,rot)] adds board-only footprints such as mounting holes; `notes` [(x0,y0,x1,y1,text)] draws
    labelled rectangles on Cmts.User (keep-outs, where a drive lies); `silk` [(x,y,text,size)] prints legends on the
    front silkscreen; `zones` [(net,layer,shape,priority[,"solid"])] adds copper pours and planes, where shape is a
    box (x0,y0,x1,y1) or a list of corner points and "solid" drops the thermal reliefs - zones are written unfilled,
    fill them with `kicad-cli pcb drc --refill-zones --save-board`; `tracks` [(net,layer,width,[(x,y),..])] and
    `vias` [(net,x,y,diameter,drill)] are routed copper; `keepouts` [(layer,(x0,y0,x1,y1))] are areas a plane may not
    fill (anti-pads under connector pads). Footprints whose name contains any of `quiet_refs` keep their
    reference designator off the silkscreen (it goes to the fabrication layer): small parts packed together only
    print as a smear. A board KiCad has saved is kept
    unless overwrite=True. Returns messages; refuses pad/pin mismatches."""
    if not overwrite and pathlib.Path(path).exists() and '(generator "netspec")' not in open(path).read(400):
        return [f"kept {path}: last saved by KiCad, so it holds layout work - not overwritten"]
    ids={n.name:i for i,n in enumerate(sorted(c.nets.values(),key=lambda n:n.name),1)}
    # KiCad names the net of a lone pin itself; using the same names keeps its schematic-parity check quiet
    lone=lambda x: f"unconnected-({x.part.ref}-{x.name}-Pad{x.num})".replace("/","{slash}")
    for p in c.parts.values():
        for x in p.pins:
            if not x.net: ids[lone(x)]=len(ids)+1
    placement=placement or {}; msgs=[]; fps=[]; cols=max(1,math.ceil(math.sqrt(len(c.parts))))
    for k,p in enumerate(sorted(c.parts.values(),key=lambda p:p.ref)):
        name=p.attrs.get("footprint") or ""; f=find_footprint(name,fp_roots)
        if not f: msgs.append(f"{p.ref}: footprint {name or '(none)'} not found - left off the board"); continue
        n=_parse(f.read_text()); pads={x[1].strip('"') for x in n if isinstance(x,list) and x[0]=="pad"}
        missing=sorted({x.num for x in p.pins if x.net}-pads)
        if missing: msgs.append(f"{p.ref}: {name} has no pad named {', '.join(missing)} - left off the board"); continue
        x,y,rot,*side=placement.get(p.ref,((k%cols)*pitch,(k//cols)*pitch,0)); u=uuid.uuid5(NS,p.ref); back="B" in side
        body=[e for e in n[2:] if not (isinstance(e,list) and e[0] in ("version","generator","generator_version","layer","uuid","at","path"))]
        netof={x.num:(x.net.name if x.net else lone(x)) for x in p.pins}
        for e in body:
            if not isinstance(e,list): continue
            if e[0]=="property" and e[1]=='"Reference"': e[2]=q(p.ref)
            if e[0]=="property" and e[1]=='"Value"': e[2]=q(p.value)
            if e[0]=="property" and e[1] not in ('"Reference"','"Value"') and ["hide","yes"] not in e: e.append(["hide","yes"])  # vendor extras
            if e[0]=="fp_text" and e[1]=="reference": e[2]=q(p.ref)      # pre-KiCad-8 footprints (vendor downloads)
            if e[0]=="fp_text" and e[1]=="value": e[2]=q(p.value)
            if e[0]=="pad" and e[1].strip('"') in netof: nn=netof[e[1].strip('"')]; e.append(["net",str(ids[nn]),q(nn)])
            if e[0]=="attr" and p.dnp and "dnp" not in e: e.append("dnp")
        if back: _mirror(body)
        _rotate(body,rot)
        if any(k in name for k in quiet_refs): _ref_to_fab(body)
        if ref_pos and p.ref in ref_pos:                           # silkscreen reference moved to a board position, upright
            rx,ry,*sz=ref_pos[p.ref]; t=math.radians(rot); dx,dy=rx-x,ry-y
            for e in body:
                if isinstance(e,list) and e[0]=="property" and e[1]=='"Reference"':
                    for a in e:
                        if isinstance(a,list) and a[0]=="at": a[1:]=[f"{dx*math.cos(t)-dy*math.sin(t):g}",f"{dx*math.sin(t)+dy*math.cos(t):g}","0"]
                        if sz and isinstance(a,list) and a[0]=="effects":
                            for f in a:
                                if isinstance(f,list) and f[0]=="font": f[1:]=[["size",f"{sz[0]:g}",f"{sz[0]:g}"],["thickness",f"{sz[0]*0.15:g}"]]
        mpn=["property",q("MPN"),q(p.mpn),["at","0","0","0"],["layer",q("F.Fab")],["hide","yes"],
             ["effects",["font",["size","1","1"],["thickness","0.15"]]]]
        if back: body=_to_back(body)
        fps.append([n[0],q(name),["layer",q("B.Cu" if back else "F.Cu")],["uuid",q(u)],["at",f"{x:g}",f"{y:g}",f"{rot:g}"],["path",q(f"/{u}")]]+body+[mpn])
    for ref,name,x,y,rot in mechanical:
        f=find_footprint(name,fp_roots)
        if not f: msgs.append(f"{ref}: footprint {name} not found - left off the board"); continue
        n=_parse(f.read_text()); body=[e for e in n[2:] if not (isinstance(e,list) and e[0] in ("version","generator","generator_version","layer","uuid","at","path"))]
        for e in body:
            if isinstance(e,list) and e[0]=="property" and e[1]=='"Reference"': e[2]=q(ref)
            if isinstance(e,list) and e[0]=="fp_text" and e[1]=="reference": e[2]=q(ref)
        attr=next((e for e in body if isinstance(e,list) and e[0]=="attr"),None)
        if attr is None: attr=["attr"]; body.insert(0,attr)
        attr+=[k for k in ("board_only","exclude_from_pos_files","exclude_from_bom") if k not in attr]
        _rotate(body,rot)
        if any(k in name for k in quiet_refs): _ref_to_fab(body)
        if ref_pos and ref in ref_pos:                           # silkscreen reference moved to a board position, upright
            rx,ry,*sz=ref_pos[ref]; t=math.radians(rot); dx,dy=rx-x,ry-y
            for e in body:
                if isinstance(e,list) and e[0]=="property" and e[1]=='"Reference"':
                    for a in e:
                        if isinstance(a,list) and a[0]=="at": a[1:]=[f"{dx*math.cos(t)-dy*math.sin(t):g}",f"{dx*math.sin(t)+dy*math.cos(t):g}","0"]
                        if sz and isinstance(a,list) and a[0]=="effects":
                            for f in a:
                                if isinstance(f,list) and f[0]=="font": f[1:]=[["size",f"{sz[0]:g}",f"{sz[0]:g}"],["thickness",f"{sz[0]*0.15:g}"]]
        fps.append([n[0],q(name),["layer",q("F.Cu")],["uuid",q(uuid.uuid5(NS,ref))],["at",f"{x:g}",f"{y:g}",f"{rot:g}"]]+body)
    cu=_CU[fab.layers]
    o=[f'(kicad_pcb (version 20241229) (generator "netspec") (generator_version "0.8")',
       f'\t(general (thickness {float(fab.thickness)*1e3:g}))','\t(paper "A4")',
       '\t(layers\n'+"\n".join(f'\t\t({_CUNUM[l]} "{l}" signal)' for l in cu)+"\n"+"\n".join(f'\t\t({i} "{l}" user)' for i,l in _TECH)+'\n\t)',
       '\t(setup (pad_to_mask_clearance 0))','\t(net 0 "")']+[f'\t(net {i} {q(n)})' for n,i in ids.items()]
    o+=["\t"+_dump(fp,1) for fp in fps]
    if outline:
        x0,y0,x1,y1=outline
        o.append(f'\t(gr_rect (start {x0:g} {y0:g}) (end {x1:g} {y1:g}) (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts") (uuid {q(uuid.uuid5(NS,"outline"))}))')
    for i,(x0,y0,x1,y1,text) in enumerate(notes):
        o.append(f'\t(gr_rect (start {x0:g} {y0:g}) (end {x1:g} {y1:g}) (stroke (width 0.15) (type dash)) (fill no) (layer "Cmts.User") (uuid {q(uuid.uuid5(NS,f"note{i}"))}))')
        if text: o.append(f'\t(gr_text {q(text)} (at {(x0+x1)/2:g} {y0+2.2:g} 0) (layer "Cmts.User") (uuid {q(uuid.uuid5(NS,f"note{i}t"))}) (effects (font (size 1.5 1.5) (thickness 0.15))))')
    for i,(net,layer,shape,prio,*opt) in enumerate(zones):
        if net not in ids: msgs.append(f"zone {i}: no net {net} - skipped"); continue
        pts=[(shape[0],shape[1]),(shape[2],shape[1]),(shape[2],shape[3]),(shape[0],shape[3])] if isinstance(shape[0],(int,float)) else shape
        o.append(f'\t(zone (net {ids[net]}) (net_name {q(net)}) (layer {q(layer)}) (uuid {q(uuid.uuid5(NS,f"zone{i}/{net}/{layer}"))}) (hatch edge 0.5) (priority {prio})\n'
                 f'\t\t(connect_pads{" yes" if "solid" in opt else ""} (clearance 0.2)) (min_thickness 0.2) (filled_areas_thickness no)\n\t\t(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.5))\n'
                 f'\t\t(polygon (pts '+" ".join(f"(xy {x:.4f} {y:.4f})" for x,y in pts)+')))')
    for i,(layer,(x0,y0,x1,y1)) in enumerate(keepouts):
        o.append(f'\t(zone (net 0) (net_name "") (layers {q(layer)}) (uuid {q(uuid.uuid5(NS,f"keepout{i}/{layer}"))}) (name "antipad") (hatch edge 0.3)\n'
                 f'\t\t(connect_pads (clearance 0)) (min_thickness 0.2) (filled_areas_thickness no)\n'
                 f'\t\t(keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))\n'
                 f'\t\t(fill (thermal_gap 0.5) (thermal_bridge_width 0.5))\n'
                 f'\t\t(polygon (pts (xy {x0:.4f} {y0:.4f}) (xy {x1:.4f} {y0:.4f}) (xy {x1:.4f} {y1:.4f}) (xy {x0:.4f} {y1:.4f}))))')
    for i,(net,layer,width,pts) in enumerate(tracks):
        if net not in ids: msgs.append(f"track {i}: no net {net} - skipped"); continue
        for j,(a,b) in enumerate(zip(pts,pts[1:])):
            if math.dist(a,b)<1e-6: continue                       # a repeated point is not a segment
            o.append(f'\t(segment (start {a[0]:.4f} {a[1]:.4f}) (end {b[0]:.4f} {b[1]:.4f}) (width {width:g}) (layer {q(layer)}) (net {ids[net]}) (uuid {q(uuid.uuid5(NS,f"trk{i}/{j}"))}))')
    for i,(net,x,y,dia,drill) in enumerate(vias):
        if net not in ids: msgs.append(f"via {i}: no net {net} - skipped"); continue
        o.append(f'\t(via (at {x:.4f} {y:.4f}) (size {dia:g}) (drill {drill:g}) (layers "F.Cu" "B.Cu") (net {ids[net]}) (uuid {q(uuid.uuid5(NS,f"via{i}"))}))')
    for i,(x,y,text,size) in enumerate(silk):
        o.append(f'\t(gr_text {q(text)} (at {x:g} {y:g} 0) (layer "F.SilkS") (uuid {q(uuid.uuid5(NS,f"silk{i}"))}) (effects (font (size {size:g} {size:g}) (thickness {size*0.15:g}))))')
    open(path,"w").write("\n".join(o)+"\n)\n")
    placed=len(fps)-len(mechanical)
    return [f"wrote {path}: {placed} of {len(c.parts)} parts, {len(mechanical)} mechanical, {len(ids)} nets"]+msgs

# ---- schematic and project ------------------------------------------------------------------------------------
# The schematic is a view, not a source: one box per part, every pin ending in a net label, no drawn wires. Symbol
# IDs are the same ref-derived IDs the board uses, so KiCad sees schematic and board as one design. Written in the
# KiCad 9 format; `kicad-cli sch upgrade` brings it to the installed version.
import json
from .core import K
_ETYPE={K.PWR_OUT:"power_out",K.PWR_IN:"power_in",K.GND:"passive",K.TX:"output",K.RX:"input",K.CLK_OUT:"output",
        K.CLK_IN:"input",K.OUT:"output",K.IN:"input",K.OD:"open_collector",K.BIDIR:"bidirectional",K.ANALOG:"passive",
        K.PASSIVE:"passive",K.NC:"no_connect",K.DNC:"no_connect"}
_G=2.54; _FONT='(effects (font (size 1.27 1.27)))'
def _u(*key): return str(uuid.uuid5(NS,"/".join(map(str,key))))
def _snap(v): return round(round(v/1.27)*1.27,4)

def _symbol(p,etype):
    """Box symbol for one part: first half of the pins down the left side, the rest down the right."""
    n=len(p.pins); left=p.pins[:(n+1)//2]; right=p.pins[(n+1)//2:]; rows=max(len(left),len(right),1)
    w=_snap(max(4*_G,math.ceil((max(len(x.name) for x in left)+max([len(x.name) for x in right] or [0])+4)*1.0/_G)*_G)); h=(rows+1)*_G
    pins=[]; pos={}
    for side,col in ((-1,left),(1,right)):
        for i,x in enumerate(col):
            px,py=side*(w/2+2*_G),_snap(h/2-(i+1)*_G); pos[x.num]=(px,py,side)
            pins.append(f'(pin {etype(x)} line (at {px:g} {py:g} {0 if side<0 else 180}) (length {2*_G}) (name {q(x.name)} {_FONT}) (number {q(x.num)} {_FONT}))')
    name=f"netspec:{p.ref}"
    body=(f'(symbol {q(name)} (pin_names (offset 0.508)) (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
          f'  (property "Reference" {q(re.sub(r"[0-9].*","",p.ref) or "U")} (at 0 {h/2+_G:g} 0) {_FONT})\n'
          f'  (property "Value" {q(p.value)} (at 0 {-h/2-_G:g} 0) {_FONT})\n'
          f'  (property "Footprint" {q(p.attrs.get("footprint",""))} (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
          f'  (symbol {q(p.ref+"_0_1")} (rectangle (start {-w/2:g} {h/2:g}) (end {w/2:g} {-h/2:g}) (stroke (width 0.254) (type default)) (fill (type background))))\n'
          f'  (symbol {q(p.ref+"_1_1")}\n    '+"\n    ".join(pins)+'))')
    return name,body,w,h,pos

_PWR_FLAG=('(symbol "netspec:PWR_FLAG" (power) (pin_names (offset 0) (hide yes)) (exclude_from_sim no) (in_bom no) (on_board no)\n'
           f'  (property "Reference" "#FLG" (at 0 1.905 0) (effects (font (size 1.27 1.27)) (hide yes)))\n  (property "Value" "PWR_FLAG" (at 0 3.81 0) {_FONT})\n'
           '  (symbol "PWR_FLAG_0_0" (pin power_out line (at 0 0 90) (length 0) (name "pwr" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27))))))\n'
           '  (symbol "PWR_FLAG_0_1" (polyline (pts (xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27)) (stroke (width 0) (type default)) (fill (type none)))))')

def export_schematic(c,path,project,label_room=22.0,sheet_h=400.0):
    """Write a label-style .kicad_sch. Parts flow down columns, big parts first. Returns messages."""
    root=_u(project,"sheet"); libs=[_PWR_FLAG]; items=[]; x=30.0; y=25.0; colw=0.0
    # KiCad's ERC allows one power output per net; paralleled sources (stack headers, a converter's SW pins) are
    # legal here and already checked by netspec, so only the first on each net keeps the type.
    first={}
    for n in c.nets.values():
        for pin in n.pins:
            if pin.kind is K.PWR_OUT: first.setdefault(n.name,pin)
    etype=lambda x: "passive" if x.kind is K.PWR_OUT and x.net and first[x.net.name] is not x else _ETYPE[x.kind]
    parts=sorted(c.parts.values(),key=lambda p:(-len(p.pins) if len(p.pins)>2 else 0,p.ref))
    for p in parts:
        name,body,w,h,pos=_symbol(p,etype); libs.append(body); cell=w+4*_G+2*label_room
        if y+h+4*_G>sheet_h and y>25.0: x+=colw+10; y=25.0; colw=0.0
        cx,cy=_snap(x+cell/2),_snap(y+h/2+2*_G); colw=max(colw,cell); y+=h+5*_G; u=_u(p.ref)
        items.append(f'(symbol (lib_id {q(name)}) (at {cx:g} {cy:g} 0) (unit 1) (exclude_from_sim no) (in_bom {"yes" if p.mpn else "no"}) (on_board yes) (dnp {"yes" if p.dnp else "no"}) (uuid {q(u)})\n'
                     f'  (property "Reference" {q(p.ref)} (at {cx:g} {_snap(cy-h/2-_G):g} 0) {_FONT})\n  (property "Value" {q(p.value)} (at {cx:g} {_snap(cy+h/2+_G):g} 0) {_FONT})\n'
                     f'  (property "Footprint" {q(p.attrs.get("footprint",""))} (at {cx:g} {cy:g} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                     f'  (property "MPN" {q(p.mpn)} (at {cx:g} {cy:g} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n  '
                     +" ".join(f'(pin {q(n)} (uuid {q(_u(p.ref,"pin",n))}))' for n in pos)
                     +f'\n  (instances (project {q(project)} (path {q("/"+root)} (reference {q(p.ref)}) (unit 1)))))')
        for pin in p.pins:
            px,py,side=pos[pin.num]; ax,ay=_snap(cx+px),_snap(cy-py)
            # global labels: a local label would name the net "/X" and the board's "X" would fail KiCad's parity check
            if pin.net: items.append(f'(global_label {q(pin.net.name)} (shape passive) (at {ax:g} {ay:g} {180 if side<0 else 0}) (effects (font (size 1.27 1.27)) (justify {"right" if side<0 else "left"})) (uuid {q(_u(p.ref,"label",pin.num))}))')
            elif pin.kind not in (K.NC,K.DNC): items.append(f'(no_connect (at {ax:g} {ay:g}) (uuid {q(_u(p.ref,"nc",pin.num))}))')
    # KiCad's ERC wants a power_out on every net with a power_in; rails fed through a fuse or inductor have none.
    fx=x+colw+20; fy=30.0; k=0
    for n in sorted(c.nets.values(),key=lambda n:n.name):
        kinds={pin.kind for pin in n.pins}
        if K.PWR_IN in kinds and K.PWR_OUT not in kinds:
            k+=1; ax,ay=_snap(fx),_snap(fy+k*6*_G)
            items.append(f'(symbol (lib_id "netspec:PWR_FLAG") (at {ax:g} {ay:g} 0) (unit 1) (exclude_from_sim no) (in_bom no) (on_board no) (dnp no) (uuid {q(_u("flag",n.name))})\n'
                         f'  (property "Reference" {q(f"#FLG{k:02d}")} (at {ax:g} {ay-1.905:g} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n  (property "Value" "PWR_FLAG" (at {ax:g} {ay-3.81:g} 0) {_FONT})\n'
                         f'  (pin "1" (uuid {q(_u("flag",n.name,"pin"))}))\n  (instances (project {q(project)} (path {q("/"+root)} (reference {q(f"#FLG{k:02d}")}) (unit 1)))))')
            items.append(f'(global_label {q(n.name)} (shape passive) (at {ax:g} {ay:g} 0) (effects (font (size 1.27 1.27)) (justify left)) (uuid {q(_u("flag",n.name,"label"))}))')
    W=math.ceil((fx+60)/10)*10
    o=[f'(kicad_sch (version 20250114) (generator "netspec") (generator_version "0.8") (uuid {q(root)}) (paper "User" {W:g} {sheet_h+30:g})',
       f'(title_block (title {q(c.name)}) (comment 1 "Generated by netspec - a view of the design, not its source"))','(lib_symbols\n'+"\n".join(libs)+')']+items
    o+=['(sheet_instances (path "/" (page "1")))','(embedded_fonts no)',')']
    open(path,"w").write("\n".join(o)+"\n")
    lib=pathlib.Path(path).with_name("netspec.kicad_sym")     # the same symbols as a library, so KiCad can resolve 'netspec:'
    lib.write_text('(kicad_symbol_lib (version 20241209) (generator "netspec") (generator_version "0.8")\n'+"\n".join(x.replace('(symbol "netspec:','(symbol "',1) for x in libs)+"\n)\n")
    return [f"wrote {path}: {len(parts)} symbols, {k} power flags",f"wrote {lib}"]

# Net classes come from what each net is, not from its name: the discipline netspec inferred, plus the rail voltage.
# Colours are (r,g,b); the PCB editor shows them once "net colours" is set to All (written to the .kicad_prl here).
# Red is left out on purpose: it is KiCad's front-copper colour, which is what every unclassed net is drawn in.
NET_CLASS_COLORS={"GND":(95,165,115),"PWR_12V":(255,90,190),"PWR_3V3":(245,160,50),"PCIE":(70,140,255),"REFCLK":(60,225,225),"SWITCH":(240,225,70)}
def net_classes(c):
    """{class name: [net names]}. PCIE and REFCLK are the length- and skew-sensitive differential pairs."""
    out={k:[] for k in NET_CLASS_COLORS}
    for n in c.nets.values():
        if not n.pins: continue
        v=round(float(n.volts),1) if n.volts is not None else None
        k={"gnd":"GND","pcie":"PCIE","hcsl":"REFCLK","switch":"SWITCH"}.get(n.disc) or ({12.0:"PWR_12V",3.3:"PWR_3V3"}.get(v) if n.disc=="rail" else None)
        if k: out[k].append(n.name)
    return out

_NC_DEFAULT={"bus_width":12,"clearance":0.2,"diff_pair_gap":0.25,"diff_pair_via_gap":0.25,"diff_pair_width":0.2,"line_style":0,"microvia_diameter":0.3,
             "microvia_drill":0.1,"name":"Default","pcb_color":"rgba(0, 0, 0, 0.000)","priority":2147483647,"schematic_color":"rgba(0, 0, 0, 0.000)",
             "track_width":0.2,"via_diameter":0.6,"via_drill":0.3,"wire_width":6}

def export_project(c,path,fp_libs=(),clearance=None,class_rules=None,colors=None,rules=None):
    """Write or update the .kicad_pro: net classes with colours (and any per-class rules in `class_rules`
    {class:{track_width:..,diff_pair_width:..,..}}), the library tables, and a .kicad_prl that turns net colours on.
    Everything else in an existing project file is left as KiCad saved it. Close the editors first - they rewrite
    the project file on exit."""
    p=pathlib.Path(path); msgs=[]; colors={**NET_CLASS_COLORS,**(colors or {})}
    d=json.loads(p.read_text()) if p.exists() else {"meta":{"filename":p.name,"version":3}}
    rgba=lambda k:"rgba(%d, %d, %d, 1.000)"%colors[k]; groups=net_classes(c)
    default={**_NC_DEFAULT,**({"clearance":clearance} if clearance else {})}
    classes=[default]+[{**default,"name":k,"pcb_color":rgba(k),"schematic_color":rgba(k),"priority":i,**(class_rules or {}).get(k,{})} for i,k in enumerate(groups)]
    d["net_settings"]={"classes":classes,"meta":{"version":4},"net_colors":None,"netclass_assignments":None,
                       "netclass_patterns":[{"netclass":k,"pattern":n} for k,v in groups.items() for n in sorted(v)]}
    if rules: d.setdefault("board",{}).setdefault("design_settings",{}).setdefault("rules",{}).update(rules)   # the fab's minimums, for KiCad's DRC
    p.write_text(json.dumps(d,indent=2)+"\n"); msgs.append(f"wrote {p}: "+", ".join(f"{k} {len(v)}" for k,v in groups.items()))
    prl=p.with_suffix(".kicad_prl"); l=json.loads(prl.read_text()) if prl.exists() else {"meta":{"filename":prl.name,"version":5}}
    l.setdefault("board",{})["net_color_mode"]=2; prl.write_text(json.dumps(l,indent=2)+"\n")      # 0 off, 1 ratsnest only, 2 all copper
    t=p.with_name("fp-lib-table")
    t.write_text("(fp_lib_table\n  (version 7)\n"+"".join(f'  (lib (name {q(pathlib.Path(d).stem)}) (type "KiCad") (uri {q("${KIPRJMOD}/"+str(d))}) (options "") (descr ""))\n' for d in fp_libs)+")\n")
    p.with_name("sym-lib-table").write_text('(sym_lib_table\n  (version 7)\n  (lib (name "netspec") (type "KiCad") (uri "${KIPRJMOD}/netspec.kicad_sym") (options "") (descr ""))\n)\n')
    return msgs+[f"wrote {t}: {len(fp_libs)} libraries",f"wrote {p.with_name('sym-lib-table')}"]
