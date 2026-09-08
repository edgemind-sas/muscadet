# PROTOTYPE, jetable : la couture déclarative

**Ce répertoire n'est pas du code de production.** Il existe pour répondre à une
question, et il l'a fait. Il est conservé comme source primaire.

## La question

La sémantique continue de muscadet (balayages capability, demande et production,
point fixe, ordre d'équations dérivé du graphe) se laisse-t-elle décrire en
**données**, ou est-elle irréductiblement procédurale ?

L'enjeu : la décision 3 de l'ADR du 2026-09-08 côté plateforme pose un document
déclaratif comme couture entre muscadet et ses moteurs. Si le continu résiste à
la déclaration, cette décision est fausse et tout ce qui est bâti dessus est à
refaire.

## Le protocole

L'installation d'électrolyse H2 (`tests/test_h2_stack_001.py`) est le cas le plus
dur disponible : quatre composants, capacités, recettes à réactif limitant,
dé-ratage, balayage de capability, un mode de défaillance à délai, et des valeurs
de référence connues.

Elle est décrite **entièrement en JSON**, reconstruite depuis ce seul JSON, et les
trajectoires sont comparées. Rien n'est concluant tant que les nombres ne
coïncident pas.

Deux processus, parce que **PyCATSHOO interdit plus d'un système par processus** :
on ne peut pas comparer un système et sa reconstruction dans la même exécution.
C'est un argument de plus pour la couture déclarative, puisque deux documents se
comparent sans rien construire.

```
python prototypes/declarative-seam/roundtrip.py ref
python prototypes/declarative-seam/roundtrip.py rebuild
python prototypes/declarative-seam/roundtrip.py compare
python prototypes/declarative-seam/callable_limit.py
```

## La réponse

**La couture déclarative est viable, et le risque que je craignais n'existe pas.**

1. **L'aller-retour tient sur le cas le plus dur.** Les dix trajectoires sont
   identiques, dé-ratage et balayage de capability compris : batterie 100 → 98,5,
   cuve 3 → 4,5, sortie à zéro pendant la panne. Toute la description tient en
   4748 octets de JSON.

2. **Le procédural n'était pas le sujet.** L'ordre d'équations est **dérivé** du
   graphe par `compute_equation_order`, qui « n'enregistre rien » : il n'a pas à
   être déclaré, il se recalcule. Les balayages sont l'algorithme d'évaluation du
   moteur, pas la description du système. Ni l'un ni l'autre n'avait à entrer dans
   un document.

3. **La déclaration porte déjà plus qu'attendu** : flux, capacités, règles,
   transferts, mesures, contrôles, automates **et modes de défaillance**. Le mode
   à délai a été reconstruit sans être redéclaré.

4. **Ce qui manque est l'enveloppe de système** : connexions et indicateurs ne
   sont portés par aucune déclaration de composant. C'est exactement, et
   uniquement, le périmètre à construire.

5. **La seule vraie limite est déjà traitée, et bruyamment.** Une fonction Python
   dans un modèle (`allocation_fun`, `combine_fun`) fait échouer la déclaration
   avec un message qui nomme le champ et dit quoi faire à la place. Jamais de
   perte silencieuse. Ces fonctions n'apparaissent que dans 4 fichiers de test
   sur 144.
