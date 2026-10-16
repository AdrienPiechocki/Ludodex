#!/usr/bin/env python3
"""games.py — opération destructrice sur une fiche de jeu.

Tient à part de `db.py`, qui ne peut pas importer `covers` : `covers` importe déjà
`db`, et le fichier de la jaquette se range à côté de la base (`DB_PATH.parent`).
"""
from .covers import covers_dir, custom_covers_dir


def delete_game(con, gid):
    """Retire un jeu de la bibliothèque : sa fiche, tout ce qu'elle porte, ses fichiers.

    `sessions` et `collection_games` portent `ON DELETE CASCADE` et suivent tout seuls
    (`PRAGMA foreign_keys=ON`, posé par `db()`). Les quatre tables listées ci-dessous,
    non : elles référencent un jeu SANS clé étrangère et garderaient leurs lignes pour
    toujours. `games_genres` et `caves` n'existent que dans les bases itch lues en
    lecture seule, jamais dans `games.db`.

    Les jaquettes partent aussi : leur nom est `{id}.jpg`, et SQLite RÉUTILISE l'id
    libéré — le prochain jeu à porter ce numéro afficherait la couverture du précédent.
    Notes, suivi YAML et statut sont des colonnes de la fiche : ils suivent.

    Renvoie False si ce `gid` n'existe pas.
    """
    row = con.execute("SELECT cover_path, custom_cover FROM games WHERE id=?", (gid,)).fetchone()
    if row is None:
        return False
    con.execute("DELETE FROM achievements WHERE game_id=?", (gid,))
    con.execute("DELETE FROM ra_achievements WHERE game_id=?", (gid,))
    con.execute("DELETE FROM steam_achievements WHERE game_id=?", (gid,))
    con.execute("DELETE FROM tracker_logs WHERE game_id=?", (gid,))
    con.execute("DELETE FROM games WHERE id=?", (gid,))
    con.commit()
    for name, folder in ((row["cover_path"], covers_dir()), (row["custom_cover"], custom_covers_dir())):
        if name:
            (folder / name).unlink(missing_ok=True)
    return True


def set_status_many(con, gids, status):
    """Statut de plusieurs jeux d'un seul commit ; renvoie le nombre RÉELLEMENT modifié.

    Déjà à ce statut : la clause `status != ?` épargne la ligne — le retour dit
    la nouveauté, pas la présence, pour que le toast de la bibliothèque ne mente
    pas (`total_changes` compte les vraies modifications)."""
    gids = list(gids)
    if not gids:
        return 0
    avant = con.total_changes
    con.execute(f"UPDATE games SET status=? WHERE status != ? "
                f"AND id IN ({','.join('?' * len(gids))})", [status, status, *gids])
    n = con.total_changes - avant
    con.commit()
    return n


def set_hidden_many(con, gids, hidden):
    """Masquage de plusieurs jeux d'un seul commit ; renvoie le nombre RÉELLEMENT modifié.

    Même contrat que `set_status_many` : déjà dans l'état voulu ne compte pas,
    pour que le toast (« masqué » / « démasqué ») ne mente pas."""
    gids = list(gids)
    if not gids:
        return 0
    avant = con.total_changes
    val = int(hidden)
    con.execute(f"UPDATE games SET hidden=? WHERE hidden != ? "
                f"AND id IN ({','.join('?' * len(gids))})", [val, val, *gids])
    n = con.total_changes - avant
    con.commit()
    return n
