#!/usr/bin/env python3
"""Valide les fichiers de succès de achievements/ et (re)génère achievements/index.json.

    python3 tools/build_index.py           écrit l'index
    python3 tools/build_index.py --check   échoue si l'index est périmé (CI)

Sortie ≠ 0 si un YAML est invalide ; les avertissements (coquilles, descriptions manquantes)
sont affichés sans faire échouer."""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import community  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="vérifie que index.json est à jour sans l'écrire")
    ap.add_argument("--dir", default=str(ROOT / "achievements"))
    args = ap.parse_args()
    root = pathlib.Path(args.dir)
    try:
        index, warnings = community.build_index(root)
    except ValueError as e:
        print(f"ERREUR: {e}", file=sys.stderr)
        return 1
    for w in warnings:
        print(f"avertissement: {w}")
    text = community.index_json(index)
    target = root / "index.json"
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            print("index.json périmé : lancez python3 tools/build_index.py", file=sys.stderr)
            return 1
        print(f"index.json à jour ({len(index['games'])} jeux)")
        return 0
    target.write_text(text, encoding="utf-8")
    print(f"{target}: {len(index['games'])} jeux")
    return 0


if __name__ == "__main__":
    sys.exit(main())
