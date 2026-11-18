#!/usr/bin/env python3
"""keystore.py — clés API Steam et RetroAchievements, hors de la base.

Chaque clé vit dans son propre fichier du dossier de données (`steam.key`, `ra.key`), lisible
par l'utilisateur seul (0600), comme `steamgriddb.key` et `igdb.key`. La base (`games.db`) peut
ainsi être copiée, sauvegardée ou partagée sans emporter de secret.

Les bases créées par les versions précédentes gardaient ces clés dans la table `settings` :
`migrate_from_db` les déplace vers les fichiers (une seule fois) puis les efface de la base.
"""
import os

# nom du réglage historique -> fichier
FILES = {"steam_api_key": "steam.key", "ra_key": "ra.key"}


def _dir():
    from .db import DB_PATH           # import tardif : db.py importe ce module
    return DB_PATH.parent


def path(name):
    return _dir() / FILES[name]


def read(name):
    try:
        return path(name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write(name, value):
    """Enregistre la clé (ou supprime le fichier si elle est vide), en 0600 dès la création."""
    f, value = path(name), (value or "").strip()
    if not value:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    f.chmod(0o600)                     # fichier préexistant : droits resserrés aussi


def migrate_from_db(con):
    """Déplace les clés encore présentes dans `settings` vers leurs fichiers, puis les efface.

    Un fichier déjà présent l'emporte (il est plus récent que la base) ; la ligne de la base
    est supprimée dans tous les cas. Renvoie les réglages déplacés."""
    moved = []
    for name in FILES:
        row = con.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
        if row is None:
            continue
        if row[0].strip() and not read(name):
            write(name, row[0])
        if read(name) or not row[0].strip():
            con.execute("DELETE FROM settings WHERE key=?", (name,))
            moved.append(name)
    con.commit()
    return moved
