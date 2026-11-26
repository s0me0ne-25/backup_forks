# backup_forks

Tiny tool to back up a public GitHub repository along with all its level-1 (direct) forks containing unique contributions.

## Features

- **Zero External Dependencies:** Built entirely with Python's standard library (`urllib`, `subprocess`, `pathlib`, etc.) and standard `git` CLI commands.
- **Smart Fork Filtering:** Uses `git ls-remote` before cloning to compare commit hashes against the main repository. Forks that are identical to or strictly behind the upstream repository are skipped.
- **Incremental Updates & Auto-Cleanup:** Re-running the script fetches new changes for existing clones and automatically deletes local fork backups if their unique changes are eventually merged into the upstream repository.
- **Rate-Limit Handling:** Automatically detects GitHub API rate limits (`403`/`429`) and pauses until the reset window passes.
- **Daemon Mode Ready:** Includes a simple bash wrapper (`daemon.sh`) to run backups periodically.

## Requirements

- **Python:** 3.10+ (uses modern type hinting like `dict[str, str]` and `Path | None`)
- **Git:** Installed and accessible in your system `PATH`

## Usage

### One-Time Backup

Run `backup_forks.py` with the HTTPS URL of the target public GitHub repository:

```bash
python backup_forks.py https://github.com/OWNER/REPO
```

### Continuous Backup (Daemon)

Use the included `daemon.sh` script to run the backup loop every 60 minutes:

```bash
chmod +x daemon.sh
./daemon.sh https://github.com/OWNER/REPO
```

## Directory Structure

Running the script creates a directory named after the target repository (`OWNER__REPO`) in your current working directory:

```text
OWNER__REPO/
├── main/                       # Clone of the primary upstream repository
└── forks/                      # Direct forks with unique commits/branches
    ├── USER1__REPO/
    └── USER2__REPO/
```

## How It Works

1. **Sync Main Repository:** Clones or fetches all branches and tags from the primary repository into `OWNER__REPO/main`.
2. **Discover Level-1 Forks:** Queries the GitHub REST API (`/repos/{owner}/{repo}/forks`) to retrieve a paginated list of all direct forks.
3. **Verify & Sync Forks:** 
   - For uncloned forks, executes `git ls-remote` to inspect remote refs. If any commit SHA is missing from the local `main` repository's commit history, the fork is cloned.
   - For previously cloned forks, fetches the latest updates.
4. **Cleanup Merged Forks:** Scans all locally backed-up forks and removes any directory whose commits are now entirely present in the `main` repository (e.g., after a Pull Request is merged upstream).

## Limitations

- **Unauthenticated API Limits:** Because no GitHub personal access token is used, requests are subject to GitHub's unauthenticated rate limit (60 requests per hour per IP). For repositories with thousands of forks, the script will automatically sleep when limits are hit.
- **Public Repositories Only:** Interactive Git prompts are explicitly disabled (`GIT_TERMINAL_PROMPT=0`), meaning private or deleted forks are safely skipped.
- **Level-1 Forks Only:** Only direct forks of the target repository are inspected; forks-of-forks are not traversed.

## License

This project is dedicated to the public domain under the **CC0 1.0 Universal** license. You can copy, modify, distribute, and perform the work, even for commercial purposes, all without asking permission.