"""Off-site backup: dated archives sent with sftp to a space that may be write-only.

The backup space needs only `put` and `mkdir` (no rename, no delete, no shell): every archive is
written once under a dated name, its size is checked on the server, then a `<name>.sha256` file is
written next to it. An archive without its .sha256 is an interrupted upload and must be ignored.
Old archives are removed on the backup server's side (the job never deletes anything there).

Run as root from cron (settings `OFFSITE_BACKUP_*` in .env, see docs/operations.md):
  cd /opt/chatbot && venv/bin/python -m scripts.offsite_backup hourly

Modes:
  hourly     people data (users, questions, feedback, survey, applications…), dynamic facts,
             inbox and the .env files                                     -> <dir>/orari/
  nightly    system configuration (/etc, /usr/local/etc, ssh keys, package and disk lists) +
             the application without its virtualenv + the people data    -> <dir>/notte/
  weekly     index and sync state (taken under the sync lock) + virtualenv, Ollama models and
             the installed Ollama package                                 -> <dir>/settimana/
  documents  downloaded documents changed since the last complete run, in parts of ~2 GB;
             a run that stops resumes without sending again the parts already sent
                                                                          -> <dir>/documenti/
  all        the four above (first run)
"""
import argparse
import fcntl
import glob
import hashlib
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings

APP = settings.BASE_DIR.resolve()
DATA = settings.DATA_DIR.resolve()
STATE = Path("/var/db/zautte-backup")
LOG = Path("/var/log/zautte-offsite.log")
PART_BYTES = 2 * 1024**3
ARCHIVE_DIRS = {"hourly": "orari", "nightly": "notte", "weekly": "settimana", "documents": "documenti"}


def _rel(path):
    """Path as tar stores it (without the leading slash), for --exclude."""
    return str(path).lstrip("/")


APP_EXCLUDES = [_rel(APP / "deploy_backups"), _rel(APP / "venv"), _rel(APP / "venv.old.*"),
                _rel(APP / "*.log"), "*/__pycache__"]
if not (APP / "data").is_symlink():  # data inside the application: sent by the other modes
    APP_EXCLUDES.append(_rel(APP / "data"))
INDEX_EXCLUDES = ["*.pilotbak", "*.bak", "*.bak_*", "*.bak[0-9]", "*.before_*"]


def log(msg):
    line = f"{settings.now_local():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def sync_lock():
    control = settings.SYNC_CONTROL_DIR
    return str(control / "sync.lock") if control.is_dir() else "/var/run/chatbot-sync.lock"


def people_paths():
    files = sorted(glob.glob(f"{DATA}/*.json") + glob.glob(f"{DATA}/*.jsonl"))
    dirs = [str(DATA / d) for d in ("facts", "inbox") if (DATA / d).is_dir()]
    envs = [str(p) for p in (APP / ".env", APP / ".env.sync") if p.exists()]
    return files + dirs + envs


def system_paths():
    paths = ["/etc", "/usr/local/etc", "/boot/loader.conf", "/root/.ssh", str(STATE / "meta")]
    paths += sorted(glob.glob("/home/*/.ssh"))
    if settings.SYNC_CONTROL_DIR.is_dir():
        paths.append(str(settings.SYNC_CONTROL_DIR))
    return [p for p in paths if os.path.exists(p)]


def sftp(commands):
    """sftp in batch mode; a command starting with '-' may fail (mkdir of an existing folder)."""
    r = subprocess.run(["sftp", "-q", "-b", "-", settings.OFFSITE_BACKUP_TARGET],
                       input="\n".join(commands) + "\n", capture_output=True, text=True,
                       timeout=6 * 3600, check=False)
    return r.returncode, r.stdout + r.stderr


def remote_size(path):
    _, out = sftp([f"ls -ln {path}"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 9 and parts[-1].endswith("/" + os.path.basename(path)):
            return int(parts[4])
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_tar(out, paths, excludes=(), compress=True, lock=False, list_file=None, cwd=None):
    cmd = ["nice", "-n", "19", "tar", "-c", "-f", str(out)]
    if compress:
        cmd += ["--zstd", "--options", "zstd:compression-level=3,zstd:threads=2"]
    for e in excludes:  # before the paths: bsdtar applies an option only to what follows it
        cmd += ["--exclude", e]
    if cwd:
        cmd += ["-C", str(cwd)]
    if list_file:
        cmd += ["--null", "-T", str(list_file)]
    cmd += [str(p) for p in paths]
    if lock:
        cmd = ["lockf", "-t", "3600", sync_lock()] + cmd
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"tar exit {r.returncode}: {r.stderr.strip()[-500:]}")


def upload(mode, local):
    """Sends the archive once, checks its size on the server, then writes the .sha256 marker."""
    name = local.name
    folder = f"{settings.OFFSITE_BACKUP_DIR}/{ARCHIVE_DIRS[mode]}"
    remote = f"{folder}/{name}"
    size = local.stat().st_size
    marker = local.with_name(name + ".sha256")
    marker.write_text(f"{sha256(local)}  {name}\n")
    t0 = time.time()
    rc, out = sftp([f"-mkdir {settings.OFFSITE_BACKUP_DIR}", f"-mkdir {folder}",
                    f"put {local} {remote}"])
    if rc != 0:
        raise RuntimeError(f"upload of {name} failed: {out.strip()[-500:]}")
    got = remote_size(remote)
    if got != size:
        raise RuntimeError(f"upload of {name}: {got} bytes on the server, {size} expected")
    rc, out = sftp([f"put {marker} {remote}.sha256"])
    if rc != 0:
        raise RuntimeError(f"{name}.sha256 not written: {out.strip()[-300:]}")
    local.unlink()
    marker.unlink()
    log(f"OK {ARCHIVE_DIRS[mode]}/{name} {size / 1e6:.1f} MB in {time.time() - t0:.0f} s")
    return size


def write_meta():
    """What a reinstallation needs to know: versions, packages, disks, network."""
    meta = STATE / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    disks = subprocess.run(["sysctl", "-n", "kern.disks"], capture_output=True, text=True, check=False).stdout.split()
    cmds = {
        "freebsd-version.txt": "freebsd-version -ku; uname -a",
        "pkg-prime.txt": "pkg prime-list",
        "pkg-all.txt": "pkg query -a '%n-%v %R'",
        "pkg-locked.txt": "pkg query -e '%k=1' '%n-%v'",
        "gpart-show.txt": "gpart show",
        "swap.txt": "swapinfo -h",
        "hw.txt": "sysctl hw.ncpu hw.physmem hw.model",
        "ifconfig.txt": "ifconfig -a",
        "services.txt": "service -e",
    }
    cmds.update({f"gpart-{d}.txt": f"gpart backup {d}" for d in disks if not d.startswith("cd")})
    for fname, cmd in cmds.items():
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)
        (meta / fname).write_text(r.stdout + r.stderr)


def ollama_package():
    """The installed Ollama package from pkg's cache, copied (the cache holds a symbolic link):
    a newer Ollama computes different vectors, so a reinstallation needs this exact version."""
    r = subprocess.run(["pkg", "query", "%n-%v", "ollama"], capture_output=True, text=True, check=False)
    name = r.stdout.strip()
    src = Path(f"/var/cache/pkg/{name}.pkg")
    if r.returncode != 0 or not name or not src.exists():
        return []
    dest = STATE / "pkg"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / src.name
    if not target.exists():
        shutil.copyfile(src.resolve(), target)
    return [str(target)]


def stamp():
    return settings.now_local().strftime("%Y%m%d-%H%M")


def run_hourly(tmp):
    out = tmp / f"{stamp()}.tar.zst"
    make_tar(out, people_paths())
    return upload("hourly", out)


def run_nightly(tmp):
    write_meta()
    out = tmp / f"{stamp()}.tar.zst"
    make_tar(out, system_paths() + [APP] + people_paths(), APP_EXCLUDES)
    return upload("nightly", out)


def run_weekly(tmp):
    s = stamp()
    out = tmp / f"{s}-indice.tar.zst"
    make_tar(out, [settings.VECTOR_STORE_DIR.resolve(), settings.CRAWL_CACHE_DIR.resolve()],
             INDEX_EXCLUDES, lock=True)
    total = upload("weekly", out)
    programs = [APP / "venv"] + ollama_package()
    try:
        programs.append(pwd.getpwnam("ollama").pw_dir)
    except KeyError:
        pass
    out = tmp / f"{s}-programmi.tar.zst"
    make_tar(out, [p for p in programs if os.path.exists(p)])
    return total + upload("weekly", out)


def run_documents(tmp):
    """Documents changed since the last complete run, in parts; each part sent is remembered."""
    state_file = STATE / "documents.json"
    st = json.loads(state_file.read_text()) if state_file.exists() else {"since": 0, "done": []}
    start = time.time()
    done = set(st.get("done", []))
    docs = settings.DOCUMENTS_DIR.resolve()
    todo = []
    with os.scandir(docs) as it:
        for e in it:
            if e.is_file(follow_symlinks=False) and e.name not in done:
                s = e.stat(follow_symlinks=False)
                if s.st_mtime >= st["since"] - 600:
                    todo.append((e.name, s.st_size))
    todo.sort()
    log(f"documents: {len(todo)} to send ({sum(s for _, s in todo) / 1e9:.2f} GB)")
    parts, cur, cur_size = [], [], 0
    for n, s in todo:
        if cur and cur_size + s > PART_BYTES:
            parts.append(cur)
            cur, cur_size = [], 0
        cur.append(n)
        cur_size += s
    if cur:
        parts.append(cur)
    total, s0 = 0, stamp()
    for i, part in enumerate(parts, 1):
        lst = tmp / "documents.list"
        lst.write_bytes(b"".join(f"{docs.name}/{n}".encode() + b"\0" for n in part))
        out = tmp / f"{s0}-p{i:03d}.tar"
        make_tar(out, [], compress=False, list_file=lst, cwd=docs.parent)
        lst.unlink()
        total += upload("documents", out)
        done.update(part)
        state_file.write_text(json.dumps({"since": st["since"], "done": sorted(done)}))
    state_file.write_text(json.dumps({"since": start, "done": []}))
    return total


def mail(subject, body):
    to = settings.OFFSITE_BACKUP_REPORT_TO
    if not to:
        return
    try:
        from api.mailer import send_email
        send_email(to, subject, body)
    except Exception as e:  # noqa: BLE001 - the backup outcome is already in the log
        log(f"mail not sent: {e}")


MODES = {"hourly": run_hourly, "nightly": run_nightly, "weekly": run_weekly,
         "documents": run_documents}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=[*MODES, "all"])
    ap.add_argument("--mail", action="store_true", help="e-mail the outcome even when all went well")
    a = ap.parse_args()
    if not settings.OFFSITE_BACKUP_TARGET:
        print("OFFSITE_BACKUP_TARGET is empty: off-site backup disabled", file=sys.stderr)
        return 0
    if os.geteuid() != 0:
        print("run as root: the archives hold the whole system configuration", file=sys.stderr)
        return 2
    os.umask(0o077)
    STATE.mkdir(parents=True, exist_ok=True)
    # Each mode prepares its archives in its own folder: the hourly run must not clean up
    # under a documents run that is still preparing a part
    tmp = settings.OFFSITE_BACKUP_TMP / a.mode
    tmp.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / f"{a.mode}.lock", "w")  # noqa: SIM115 - held until the process exits
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(f"{a.mode}: previous run still going, skipped")
        return 0
    for f in tmp.iterdir():  # leftovers of an interrupted run of this mode
        f.unlink()
    modes = ["nightly", "hourly", "weekly", "documents"] if a.mode == "all" else [a.mode]
    t0, report, failed = time.time(), [], False
    for m in modes:
        t = time.time()
        try:
            size = MODES[m](tmp)
            report.append(f"{m}: OK, {size / 1e9:.2f} GB in {(time.time() - t) / 60:.0f} min")
        except Exception as e:  # noqa: BLE001 - every failure goes in the report
            failed = True
            log(f"FAILED {m}: {e}")
            report.append(f"{m}: FAILED - {e}")
    for f in tmp.iterdir():
        f.unlink()
    if failed or a.mail or a.mode == "weekly":
        subject = (f"Zautte: copia di sicurezza {a.mode} "
                   + ("(da controllare)" if failed else "riuscita"))
        body = (f"Macchina: {socket.gethostname()}\n"
                f"Destinazione: {settings.OFFSITE_BACKUP_TARGET}:{settings.OFFSITE_BACKUP_DIR}\n"
                f"Durata: {(time.time() - t0) / 60:.0f} min\n\n" + "\n".join(report)
                + f"\n\nDettagli: {LOG}\n")
        mail(subject, body)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
