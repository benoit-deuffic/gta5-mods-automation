# Installation et test en jeu (Windows / GTA V Legacy)

À faire à la main sous Windows, avec OpenIV, PedSelector (ScriptHookV + SHVDN3) et Menyoo
installés. Le pipeline Linux produit tout le reste.

## 0. Ce que le pipeline livre

`package_layered_ped` / `update_packaged_ped` (MCP) produisent, pour un ped `<nom>` :

```
dist/<nom>.oiv          paquet OpenIV (à installer)
dist/<nom>.yft/.ymt     binaires RAGE (contenus dans le .oiv)
dist/<nom>/             drawables .ydd/.ytd (contenus dans le .oiv)
workspace/<nom>/        .blend, aperçu, <nom>_deformation.json (cibles + apparence)
```

Le ped est aussi inscrit directement dans
`Documents\Rockstar Games\GTA V\UserMods\ap_m.xml` (sauvegarde horodatée `ap_m.xml.bak-*`
avant chaque écriture, entrées existantes jamais modifiées, PoseMatcher Male conservé).
Rien à faire à la main pour le manifeste.

Format du paquet : `docs/architecture-decisions.md` point 44 ; apparence (tête, coiffure,
pieds nus) : point 46.

## 1. Installer le paquet

1. Copie `dist/<nom>.oiv` sous Windows (le pipeline le copie déjà dans `Downloads`).
2. OpenIV → Tools → Package Installer → `<nom>.oiv` → installation dans le dossier **mods**.
   Il ajoute les fichiers dans
   `<GTA V>\mods\update\x64\dlcpacks\pedselector\dlc.rpf\peds.rpf`.
3. Une mise à jour (`update_packaged_ped`) garde le même id de paquet : réinstaller le
   nouveau `.oiv` remplace les fichiers.

## 2. Lancer le jeu et sélectionner le ped

1. Lance GTA V **Legacy** (PedSelector ne fonctionne pas sur Enhanced).
2. Ouvre PedSelector, cherche `<nom>` (en minuscules dans `ap_m.xml`), sélectionne-le.

À vérifier :
- le ped se charge sans crash ni texture manquante ;
- corps remodelé (poitrine, fesses), tête et coiffure attendues, pieds nus ;
- plusieurs apparitions : la couleur des cheveux varie (le jeu tire une couleur par
  apparition parmi celles livrées) ;
- pas de jointure ouverte entre haut et bas du corps.

## 3. Cloner le ped avec Menyoo

Menyoo (`F8`) → Player Options → Clone Player : le clone apparaît, stable, avec la même
apparence.

## 4. Persistance (Object Spooner)

Menyoo → Object Spooner → sélectionne le clone → Add to Database ; recharge une session :
le PNJ réapparaît à sa position.

## 5. Animations

Marche, course, accroupi, véhicule, émotes aux bras amples : pas d'étirement de polygones,
les cheveux longs suivent les épaules, rien ne flotte loin du corps.

## 6. Quoi me remonter

Pour chaque étape : succès/échec, avec le message d'erreur ou une capture si échec.
