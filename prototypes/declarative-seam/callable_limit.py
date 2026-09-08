"""PROTOTYPE, jetable. La limite : que fait la declaration face a une
fonction Python dans le modele ? Elle doit REFUSER, jamais laisser tomber
en silence : un composant qui se declare en perdant sa regle d'allocation
serait reconstruit avec un comportement different, sans un mot."""
import json
import muscadet, muscadet.kb.continuous  # noqa: F401
from muscadet.declare import component_spec

s = muscadet.System(name="CallableProbe")
s.add_component(name="Src", cls="SourceContinuous", flow="E", rate=10)

comp = s.comp["Src"]
port = comp.flows_out["E"]
port.allocation_fun = lambda demands, available: {k: available / len(demands) for k in demands}
print("fonction Python posee sur l'allocation de la sortie")

try:
    spec = component_spec(comp)
except Exception as e:
    print(f"REFUS a la declaration : {type(e).__name__}: {e}")
else:
    try:
        json.dumps(spec)
        print("SILENCE : la declaration passe ET se serialise")
    except TypeError as e:
        print(f"REFUS a la serialisation : {e}")
    flows = spec.get("flows") or []
    out = [f for f in flows if f.get("name") == "E" and f.get("direction") == "out"]
    print("  la cle allocation_fun est-elle dans la declaration ?",
          any("allocation_fun" in (f or {}) for f in out))
