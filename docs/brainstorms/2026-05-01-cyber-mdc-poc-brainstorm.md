---
date: 2026-05-01
topic: cyber-mdc-poc
---

# POC mode de compromission (MdC) — exemple 3 composants

## Contexte

Slides projet IMdR P23-4 (atelier 2025-09-19) introduisent le concept de **mode
de compromission (MdC)** comme extension cyber des modes de défaillance (MdD).
Slide 48 — un MdC reprend strictement le formalisme MdD (automate 2 états +
conditions d'activation + lois d'occurrence + effets) et ajoute deux choses :

1. Un MdC peut **conditionner l'occurrence d'un autre MdC** (cascade — ex.
   élévation de privilèges qui débloque l'exploitation d'une fonction service).
2. Un MdC peut **activer/exploiter** une fonction du composant pour propager
   l'attaque dans le système (et pas seulement inhiber une sortie).

L'exemple cible (slides 49-56) : trois composants `Alim. élec.` → `Serveur` →
`Process industriel`, avec :

- Un `MdC_A` sur le serveur (élévation de privilèges).
- Un `MdC_B` sur le serveur (exploitation d'une fonction service dormante),
  conditionné par `MdC_A` actif.
- Un `MdC` sur le process industriel, déclenché par la propagation de la
  fonction service compromise depuis le serveur, qui inhibe les deux fonctions
  fonctionnelles du process.

## What We're Building

Un exemple isolé `examples/isimu/cyber_3comp.py` qui :

1. Reproduit l'architecture exacte de l'exemple slides 49-56 en muscadet
   (composants + flux fonctionnels + flux service dormant).
2. Modélise les trois MdC à l'aide de `cod3s.ObjFMDelay` (lois déterministes,
   pour que la timeline soit lisible en stepping `isimu`).
3. Implémente la cascade MdC_A → MdC_B via une `failure_cond` callable qui
   inspecte l'état d'automate de MdC_A.
4. Modélise la propagation MdC_B → MdC_process via un flux service muscadet
   classique : MdC_B active la fonction service du serveur (effet de type
   "exploitation"), le flux se propage vers le process, et le `failure_cond`
   du MdC_process surveille l'entrée correspondante (motif structuré
   `[[{"attr": "f_service_fed_in", "value": True}]]`).
5. Expose `build() -> muscadet.System` (factory `cod3s-isimu --factory`) et
   `run()` (stepping scripté Python sans dépendance TUI), comme les autres
   exemples du dossier `examples/isimu/`.

## Why This Approach (option α)

**Hypothèse confirmée** : `cod3s.ObjFM*` couvre 100 % de la formalisation MdC
du slide 48. Aucun nouveau type d'objet à introduire pour ce POC.

| Besoin MdC | cod3s.ObjFM | Validé |
|---|---|---|
| Automate 2 états | `add_aut2st` interne | ✅ |
| Loi d'occurrence | `ObjFMExp` / `ObjFMDelay` | ✅ |
| Effet "inhibition" | `failure_effects={"v": False}` | ✅ |
| Effet "exploitation" | `failure_effects={"v": True}` | ✅ (mêmes mécaniques) |
| Cascade A → B | `failure_cond=callable` inspectant l'automate de A | ✅ |
| Propagation par flux | flux muscadet existants (FlowOut → FlowIn) | ✅ |

L'**option α** (FlowOut dormant + effet d'activation) garde le POC dans le
formalisme muscadet vanilla : la fonction service est un `FlowOut` ordinaire
dont la valeur reste à `False` tant qu'aucun MdC ne l'active. C'est exactement
le miroir conceptuel d'un MdD — au lieu de mettre `var_fed_available=False`
(inhibition), le MdC met une variable d'activation à `True`
(exploitation).

Approches considérées et rejetées pour le POC :

- **Option β (FlowOutOnTrigger)** : sémantique "trigger" plus explicite mais
  câblage en plus, et la trigger var doit être maintenue à jour par le MdC →
  indirection inutile pour un POC.
- **Option γ (classe dédiée `ObjCompromiseMode`)** : ajout d'une abstraction
  cyber explicite. Pertinent si on industrialise, hors-scope POC.

## Key Decisions

- **Lois d'occurrence : déterministes (`ObjFMDelay`)**. Permet de stepper la
  timeline avec `isimu` ou `python -m`. Les délais représentent la
  "difficulté" de la compromission au sens du slide 48.
- **Cascade MdC_A → MdC_B via `failure_cond=callable`**. Plus direct que
  passer par une variable intermédiaire ; le callable inspecte
  `mdc_a_comp.automata_d[fm_name].get_state_by_name("occ")._bkd.isActive()`.
- **Cascade MdC_B → MdC_process via flux muscadet**. Le serveur a un FlowOut
  `f_service` dormant ; MdC_B met `f_service` actif ; le process a un
  FlowIn `f_service` et son MdC surveille `f_service_fed_in == True` via la
  forme structurée `failure_cond=[[{"attr": "...", "value": True}]]`.
  Permet d'illustrer **deux mécanismes de cascade** différents dans le même
  exemple (état d'automate vs propagation par flux).
- **Pas de MdD préexistant dans cette première itération**. On se concentre
  sur la chaîne cyber pour lisibilité ; les MdD du slide 50 pourront être
  rajoutés ensuite si pertinent.
- **Localisation : `examples/isimu/cyber_3comp.py`**. Cohérent avec les 4
  factories isimu existantes ; même conventions de `build()` / `run()` /
  `_snapshot()`.

## Timeline scriptée pour le stepping

Délais choisis pour produire une séquence courte et lisible :

```
t=0:    tout normal — Process produit, f_service inactive
t=10:   MdC_A activé           (élévation de privilèges réussie)
t=15:   MdC_B activé           (exploitation : f_service active sur Serveur)
        propagation flux       (Process voit f_service_fed_in passer à True)
t=23:   MdC_process activé     (perte des deux fonctions du Process)
```

## Open Questions

- Faut-il dans cette même session prévoir un test pytest miroir
  (`tests/test_cyber_3comp.py`) pour figer les invariants de la cascade, ou
  laisser ça à une session ultérieure ? *(par défaut : pas de test, c'est un
  exemple pédagogique).*
- Si le POC s'avère concluant, vaut-il la peine d'ajouter une note dans le
  README `examples/isimu/` ou de lancer un brainstorm séparé pour le module
  `muscadet.kb.cyber` (option γ) ?

## Next Steps

→ `/workflows:plan` pour le plan d'implémentation détaillé (structure des
classes de composants, signature des `failure_effects`/`failure_cond`,
séquence des transitions à stepper dans `run()`).
