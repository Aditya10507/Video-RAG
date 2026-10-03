# Running this project on Windows

Everything in this project is pure Python and runs natively on Windows. Only the
helper commands differ, so this guide is the Windows equivalent of the commands
in the README.

All commands below are for PowerShell, which is the default terminal in Windows
Terminal and in VS Code. If you prefer Command Prompt, replace `.\run.ps1` with
`run.cmd` in every example.

## 1. Install the prerequisites

Required:

- Python 3.12 or newer
- Git (optional, only if you want version control)

Optional, depending on which features you enable:

- Docker Desktop, only if you want the container or the Qdrant stack

The fastest install path uses winget, which ships with Windows 11 and recent
Windows 10 builds:

    winget install Python.Python.3.12
    winget install Git.Git
    winget install Gyan.FFmpeg

Close and reopen the terminal after installing Python so the new PATH is picked
up. Verify:

    python --version

If that prints nothing useful or opens the Microsoft Store, the App Execution
Alias is intercepting it. Open Settings, search for "Manage app execution
aliases", and turn off the two entries named `python.exe` and `python3.exe`.

## 2. One time setup

Open PowerShell in the project folder, then run:

    .\run.ps1 setup

That creates `.venv`, upgrades pip, and installs both the runtime and the
development dependencies.

If PowerShell refuses to run the script with a message about scripts being
disabled, you have two options. Either use the Command Prompt wrapper, which
needs no policy change:

    run.cmd setup

Or allow local scripts once, for your user only:

    Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

## 3. Verify the install with no network and no keys

    .\run.ps1 smoke

This runs the full ingestion and question pipeline against stub adapters. It
should answer a hashmap question with a timestamped link and refuse an off topic
question with the exact sentence "The topic is not covered in the given course",
then print `Smoke check: PASSED`.

Run the tests the same way:

    .\run.ps1 test

## 4. Daily commands

| Task | Windows | Linux and macOS |
|---|---|---|
| Install everything | `.\run.ps1 setup` | `make install-dev` |
| Offline self check | `.\run.ps1 smoke` | `make smoke` |
| Tests with pytest | `.\run.ps1 test` | `make test` |
| Tests with no pytest | `.\run.ps1 test-stdlib` | `make test-stdlib` |
| Lint | `.\run.ps1 lint` | `make lint` |
| Types | `.\run.ps1 typecheck` | `make typecheck` |
| Start the API | `.\run.ps1 serve` | `make serve` |
| Show config | `.\run.ps1 config` | see README |
| Index a course | `.\run.ps1 ingest "URL"` | see README |
| Ask a question | `.\run.ps1 ask "question" --course PLxxxx` | see README |
| Reset local index | `.\run.ps1 reset-data` | `rm -rf data` |

Examples with real arguments:

    .\run.ps1 ingest "https://www.youtube.com/playlist?list=PLxxxx"
    .\run.ps1 ask "where can we use hashmaps" --course PLxxxx

Always wrap YouTube URLs in double quotes. PowerShell treats a bare `&` in a URL
as a command separator and will report "The ampersand is not allowed".

## 5. If you prefer typing the commands yourself

The task runner exists because the bash form used in most tutorials does not
work in PowerShell. This fails:

    PYTHONPATH=backend/src python -m video_rag.cli smoke      # bash syntax, not PowerShell

The PowerShell form sets the variable first, as a separate statement:

    .\.venv\Scripts\Activate.ps1
    $env:PYTHONPATH = "backend/src"
    python -m video_rag.cli smoke

In Command Prompt the equivalent is:

    .venv\Scripts\activate.bat
    set PYTHONPATH=backend\src
    python -m video_rag.cli smoke

The variable only lasts for that terminal session. Opening a new terminal means
setting it again, which is why `.\run.ps1` is the safer habit.

## 6. Configuration file

Copy the sample environment file, then edit it in any text editor:

    Copy-Item .env.example .env
    notepad .env

Paths in `.env` accept either slash style, so both `data` and `C:\video-rag\data`
work. Prefer forward slashes or a plain relative folder to avoid escaping issues.

Local state is written under the `data` folder in the project:

    data\registry.db          course and video bookkeeping, SQLite
    data\memory_store.json    local vectors when the memory backend is used
    data\qdrant\              embedded Qdrant storage when that backend is used

## 7. Videos without captions

Transcripts come from the caption API. The yt-dlp subtitle fallback exists in
the code path but its parser is not configured, so in practice a video without
caption-API tracks is marked failed and skipped; the rest of the course still
indexes. Upload captions to YouTube or pick videos that have them. If videos
fail with `embedding_failed` instead, see the Jina note in section 4.

## 8. Docker on Windows

Docker Desktop with the WSL2 backend runs the provided image unchanged:

    .\run.ps1 docker-build
    .\run.ps1 docker-up

The compose file starts the API on port 8000 plus a Qdrant container, and stores
both volumes inside Docker rather than on the Windows filesystem, which is much
faster than a bind mount.

If the build produces errors about a missing shebang or a stray carriage return,
Git converted line endings on checkout. The included `.gitattributes` prevents
this, so re-clone the repository or run:

    git add --renormalize .

## 9. Common Windows problems

| Message | Cause and fix |
|---|---|
| `python is not recognized` | Python is not on PATH, or the Store alias intercepts it. Reopen the terminal, or disable the app execution aliases as in step 1. |
| `running scripts is disabled on this system` | PowerShell execution policy. Use `run.cmd` instead, or set the policy as in step 2. |
| `ModuleNotFoundError: No module named 'video_rag'` | `PYTHONPATH` is not set for this terminal. Use `.\run.ps1`, or set `$env:PYTHONPATH = "backend/src"` first. |
| `The ampersand is not allowed` | An unquoted URL. Wrap it in double quotes. |
| `Access to the path .venv is denied` | Antivirus or OneDrive is locking the folder. Move the project outside OneDrive, for example `C:\projects\video-rag`. |
| Windows Firewall prompt on `serve` | Expected. The API binds to 127.0.0.1 by default, so you can decline the prompt and still use it locally. |
| Port 8000 already in use | Pass another port: `.\run.ps1 serve --port 8010`. |

## 10. Editor setup

In VS Code, select the interpreter at `.\.venv\Scripts\python.exe` with the
Python: Select Interpreter command. The project already declares a src layout,
so imports resolve once that interpreter is selected. Nothing else is needed.
