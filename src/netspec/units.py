"""Typed quantities, after psucalc: V(3.3), mA(660), effc(0.9). A volt cannot be compared to an amp."""
class Q(float):
    _MUL={("V","A"):"W",("A","ohm"):"V",("W","degC/W"):"degC"}
    _DIV={("W","V"):"A",("W","A"):"V",("V","A"):"ohm",("V","ohm"):"A",("degC","W"):"degC/W"}
    def __new__(cls,x,unit): o=float.__new__(cls,x); o.unit=unit; return o
    def _same(s,o,op):
        if isinstance(o,Q):
            if o.unit!=s.unit: raise TypeError(f"unit mismatch: {s!r} {op} {o!r}")
            return float(o)
        if o==0: return 0.0                                  # lets sum() and 'x > 0' work
        raise TypeError(f"bare number in {s!r} {op} {o!r}; wrap it in a unit")
    def __add__(s,o): return Q(float(s)+s._same(o,"+"),s.unit)
    __radd__=__add__
    def __sub__(s,o): return Q(float(s)-s._same(o,"-"),s.unit)
    def __neg__(s): return Q(-float(s),s.unit)
    def __abs__(s): return Q(abs(float(s)),s.unit)
    def __mul__(s,o):
        if not isinstance(o,Q): return Q(float(s)*o,s.unit)
        u=Q._MUL.get((s.unit,o.unit)) or Q._MUL.get((o.unit,s.unit))
        if not u: raise TypeError(f"no rule for {s.unit}*{o.unit}")
        return Q(float(s)*float(o),u)
    __rmul__=__mul__
    def __truediv__(s,o):
        if not isinstance(o,Q): return Q(float(s)/o,s.unit)
        if o.unit==s.unit: return float(s)/float(o)          # ratio -> plain number
        u=Q._DIV.get((s.unit,o.unit))
        if not u: raise TypeError(f"no rule for {s.unit}/{o.unit}")
        return Q(float(s)/float(o),u)
    def __eq__(s,o): return isinstance(o,Q) and o.unit==s.unit and float(o)==float(s)
    def __ne__(s,o): return not s==o
    def __hash__(s): return hash((float(s),s.unit))
    def __lt__(s,o): return float(s)< s._same(o,"<")
    def __le__(s,o): return float(s)<=s._same(o,"<=")
    def __gt__(s,o): return float(s)> s._same(o,">")
    def __ge__(s,o): return float(s)>=s._same(o,">=")
    def __repr__(s):
        x=float(s)
        if s.unit in ("degC","degC/W"): return f"{x:.1f} {s.unit}"
        for f,p in ((1e6,"M"),(1e3,"k"),(1,""),(1e-3,"m"),(1e-6,"u"),(1e-9,"n"),(1e-12,"p")):
            if abs(x)>=f or f==1e-12: return f"{x/f:.3g} {p}{s.unit}" if x else f"0 {s.unit}"
    __str__=__repr__
def _u(unit,scale=1.0): return lambda x: Q(x*scale,unit)
V,mV=_u("V"),_u("V",1e-3); A,mA=_u("A"),_u("A",1e-3); W,mW=_u("W"),_u("W",1e-3)
ohm,k,M=_u("ohm"),_u("ohm",1e3),_u("ohm",1e6); uF,nF,pF=_u("F",1e-6),_u("F",1e-9),_u("F",1e-12)
degC,C_per_W=_u("degC"),_u("degC/W")
uH,nH=_u("H",1e-6),_u("H",1e-9); kHz,MHz=_u("Hz",1e3),_u("Hz",1e6)
mm,mil,um=_u("m",1e-3),_u("m",25.4e-6),_u("m",1e-6)
effc=duty=derate=float                                      # dimensionless, named for readability
