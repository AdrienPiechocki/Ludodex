"""Les tests ne touchent jamais au vrai dossier de données : HOME est redirigé avant tout import."""
import os, pathlib, sys, tempfile

_HOME = tempfile.mkdtemp(prefix="ludodex-test-home-")
os.environ["HOME"] = _HOME
os.environ.pop("STEAMGRIDDB_API_KEY", None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
