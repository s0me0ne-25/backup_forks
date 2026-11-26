import json
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

def git_env() -> dict[str, str]:
    return {
        **subprocess.os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "",
    }

def parse_github_url(url: str) -> tuple[str, str]:
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    match = re.match(r"^https?://github\.com/([^/]+)/([^/]+)$", url)
    if not match:
        raise ValueError(f"Invalid GitHub repository HTTPS URL: {url}")
    return match.group(1), match.group(2)


def github_get_json(url: str) -> tuple[object, dict[str, str]]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "git-fork-cascading-backup-script",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            data = json.loads(resp.read().decode("utf-8"))
            return data, headers
    except urllib.error.HTTPError as e:
        headers = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        if e.code in (403, 429) and headers.get("x-ratelimit-remaining") == "0":
            reset_ts = int(headers.get("x-ratelimit-reset", "0"))
            wait_sec = max(1, reset_ts - int(time.time()) + 2)
            print(f"[RATE LIMIT] GitHub API rate limit exceeded. Waiting {wait_sec} sec...")
            time.sleep(min(wait_sec, 300))
            return github_get_json(url)
        raise RuntimeError(f"GitHub API error ({e.code}) for {url}: {e.reason}") from e


def fetch_direct_forks(owner: str, repo: str) -> list[dict]:
    forks: list[dict] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        url = f"https://api.github.com/repos/{owner}/{repo}/forks?{query}"
        data, _ = github_get_json(url)
        if not isinstance(data, list) or len(data) == 0:
            break
        forks.extend(data)
        if len(data) < 100:
            break
        page += 1
    return forks


def run_git(args: list[str], cwd: Path | None = None) -> None:
    cmd = ["git", *args]
    env = git_env()
    subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=True,
        text=True,
    )

def has_fork_contributions(clone_url: str, main_dir: Path) -> bool:
    try:
        env = git_env()
        result = subprocess.run(
            ["git", "ls-remote", clone_url],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
            env=env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # If the repository became private, was deleted, or requires authentication — assume no contributions (skip)
        return False
        
    lines = result.stdout.splitlines()
    if not lines:
        return False

    for line in lines:
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.endswith("^{}"):
            continue  # Skip dereferenced tag pointers

        # Check if this hash exists in the local main repository
        check = subprocess.run(
            ["git", "-C", str(main_dir), "rev-parse", "--quiet", "--verify", f"{sha}^{{commit}}"],
            capture_output=True,
            text=True,
        )
        # If the hash is NOT found in main, the fork has unique contributions (a new commit/branch)
        if check.returncode != 0:
            return True

    return False


def has_local_fork_contributions(fork_dir: Path, main_dir: Path) -> bool:
    if not (fork_dir / ".git").exists():
        return False

    try:
        result = subprocess.run(
            ["git", "-C", str(fork_dir), "show-ref"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return False

    lines = result.stdout.splitlines()
    if not lines:
        return False

    for line in lines:
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.endswith("^{}"):
            continue  # Skip dereferenced tag pointers

        # Check if this hash exists in the local main repository
        check = subprocess.run(
            ["git", "-C", str(main_dir), "rev-parse", "--quiet", "--verify", f"{sha}^{{commit}}"],
            capture_output=True,
            text=True,
        )
        # If the hash is NOT found in main, the fork has unique contributions (a new commit/branch)
        if check.returncode != 0:
            return True

    return False


def remove_readonly(func, path, excinfo) -> None:
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def sync_repo(clone_url: str, target_dir: Path) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if not (target_dir / ".git").exists():
        print(f"[CLONE] {clone_url} -> {target_dir.as_posix()}")
        run_git(["clone", clone_url, target_dir.as_posix()])
    else:
        print(f"[FETCH] {target_dir.as_posix()}")
        run_git(["remote", "set-url", "origin", clone_url], cwd=target_dir)
        run_git(["fetch", "--all", "--tags", "--prune"], cwd=target_dir)

        branches_out = subprocess.check_output(
            ["git", "for-each-ref", "--format=%(refname:short) %(upstream:short)", "refs/heads/"],
            cwd=str(target_dir),
            text=True,
        )
        for line in branches_out.splitlines():
            parts = line.strip().split()
            if len(parts) == 2:
                local_branch, _ = parts
                current_branch = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=str(target_dir),
                    text=True,
                ).strip()
                if current_branch == local_branch:
                    subprocess.run(
                        ["git", "pull", "--ff-only"],
                        cwd=str(target_dir),
                        check=False,
                        text=True,
                        env=git_env(),
                    )


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python backup_forks.py https://github.com/OWNER/REPO", file=sys.stderr)
        return 1

    owner, repo = parse_github_url(sys.argv[1])
    base_dir = Path.cwd() / f"{owner}__{repo}"
    main_dir = base_dir / "main"
    forks_dir = base_dir / "forks"

    # Step 1: Update / clone the main repository
    main_clone_url = f"https://github.com/{owner}/{repo}.git"
    print(f"=== Main repository: {owner}/{repo} -> {main_dir.as_posix()} ===")
    sync_repo(main_clone_url, main_dir)

    # Step 2: Fetch the list of forks via the GitHub API
    print(f"\n=== Fetching list of direct forks (level 1) for {owner}/{repo} ===")
    forks = fetch_direct_forks(owner, repo)
    print(f"Forks found via API: {len(forks)}")

    # Step 3: Iterate through forks
    for idx, fork in enumerate(forks, start=1):
        full_name = fork.get("full_name", "")
        clone_url = fork.get("clone_url") or (f"https://github.com/{full_name}.git" if full_name else "")
        if not full_name or not clone_url:
            continue
        
        safe_name = full_name.replace("/", "__")
        fork_dir = forks_dir / safe_name
        print(f"\n--- [{idx}/{len(forks)}] Fork: {full_name} ---")

        # If the fork has not been cloned previously, check for contributions via ls-remote
        if not fork_dir.exists():
            print(f"[CHECK] Checking for unique contributions via ls-remote...")
            if not has_fork_contributions(clone_url, main_dir):
                print(f"[SKIP] Fork {full_name} has no unique contributions (identical to main or outdated). Skipping.")
                continue
            print(f"[INFO] Unique contributions detected in fork {full_name}. Starting clone.")

        try:
            sync_repo(clone_url, fork_dir)
        except subprocess.CalledProcessError as e:
            print(f"[SKIP/ERROR] Access to {full_name} denied or network error (code {e.returncode}). Skipping.")
        except RuntimeError as e:
            print(f"[ERROR] {e}. Skipping.")

    # Step 4: Clean up existing local fork directories that no longer have unique contributions
    if forks_dir.exists():
        print(f"\n=== Cleaning up local forks with no unique contributions in {forks_dir.as_posix()} ===")
        for fork_dir in sorted(forks_dir.iterdir()):
            if not fork_dir.is_dir():
                continue
            print(f"[CHECK] Checking local fork {fork_dir.name} for unique contributions...")
            if not has_local_fork_contributions(fork_dir, main_dir):
                print(f"[CLEANUP] Fork {fork_dir.name} has no unique contributions relative to main. Removing directory.")
                shutil.rmtree(fork_dir, onerror=remove_readonly)
            else:
                print(f"[KEEP] Fork {fork_dir.name} still has unique contributions.")

            
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())