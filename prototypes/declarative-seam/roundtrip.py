"""PROTOTYPE, jetable. Question : la semantique continue de muscadet
se laisse-t-elle decrire en donnees ?

Protocole : l'installation d'electrolyse H2 (4 composants, capacites, recettes,
deratage, balayage de capability, un mode de defaillance a delai) est decrite
ENTIEREMENT en JSON, reconstruite depuis ce seul JSON, et les trajectoires sont
comparees. Rien n'est concluant tant que les nombres ne coincident pas.

Deux processus, parce que PyCATSHOO interdit plus d'un systeme par processus.
Usage : roundtrip.py ref | roundtrip.py rebuild | roundtrip.py compare
"""
import json, sys, pathlib

OUT = pathlib.Path("/tmp/cod3s-debug/proto")
H2_CONS = {"H2O": 4, "Elec": 1}
H2_PROD = {"H2": 1, "O2": 1}
SCHEDULE = {"nb_runs": 1, "schedule": [{"start": 0, "end": 5, "nvalues": 6}]}
OBSERVED = [("Electro", "H2_fed_out"), ("Electro", "O2_fed_out"),
            ("Electro", "H2O_demand_out"), ("Electro", "Elec_demand_out"),
            ("Electro", "H2O_fed_in"), ("Electro", "Elec_fed_in"),
            ("Local", "tank_qty_H2"), ("B1", "battery_qty_Elec"),
            ("B1", "Elec_capability_out"), ("S_H2O", "H2O_fed_out")]
ORDER = ["S_H2O", "B1", "Electro", "Local"]


def wire(s, with_failure_mode=True):
    """Ce que la declaration de COMPOSANT ne porte pas : le niveau SYSTEME.

    Le mode de defaillance, lui, EST porte par la declaration du composant :
    a la reconstruction il est deja la, d'ou le drapeau.
    """
    s.connect_flow(source="S_H2O", target="Electro", flow_name="H2O")
    s.connect_flow(source="B1", target="Electro", flow_name="Elec")
    s.connect_flow(source="Electro", target="Local", flow_name="H2")
    if with_failure_mode:
        s.comp["Electro"].add_delay_failure_mode(
            name="df_H2", failure_time=2, repair_time=2, failure_effects=[(".*", 0.0)])
    for comp, var in OBSERVED:
        s.add_indicator_var(component=f"^{comp}$", var=f"^{var}$", stats=["mean"])


def run(s):
    s.simulate(SCHEDULE)
    out = {}
    for r in s.indic_to_frame().to_dict("records"):
        if r["stat"] == "mean":
            out.setdefault(r["name"], {})[r["instant"]] = round(float(r["values"]), 9)
    return {k: [v[i] for i in sorted(v)] for k, v in out.items()}


def do_ref():
    import muscadet, muscadet.kb.continuous  # noqa: F401
    from muscadet.declare import component_spec, check_spec
    print(f"muscadet {getattr(muscadet, '__version__', '?')}")
    s = muscadet.System(name="H2Ref")
    s.add_component(name="S_H2O", cls="SourceContinuous", flow="H2O", rate=2)
    s.add_component(name="B1", cls="CapacityContinuous", flow="Elec", ports="out",
                    capacity=100, content_init={"Elec": 100}, capacity_name="battery")
    s.add_component(name="Electro", cls="TransformerContinuous",
                    flows_in=list(H2_CONS), flows_out=list(H2_PROD),
                    rules=[dict(name="electrolysis", cons=H2_CONS, prod=H2_PROD)])
    s.add_component(name="Local", cls="CapacityContinuous", flow="H2", ports="both",
                    capacity=6, content_init={"H2": 3}, capacity_name="tank", fill_rate=1)
    wire(s)

    specs = {n: component_spec(c) for n, c in s.comp.items()}
    blob = json.dumps(specs, indent=1)          # 1. est-ce des DONNEES ?
    for n, sp in specs.items():
        check_spec(sp)                          # 2. la declaration se relit-elle ?
    (OUT / "specs.json").write_text(blob)
    (OUT / "ref.json").write_text(json.dumps(run(s)))
    print(f"specs serialisees : {len(blob)} octets ; trajectoires de reference ecrites")


def do_rebuild():
    import muscadet, muscadet.kb.continuous  # noqa: F401
    from muscadet.declare import build_component
    specs = json.loads((OUT / "specs.json").read_text())
    s = muscadet.System(name="H2Rebuilt")
    for name in ORDER:                          # 3. reconstruire depuis le JSON SEUL
        build_component(s, specs[name])
    wire(s, with_failure_mode=False)
    (OUT / "rebuilt.json").write_text(json.dumps(run(s)))
    print("systeme reconstruit depuis le JSON, trajectoires ecrites")


def do_compare():
    a = json.loads((OUT / "ref.json").read_text())
    b = json.loads((OUT / "rebuilt.json").read_text())
    ok = True
    for k in sorted(set(a) | set(b)):
        same = a.get(k) == b.get(k)
        ok &= same
        print(f"  {'OK   ' if same else 'ECART'} {k}")
        if same:
            print(f"         {a.get(k)}")
        else:
            print(f"         reference  : {a.get(k)}")
            print(f"         reconstruit: {b.get(k)}")
    print("\nVERDICT :", "indiscernables" if ok else "DIVERGENCE")


{"ref": do_ref, "rebuild": do_rebuild, "compare": do_compare}[sys.argv[1]]()
