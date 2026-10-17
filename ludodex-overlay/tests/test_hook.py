"""tests/test_hook.py — drapeau « souris libérée » partagé avec le hook LD_PRELOAD."""
from ludodex_overlay import hook


def test_chemin_etat_suit_home():
    p = hook.chemin_etat({"HOME": "/h"})
    assert str(p) == "/h/.local/share/ludodex-hook/etat"


def test_ecrire_etat_cree_le_dossier_et_ecrit(tmp_path):
    env = {"HOME": str(tmp_path)}
    hook.ecrire_etat(True, env=env)
    assert hook.chemin_etat(env).read_text() == "1"
    hook.ecrire_etat(False, env=env)
    assert hook.chemin_etat(env).read_text() == "0"


def test_ecrire_etat_ne_plante_jamais(monkeypatch):
    """Dossier inaccessible : best-effort assumé (l'overlay ne plante pas)."""
    def interdit(*_args, **_kwargs):
        raise OSError("refusé")
    monkeypatch.setattr(hook.pathlib.Path, "mkdir", interdit)
    hook.ecrire_etat(True, env={"HOME": "/inaccessible"})
