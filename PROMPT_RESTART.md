# Annoter — Prompt de redémarrage projet (Claude Code)

> Copie-colle ce document dans Claude Code à la racine du dossier où tu veux travailler. Il contient tout : contexte produit, exigences validées, stack technique, architecture, jalons, conventions de code.

---

## 1. Contexte et objectif

Je veux développer **Annoter**, un logiciel standalone d'annotation de PDF dédié aux **revues de plans mécaniques (dessins industriels)**. Cible : ingénieurs / techniciens méthodes / qualité qui annotent des plans cotés (A0/A1) pour signaler des défauts, ajouter des spécifications GD&T, des remarques, etc.

**Plateforme cible** : **Windows en priorité**, mais le code doit rester portable Linux et macOS (le packaging Windows est livré en premier ; Linux/macOS suivront sans réécriture).

**Modèle d'usage** : mono-utilisateur, local, **aucune installation, aucun droit admin requis**. Livré en deux formes :

- un **`.exe` onefile** (PyInstaller), exécutable depuis n'importe quelle clé USB ;
- un **dossier portable zippé** (PyInstaller `--onedir`) pour les utilisateurs qui veulent voir les fichiers.

**Persistence des annotations** : directement dans le PDF, en utilisant les **annotations PDF standard** (compatibles Acrobat / Foxit / autres). Pas de fichier sidecar.

**Langue UI** : anglais uniquement (le code et les commentaires aussi).

---

## 2. Exigences fonctionnelles validées

### 2.1 Annotations v1

- **GD&T (jeu complet ISO 1101)** : 14 caractéristiques (rectitude, planéité, circularité, cylindricité, profil ligne, profil surface, parallélisme, perpendicularité, inclinaison, position, concentricité, symétrie, battement circulaire, battement total) + modificateurs **Ⓜ / Ⓛ / Ⓟ / Ⓔ** + datums (primaire/secondaire/tertiaire) + préfixe diamètre.
- **Texte libre** : bulles d'info éditables inline.
- **Formes** : rectangle, ellipse/cercle, ligne, **flèche** (avec pointe).
- **Dessin à main levée** : trait lissé.

### 2.2 Interaction GD&T

- **Palette de symboles cliquable** (dock widget).
- Clic sur un symbole → **mini-formulaire (dialog modal)** pour saisir tolérance, modifier, datums, diamètre.
- À la validation, le prochain clic sur le PDF pose la "feature control frame" complète à cet endroit.
- **Pas de calibration d'échelle** ni de mesure géométrique : on annote seulement.

### 2.3 UI / UX

- **Palette d'outils flottante** (déplaçable, ancrable) + **zone PDF plein écran** (canvas central).
- **Panneau liste des annotations** à droite, groupé par page, double-clic = recentrer la vue.
- **Thème clair + sombre** avec switch dans le menu.
- Toggle pour afficher/masquer chaque dock.

### 2.4 Navigation

- Zoom **molette + Ctrl** (multiplicatif), Ctrl++ / Ctrl+- / Ctrl+0 (fit) / Ctrl+1 (100 %).
- **Zoom fenêtre** (sélection rectangle pour zoomer sur une zone).
- **Pan à la barre espace** (curseur main) + clic milieu.
- **Rotation de page** (90°/180°/270°).
- **PageUp / PageDown** pour naviguer entre pages.

### 2.5 Gestion des annotations

- **Undo / Redo illimité** (`QUndoStack`, limite raisonnable 200).
- **Couleurs personnalisables** par annotation (palette de 5 couleurs par défaut).
- **Stroke width** sélectionnable (1.0 / 2.0 / 3.5 px).
- **Modification après pose** : déplacer, redimensionner, recolorier, changer texte/valeur GD&T.
- **Suppression** (Del/Backspace), select-all (Ctrl+A).

### 2.6 Performances

- Cibles : plans **A0/A1** et PDF **lourds (>100 Mo)**.
- Cache LRU des pixmaps de page rendus.
- Re-render à plus haute DPI au-delà d'un seuil de zoom (en gardant les annotations attachées aux pages).

### 2.7 Fichiers

- **Open / Save / Save As** + raccourcis standards.
- **Liste des fichiers récents** (max 10, persistée via QSettings).
- **Drag & drop d'un PDF** dans la fenêtre.
- Le titre de fenêtre reflète le fichier en cours et un indicateur "modifié".

### 2.8 Exclusions v1 (notées pour v2)

- **Stamps** (ex. "APPROVED", "REJECTED", "BON POUR EXÉCUTION") — v2.
- Mode collaboratif / multi-utilisateur — pas prévu.
- OCR — pas prévu.

---

## 3. Stack technique imposée

- **Python 3.12** (CPython, distribution embedded pour le packaging).
- **PySide6** (Qt 6 bindings, licence LGPL → distribution autorisée sans publication du code utilisateur).
- **PyMuPDF** (`fitz`) pour le rendu et l'écriture des annotations PDF.
- **Pillow** pour les conversions image utilitaires.
- **PyInstaller** pour le packaging (`--onefile` ET `--onedir`).
- **pytest** pour les tests.

`pyproject.toml` en src-layout, install dev avec `pip install -e .`.

---

## 4. Architecture (MVC adapté Qt)

```
Annoter/
├── pyproject.toml
├── README.md
├── PLAN.md
├── build.py                 # script PyInstaller (onefile + onedir)
├── requirements.txt
├── resources/               # icônes, thèmes QSS, samples
├── src/
│   └── annoter/
│       ├── __init__.py
│       ├── app.py           # main(): QApplication + MainWindow
│       ├── config.py        # constantes (couleurs, DPI, zoom, etc.)
│       ├── model/
│       │   └── document.py  # PdfDocument (wrapper fitz.Document)
│       ├── services/
│       │   ├── pdf_render.py        # PageRenderer + cache LRU
│       │   ├── recent_files.py      # MRU via QSettings
│       │   ├── pdf_export.py        # M4: écriture annotations PDF
│       │   └── theme.py             # M4: switch clair/sombre (QSS)
│       ├── controllers/
│       │   ├── tools.py             # ToolController (enum Tool)
│       │   └── commands.py          # QUndoCommand subclasses
│       └── views/
│           ├── main_window.py
│           ├── pdf_view.py          # QGraphicsView (zoom/pan/draw)
│           ├── pdf_scene.py         # QGraphicsScene multi-pages
│           ├── icons.py             # QIcon factory inline
│           ├── tool_palette.py      # dock: outils + couleurs + stroke
│           ├── annotation_list.py   # dock: liste groupée par page
│           ├── gdt_palette.py       # dock: 14 symboles ISO 1101
│           ├── gdt_dialog.py        # dialog d'insertion GD&T
│           └── items/               # graphics items
│               ├── base.py          # AnnotationItem (super)
│               ├── shapes.py        # RectangleItem, EllipseItem
│               ├── lines.py         # LineItem, ArrowItem
│               ├── text.py          # TextAnnotationItem (édit. inline)
│               ├── freehand.py      # FreehandItem
│               ├── gdt_symbols.py   # 14 dessins QPainterPath
│               └── gdt.py           # GdtAnnotationItem (frame complet)
└── tests/
    ├── test_smoke.py                # headless (PyMuPDF only)
    ├── test_tools.py                # ToolController
    ├── test_commands.py             # QUndoCommand classes
    └── test_main_window_wiring.py   # end-to-end Qt offscreen
```

### Conventions

- **Annotations parentées aux pages** : un `QGraphicsItem` annotation est enfant du `QGraphicsPixmapItem` de sa page → coordonnées locales à la page, déplacement page = déplacement annotations.
- **Zoom** : `_apply_zoom(factor)` où `factor=1.0` signifie « 1 pixel écran par point PDF » (taille réelle). `view_scale = factor * 72 / render_dpi`.
- **Re-render DPI** : seuil hystérétique. Pendant M2 le seuil est très haut (les annotations enfant seraient orphelinées sur reconstruction). M4 ajoute un mécanisme qui re-parente les annotations après re-render.
- **Tous les changements scène passent par un `QUndoCommand`** : Add / Delete / Move / ChangeColor / ChangeStroke / ChangeGdt. Aucune mutation directe du scene depuis les widgets.
- **`ToolController(QObject)`** : source de vérité pour outil/couleur/stroke courant ; vues s'abonnent à ses signaux (palette, view, etc.) — pas de couplage direct entre vues.

---

## 5. Jalons (ordre de livraison strict)

### M1 — Socle PDF viewer

- Ouvrir/fermer un PDF, navigation pages, zoom, pan, drag&drop, fichiers récents, status bar.
- Pas encore d'annotations.
- **Critère de fin** : `python -m annoter` ouvre un PDF >100 Mo, scrolle fluidement, zoom OK.

### M2 — Annotations classiques

- `ToolController`, `QUndoStack`, palette d'outils flottante, panneau liste, items graphiques (rect, ellipse, ligne, flèche, texte, freehand), commandes Add/Delete/Move/ChangeColor/ChangeStroke, raccourcis Ctrl+Z/Y/Del/Ctrl+A, menu Edit.
- Texte vide après édition → rollback automatique de l'add.

### M3 — GD&T

- Module `gdt_symbols.py` : 14 symboles ISO 1101 dessinés en QPainterPath, normalisés dans une boîte unitaire ; modificateurs M/L/P/E (caractères Unicode encerclés).
- `GdtAnnotationItem` : feature control frame composite (cellule symbole + cellule tolérance + cellules datums) avec mise en page automatique selon `QFontMetricsF`.
- `GdtDialog` modal : combobox caractéristique, tolérance avec validator, checkbox ⌀, combo modifier, 3 champs datums, **prévisualisation live**.
- `GdtPalette` (dock widget) : grille 2 colonnes par famille (Form / Profile / Orientation / Location / Runout). Clic → dialog → puis pose au prochain clic canvas.

### M4 — Persistence & polish

- **Save / Save As** : convertir chaque `AnnotationItem` en annotation PDF native (`fitz.Annot`). Mapping : RectangleItem → `Square`, EllipseItem → `Circle`, LineItem → `Line`, ArrowItem → `Line` avec endStyle, FreehandItem → `Ink`, TextAnnotationItem → `FreeText`, GdtAnnotationItem → `Stamp` (rasterisé) avec `Contents` JSON pour rouvrir. À la réouverture du PDF, lire les annotations et reconstruire les items.
- **Theme switch** clair/sombre (QSS).
- Re-render DPI sans orphéliner les annotations.
- **Préférences** persistantes (QSettings) : dernière taille fenêtre, état des docks, thème.

### M5 — Packaging

- `build.py` qui appelle PyInstaller deux fois (`--onefile` puis `--onedir`).
- Inclure les `.qm` Qt nécessaires, fitz, fonts.
- Vérifier que l'exe tourne sur une VM Windows propre, sans Python installé, depuis une clé USB sans droits admin.

---

## 6. Tests

- **Headless** (`test_smoke.py`) : utilise uniquement PyMuPDF, vérifie ouverture / page count / dimensions / DPI.
- **Qt** : `pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)` ; testent `ToolController`, les `QUndoCommand` (Add/Delete/Move + roundtrip), wiring main window (offscreen `QT_QPA_PLATFORM=offscreen`).
- **Cible CI** : tous les tests verts sur Linux GitHub Actions avec `xvfb-run` ou `QT_QPA_PLATFORM=offscreen`.

---

## 7. Workflow attendu de Claude Code

1. Lis ce document en entier avant tout.
2. Crée le squelette de fichiers (vide ou avec docstring) **avant** d'écrire la moindre logique, pour que je voie l'architecture proposée.
3. Travaille **jalon par jalon** dans l'ordre M1 → M5. Ne commence pas M2 tant que M1 n'est pas validé par moi.
4. À la fin de chaque jalon : un résumé bref de ce qui marche, un appel explicite à validation ("Je peux passer à M3 ?"), et la liste des écarts éventuels avec le plan.
5. **Aucun emoji** dans le code, les docstrings, les UI strings, ni les commit messages.
6. **Anglais** pour le code, les commentaires, les UI strings. **Français** pour la conversation avec moi.
7. Pas de fichier `README.md` superflu, mais un `PLAN.md` à jour avec les décisions d'archi.
8. Toute commande shell doit utiliser des chemins absolus et fonctionner aussi bien sur Windows PowerShell que sur Linux/bash.
9. Pour le packaging final (M5), produit explicitement les deux livrables (`.exe` onefile + zip onedir) et donne les instructions de test sur clé USB.

---

## 8. Première chose à faire

Avant tout code : **redonne-moi le plan détaillé reformulé avec tes propres mots**, point par point, et liste les questions ouvertes que tu as. Je validerai avant le moindre fichier créé.
