// Slides pédagogiques — modes de compromission cyber dans muscadet
//
// Compilation::
//
//   typst compile docs/presentations/cyber_examples.typ
//
// Couvre les deux exemples développés dans `examples/isimu/`:
// - cyber_3comp : cascade de MdC sur 3 composants
// - power_plant : centrale avec redondance froide + chaîne d'attaque cyber

#import "@preview/touying:0.6.1": *
#import themes.simple: *
#import "@preview/fletcher:0.5.7" as fletcher: diagram, node, edge

#show: simple-theme.with(
  aspect-ratio: "16-9",
  config-info(
    title: [Modélisation cyber dans muscadet],
    subtitle: [Modes de défaillance (MdD) et modes de compromission (MdC)],
    author: [muscadet pedagogical examples],
    date: datetime(year: 2026, month: 5, day: 3),
  ),
)

#set text(size: 18pt)
#show raw: set text(size: 0.85em)

// ─── Helpers ────────────────────────────────────────────────────────────────

#let active = green.darken(20%)
#let inactive = red.darken(10%)
#let cyber = purple.darken(10%)
#let neutral = blue.darken(20%)

#let badge(label, on, color: red) = {
  box(
    stroke: 0.6pt + color,
    fill: if on { color.lighten(60%) } else { white },
    inset: (x: 4pt, y: 2pt),
    radius: 3pt,
    text(size: 9pt, weight: "bold", fill: if on { color.darken(20%) } else { gray },
         label + (if on { "·ACT" } else { "·idle" })),
  )
}

#let comp_node(pos, label, alive: true, kind: "physical", name: none) = {
  let border = if alive { active } else { inactive }
  let bg = if kind == "logical" { blue.lighten(85%) } else { white }
  if name == none {
    node(pos, label, stroke: 1.2pt + border, fill: bg, corner-radius: 4pt, inset: 6pt)
  } else {
    node(pos, label, stroke: 1.2pt + border, fill: bg, corner-radius: 4pt, inset: 6pt, name: name)
  }
}

#let flow_edge(from, to, label, alive: true, is_dormant: false, bend: 0deg) = {
  let c = if is_dormant { gray } else if alive { active } else { inactive }
  let stroke_def = if is_dormant {
    (paint: c, thickness: 1pt, dash: "dashed")
  } else {
    1pt + c
  }
  edge(from, to, "->", text(size: 7pt, fill: c, label),
       stroke: stroke_def,
       label-pos: 0.5,
       bend: bend)
}

// Architecture cyber_3comp réutilisable
#let arch_3comp(f_orange: true, f_blue: true, f_service: false, proc_alive: true, mark_dormant_service: true) = {
  diagram(
    spacing: (3.5em, 2em),
    node-stroke: 1pt,
    comp_node((0, 0), "Alim", name: <alim>),
    comp_node((2, 0), "Serveur", name: <srv>),
    comp_node((4, 0), "Process", alive: proc_alive, name: <proc>),
    flow_edge(<alim>, <srv>, [power]),
    flow_edge(<srv>, <proc>, [f_orange], alive: f_orange, bend: -25deg),
    flow_edge(<srv>, <proc>, [f_blue], alive: f_blue),
    flow_edge(<srv>, <proc>, [f_service],
              alive: f_service,
              is_dormant: mark_dormant_service and not f_service,
              bend: 25deg),
  )
}

// Architecture power_plant réutilisable
#let arch_pp(pa: true, pb: false, plant: true) = {
  diagram(
    spacing: (3em, 1.6em),
    node-stroke: 1pt,
    comp_node((0, 1), "Grid", name: <grid>),
    comp_node((2, 0), "PumpA", alive: pa, name: <pa>),
    comp_node((2, 2), "PumpB", alive: pb, name: <pb>),
    comp_node((4, 1), "Plant", alive: plant, name: <plant>),
    flow_edge(<grid>, <pa>, [power]),
    flow_edge(<grid>, <pb>, [power]),
    flow_edge(<grid>, <plant>, [power]),
    flow_edge(<pa>, <plant>, [cool], alive: pa),
    flow_edge(<pb>, <plant>, [cool], alive: pb, is_dormant: not pb),
  )
}

// ─── Title ──────────────────────────────────────────────────────────────────

#title-slide[
  #text(size: 32pt, weight: "bold")[Modélisation cyber dans muscadet]

  #v(0.5em)
  #text(size: 16pt)[
    Modes de défaillance (MdD) et modes de compromission (MdC)\
    illustrations pas-à-pas
  ]

  #v(1.5em)
  #text(size: 14pt)[
    Deux exemples : `cyber_3comp` et `power_plant`
  ]

  #v(2em)
  #text(size: 11pt, fill: gray)[
    docs/presentations/cyber_examples.typ — 2026-05-03
  ]
]

// ─── Contexte ───────────────────────────────────────────────────────────────

== Contexte — MdD étendu en MdC

#text(size: 13pt)[
  #grid(
    columns: (1fr, 1fr),
    gutter: 1em,
    [
      *Mode de défaillance (MdD)*

      Automate à 2 états : `rep` ↔ `occ`

      - condition d'activation
      - loi d'occurrence (Exp, Delay…)
      - effets sur les sorties (inhibition)
    ],
    [
      *Mode de compromission (MdC)*

      Mêmes briques + deux ajouts :

      - cascade : cond. basée sur l'état d'un *autre* MdC
      - effets d'*activation* (exploitation de fonction service)
    ],
  )

  #v(0.4em)
  Implémentation : `cod3s.ObjFMExp` / `ObjFMDelay` — pas de nouvelle classe muscadet.
]

== Deux mécanismes de cascade

#text(size: 13pt)[
  #grid(
    columns: (1fr, 1fr),
    gutter: 1em,
    [
      *Intra-composant — par état d'automate*

      ```yaml
      failure_cond:
        - - attr: occ
            obj: Srv__mdc_a
            value: true
      ```

      MdC B s'active quand l'automate du MdC A est en `occ`.
      Pas de propagation visible.
    ],
    [
      *Inter-composant — par flux*

      ```yaml
      failure_cond:
        - - attr: f_service_fed_in
            value: true
      ```

      MdC s'active quand un flux *physique* arrive en entrée.
      Traverse les message-boxes muscadet.
    ],
  )
]

// ─── Exemple 1 : cyber_3comp ────────────────────────────────────────────────

== Exemple 1 — cyber_3comp · architecture

#align(center)[#arch_3comp()]

#text(size: 13pt)[
  - *Alim* — source d'alimentation (toujours active sous nominal)
  - *Serveur* — `f_orange`, `f_blue` (mission) + `f_service` (dormante)
  - *Process* — produit `F1`, `F2` (les fonctions cibles)
]

== Exemple 1 · vulnérabilités cyber identifiées

#text(size: 14pt)[
  Sur le serveur (analyse cyber, slides IMdR P23-4) :

  + *MdC_A* — élévation de privilèges (pas d'effet flux)
  + *MdC_B* — exploitation de la *fonction service*, gated par MdC_A
  + *MdC_proc* — sur le process, inhibe `F1` et `F2`, gated par `f_service`
]

#v(0.5em)

#align(center)[
  #diagram(
    spacing: (2.5em, 1.5em),
    node-stroke: 1pt,
    node((0, 0), [MdC_A\ priv. esc.], stroke: 1pt + cyber, corner-radius: 4pt, inset: 6pt, name: <a>),
    node((1, 0), [MdC_B\ exploit], stroke: 1pt + cyber, corner-radius: 4pt, inset: 6pt, name: <b>),
    node((2, 0), [MdC_proc\ inhibit], stroke: 1pt + cyber, corner-radius: 4pt, inset: 6pt, name: <c>),
    edge(<a>, <b>, "->", text(size: 8pt, [`occ`]), stroke: 1pt + cyber),
    edge(<b>, <c>, "->", text(size: 8pt, [`f_service`]), stroke: 1pt + active),
  )
]

== Exemple 1 · étape 0 — nominal ($t = 0$)

#align(center)[#arch_3comp()]

#align(center)[
  #badge("MdC_A", false) #h(0.4em)
  #badge("MdC_B", false) #h(0.4em)
  #badge("MdC_proc", false)
  #h(2em)
  #text(fill: active, weight: "bold")[F1 = 1] #h(0.7em)
  #text(fill: active, weight: "bold")[F2 = 1]
]

#text(size: 11pt, fill: gray)[
  Tous les flux opérationnels sont alimentés. `f_service` est *dormante*.
]

== Exemple 1 · étape 1 — MdC_A active ($t = 10$)

#align(center)[#arch_3comp()]

#align(center)[
  #badge("MdC_A", true) #h(0.4em)
  #badge("MdC_B", false) #h(0.4em)
  #badge("MdC_proc", false)
  #h(2em)
  #text(fill: active, weight: "bold")[F1 = 1] #h(0.7em)
  #text(fill: active, weight: "bold")[F2 = 1]
]

#text(size: 11pt, fill: gray)[
  L'élévation de privilèges réussit. *Aucun effet flux* — MdC_B peut désormais s'amorcer.
]

== Exemple 1 · étape 2 — MdC_B active : f_service exploitée ($t = 15$)

#align(center)[#arch_3comp(f_service: true, mark_dormant_service: false)]

#align(center)[
  #badge("MdC_A", true) #h(0.4em)
  #badge("MdC_B", true) #h(0.4em)
  #badge("MdC_proc", false)
  #h(2em)
  #text(fill: active, weight: "bold")[F1 = 1] #h(0.7em)
  #text(fill: active, weight: "bold")[F2 = 1]
]

#text(size: 11pt, fill: gray)[
  L'effet `failure_effects: { f_service_prod_available: true }` *active*
  le flux service. Il se propage jusqu'au process.
]

== Exemple 1 · étape 3 — MdC_proc : inhibition F1/F2 ($t = 23$)

#align(center)[#arch_3comp(f_service: true, proc_alive: false, mark_dormant_service: false)]

#align(center)[
  #badge("MdC_A", true) #h(0.4em)
  #badge("MdC_B", true) #h(0.4em)
  #badge("MdC_proc", true)
  #h(2em)
  #text(fill: inactive, weight: "bold")[F1 = 0] #h(0.7em)
  #text(fill: inactive, weight: "bold")[F2 = 0]
]

#text(size: 11pt, fill: gray)[
  L'arrivée de `f_service` sur le process active la condition de MdC_proc.
  Effet : inhibition de `F1` et `F2`. Événement redouté atteint.
]

== Exemple 1 · analyse de séquences (`run-cod3s-study`)

Sortie `sequences.xml` (déterministe, 1 séquence, $P=1$) :

#text(size: 13pt)[
  ```xml
  <SEQ N="1" P="1" C="process_F1_lost">
      <BR T="10">  <TR NAME="Srv__mdc_a.occ"     ... /></BR>
      <BR T="15">  <TR NAME="Srv__mdc_b.occ"     ... /></BR>
      <BR T="23">  <TR NAME="Proc__mdc_proc.occ" ... /></BR>
  </SEQ>
  ```
]

#v(0.5em)
#text(size: 13pt)[
  Cascade extraite par `Pyc.CAnalyser` à partir de
  `Plant.electricity_fed_out == 0`. Une variante probabilisée
  (`cyber_3comp_study_exp.yaml`) ajoute des MdD avec lois Exp.
]

// ─── Exemple 2 : power_plant ────────────────────────────────────────────────

== Exemple 2 — power_plant · architecture

#grid(
  columns: (3fr, 2fr),
  gutter: 1em,
  align: (center + horizon, left + horizon),
  arch_pp(),
  text(size: 12pt)[
    *Grid* alimente tout. *Plant* a besoin\
    de power AND cooling.\
    \
    *PumpA* : pompe principale.\
    *PumpB* : pompe de secours via\
    `FlowOutOnTrigger` — démarre quand\
    `PumpA.cooling_out` tombe.\
    \
    *HMI* : poste opérateur, sans flux,\
    héberge la chaîne d'attaque IT.
  ],
)

== Exemple 2 · redondance froide via `FlowOutOnTrigger`

#text(size: 12pt)[
  `PumpB` est inactive sous nominal. Son output `cooling` est un
  `FlowOutOnTrigger` dont le trigger est branché sur `PumpA.cooling_out`.

  ```python
  class BackupPump(muscadet.ObjFlow):
      def add_flows(self, **kwargs):
          super().add_flows(**kwargs)
          self.add_flow_in(name="power", logic="and")
          self.add_flow_out_on_trigger(
              name="cooling",
              trigger_time_up=0, trigger_time_down=0,
              trigger_logic="and",
              var_prod_default=True,
              var_prod_cond=[["power"]],   # garde: power requis
          )
  ```
]

== Exemple 2 · MdD + chaîne cyber (4 étapes)

#text(size: 13pt)[
  #grid(
    columns: (1fr, 1fr),
    gutter: 1em,
    [
      *MdD hardware (lois Exp)*

      - `hw_grid` (panne secteur)
      - `hw_pumpA` (pompe principale)
      - `hw_pumpB` (pompe secours)

      Chacun met `*_fed_available_out=False`.
    ],
    [
      *MdC kill chain*

      + `mdc_phishing` (HMI)
      + `mdc_lateral_movement` (HMI)
      + `mdc_disable_main_pump` (PumpA)
      + `mdc_inhibit_backup_pump` (PumpB)

      Étapes 3 et 4 : *concourent* sur `lateral.occ`.
    ],
  )

  #v(0.4em)
  *Enjeu* : la redondance protège contre les pannes *indépendantes*. Une
  attaque *coordonnée* peut désactiver les deux pompes en parallèle.
]

== Scénario A · `hw_pumpA` seul · A.1 nominal ($t=0$)

#align(center)[#arch_pp()]

#align(center)[
  #badge("hw_pumpA", false)
  #h(2em)
  #text(fill: active, weight: "bold", size: 16pt)[Plant.elec = 1]
]

== Scénario A · A.2 ($t=8$) — `hw_pumpA` fire — *glitch*

#align(center)[#arch_pp(pa: false, pb: false, plant: false)]

#align(center)[
  #badge("hw_pumpA", true)
  #h(2em)
  #text(fill: inactive, weight: "bold", size: 16pt)[elec = 0 (transitoire)]
]

#text(size: 11pt, fill: gray)[
  Perte de cooling propagée à `Plant.cooling_in` puis `Plant.electricity_fed_out`.
  *Le trigger n'a pas encore fired* — il est traité en événement séparé.
]

== Scénario A · A.3 ($t=8$, après trigger) — *redondance assurée*

#align(center)[#arch_pp(pa: false, pb: true)]

#align(center)[
  #badge("hw_pumpA", true)
  #h(2em)
  #text(fill: active, weight: "bold", size: 16pt)[elec = 1 (rétablie)]
]

#text(size: 11pt, fill: gray)[
  `cooling_trigger_up` fire, `PumpB.cooling=True`, `Plant.cooling_in`
  (logic="or") reste alimenté. *La redondance fonctionne.*
]

== Scénario B · kill chain · B.1 phishing ($t=10$)

#align(center)[#arch_pp()]

#align(center)[
  #badge("phishing", true, color: cyber) #h(0.3em)
  #badge("lateral", false, color: cyber) #h(0.3em)
  #badge("disable_PA", false, color: cyber) #h(0.3em)
  #badge("inhibit_PB", false, color: cyber)
  #h(1.5em)
  #text(fill: active, weight: "bold")[elec = 1]
]

#text(size: 11pt, fill: gray)[
  MdC_A active sur HMI. État seul, aucun effet flux.
]

== Scénario B · B.2 ($t=15$) — pivot vers OT

#align(center)[#arch_pp()]

#align(center)[
  #badge("phishing", true, color: cyber) #h(0.3em)
  #badge("lateral", true, color: cyber) #h(0.3em)
  #badge("disable_PA", false, color: cyber) #h(0.3em)
  #badge("inhibit_PB", false, color: cyber)
  #h(1.5em)
  #text(fill: active, weight: "bold")[elec = 1]
]

#text(size: 11pt, fill: gray)[
  `mdc_lateral_movement` active. Son `occ` débloque les deux exploits OT
  en parallèle.
]

== Scénario B · B.3 ($t=18$) — exploit + bascule

#align(center)[#arch_pp(pa: false, pb: true)]

#align(center)[
  #badge("phishing", true, color: cyber) #h(0.3em)
  #badge("lateral", true, color: cyber) #h(0.3em)
  #badge("disable_PA", true, color: cyber) #h(0.3em)
  #badge("inhibit_PB", false, color: cyber)
  #h(1.5em)
  #text(fill: active, weight: "bold")[elec = 1 (PumpB)]
]

#text(size: 11pt, fill: gray)[
  Le trigger fire au même instant simulation — la redondance bascule.
]

== Scénario B · B.4 ($t=19$) — *redondance défaite*

#align(center)[#arch_pp(pa: false, pb: false, plant: false)]

#align(center)[
  #badge("phishing", true, color: cyber) #h(0.3em)
  #badge("lateral", true, color: cyber) #h(0.3em)
  #badge("disable_PA", true, color: cyber) #h(0.3em)
  #badge("inhibit_PB", true, color: cyber)
  #h(1.5em)
  #text(fill: inactive, weight: "bold")[elec = 0 (durable)]
]

#text(size: 11pt, fill: gray)[
  `mdc_inhibit_backup_pump` éteint PumpB. Plus de cooling possible →
  *l'attaque coordonnée a défait la redondance*.
]

== Scénario C · cyber *sans* `inhibit_backup_pump`

#align(center)[#arch_pp(pa: false, pb: true)]

#align(center)[
  #badge("phishing", true, color: cyber) #h(0.3em)
  #badge("lateral", true, color: cyber) #h(0.3em)
  #badge("disable_PA", true, color: cyber)
  #h(1.5em)
  #text(fill: active, weight: "bold")[elec = 1 (régime permanent)]
]

#text(size: 11pt, fill: gray)[
  Sans inhibit_backup, PumpB compense. *La redondance tient.* Pin testé
  par `tests/test_power_plant_example.py::test_scenario_C_*`.
]

== Note pédagogique — *transitoire* vs *régime permanent*

#text(size: 13pt)[
  #grid(
    columns: (1fr, 1fr),
    gutter: 1em,
    [
      *Transitoire (instant simu)*

      Le `trigger_up` est *séparé* du déclencheur, même à
      `trigger_time_up=0`. Entre les deux, `elec` lit *brièvement* `False`.

      L'analyse Monte Carlo s'arrête au premier `elec=False` → *capture
      le glitch*.
    ],
    [
      *Régime permanent*

      Après que tous les événements simultanés se soient résolus, la
      redondance restaure `elec=True`.

      Filtrer les séquences "perte durable" → contiennent
      `inhibit_backup`, `hw_pumpA + hw_pumpB`, ou `hw_grid` seul.
    ],
  )

  #v(0.4em)
  Tests pytest : *figent les comportements steady-state* des trois
  scénarios A, B, C.
]

== Étude probabilisée — top des séquences (1000 tirages)

#text(size: 12pt)[
  #table(
    columns: (auto, 1fr, auto),
    align: (center, left, center),
    stroke: 0.4pt + gray,
    inset: 4pt,
    table.header([*P*], [*Séquence*], [*Catégorie*]),
    [0.290], [`phishing → lateral → disable_main_pump`], text(fill: orange)[transitoire],
    [0.270], [`hw_pumpA` seul], text(fill: orange)[transitoire],
    [*0.131*], [*`phishing → lateral → inhibit_backup → disable_main`*], text(fill: inactive)[*durable*],
    [0.063], [`hw_grid`], text(fill: inactive)[durable],
    [0.050], [`phishing → hw_pumpA`], text(fill: orange)[transitoire],
    [0.035], [`hw_pumpB → phishing → lateral → disable_main`], text(fill: orange)[transitoire],
    [0.029], [`hw_pumpB → hw_pumpA`], text(fill: inactive)[durable],
    [0.014], [`hw_pumpB` seul (cible non atteinte)], text(fill: active)[redondance OK],
  )

  *Perte durable* ≈ 0.131 + 0.063 + 0.029 ≈ 22 %.
  La cyber kill chain *complète* représente l'essentiel du risque durable.
]

// ─── Conclusion ─────────────────────────────────────────────────────────────

== Conclusion

#text(size: 13pt)[
  #grid(
    columns: (1fr, 1fr),
    gutter: 1em,
    [
      *Le formalisme MdC = MdD étendu*

      100 % couvert par `cod3s.ObjFM*`. Aucune nouvelle classe muscadet.

      Deux mécanismes de cascade :
      - état d'automate (intra)
      - propagation de flux (inter)
    ],
    [
      *L'enjeu sûreté/sécurité illustré*

      La redondance protège des pannes *indépendantes*. Une attaque
      *coordonnée* la défait.

      L'analyse de séquences quantifie les *contributions* MdD vs MdC à
      un même événement redouté.
    ],
  )

  #v(0.5em)

  Trois usages du même modèle muscadet :
  `python -m examples.isimu.X`, `cod3s-isimu --factory X:build`,
  `run-cod3s-study --model M --study-specs S`.
]

== Pour aller plus loin

#text(size: 13pt)[
  - *Sources* : `examples/isimu/cyber_3comp.py`, `power_plant.py`, et
    leurs YAML model + study.
  - *Tests* : `tests/test_power_plant_example.py` fige les comportements
    pédagogiques A, B, C.
  - *Sortie de l'analyse* : `sequences.xml` produit par
    `run-cod3s-study`, rendu HTML automatique via `PySeq.xsl`.
  - *Slides IMdR P23-4* : formalisation du concept de MdC et de la cascade
    (slides 38-58 de l'atelier 2025-09-19).
]

#v(2em)

#align(center)[
  #text(size: 22pt, weight: "bold")[Questions ?]
]
