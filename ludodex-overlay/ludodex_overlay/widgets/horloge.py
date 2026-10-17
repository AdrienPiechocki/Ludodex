"""horloge.py — widget horloge : texte formaté et classe Widget (tâche 6).

La conversion est pure (pas de `strftime`) : le rendu ne dépend donc d'aucune
locale, les tests sont déterministes et le format 12 h reste prévisible.
"""
import datetime, typing


def heure_texte(moment: datetime.datetime, format_24h: bool, secondes: bool) -> str:
    """Heure de `moment` : « 13:05 », « 13:05:07 », « 1:05 PM », « 12:05 AM »."""
    h, m, s = moment.hour, moment.minute, moment.second
    base = f"{h:02d}:{m:02d}"
    if not format_24h:
        h12 = h % 12 or 12
        base = f"{h12}:{m:02d}"
    if secondes:
        base += f":{s:02d}"
    if not format_24h:
        base += " PM" if h >= 12 else " AM"
    return base
