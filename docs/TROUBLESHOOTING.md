# Troubleshooting

Every section below starts with the message you actually see, then explains the
cause in plain words, then gives the fix. Commands are written for Windows
PowerShell, run from the project folder.


## 1. "run.ps1 cannot be loaded ... is not digitally signed"

Full message:

    .\run.ps1 : File C:\Users\...\video-rag\run.ps1 cannot be loaded.
    The file is not digitally signed. You cannot run this script on the
    current system.

Cause: Windows marks every file that came out of a downloaded zip as coming
from the internet. PowerShell then refuses to run it. This has nothing to do
with the code inside the script.

Three fixes, any one of them works.

Easiest, use the batch launcher instead of the script:

    .\run.cmd setup

Or allow scripts for this one terminal window only:

    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    .\run.ps1 setup

Or remove the internet mark from every extracted file, once:

    Get-ChildItem -Path . -Recurse -File | Unblock-File


## 2. "SyntaxError: invalid syntax" pointing at the first line of run.ps1

Cause: the script was started with Python. It is a PowerShell script, not a
Python file.

Wrong:

    python .\run.ps1 setup

Right:

    .\run.cmd setup


## 3. "python is not recognized"

Cause: Python is not installed, or it is not on the PATH.

Install it and open a new terminal afterwards:

    winget install Python.Python.3.12

Check:

    python --version


## 4. "Indexed 0 of N videos, 0 chunks stored, N failed"

The log line above it says:

    Video ingestion failed ... error_code: transcript_unavailable

Cause: the system could not get a transcript for any video. Captions are the
default source, so this means YouTube did not hand over a usable caption track.
There are three different reasons, and they need three different fixes. Find
out which one you have before changing anything:

    .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend/src'); from video_rag.adapters.youtube_captions import CaptionTranscriptProvider; doc = CaptionTranscriptProvider().fetch('VIDEO_ID'); print(len(doc.raw_words), 'words')"

VIDEO_ID is the eleven character code in the video link. You can also paste the
full link. A word count means captions are fine and the problem is elsewhere;
an error names the failing stage. If indexing then still fails with
`embedding_failed`, the Jina API key is missing or rate limited (HTTP 429):
lower `VIDEO_RAG_EMBEDDING_BATCH_SIZE` in `.env` and retry after a minute.

### Case A: it lists tracks, but not in English

Example output: `hi (automatic)`.

The system already handles this: it translates the track when YouTube allows
it, and otherwise indexes the original language. If it still fails, put the
language first in your `.env`:

    VIDEO_RAG_CAPTION_LANGUAGES=hi,en
    VIDEO_RAG_CAPTION_ANY_LANGUAGE=true

Embeddings are multilingual (Jina), so Hindi transcripts match Hindi,
English and mixed queries. If matches look weak, the cause is usually the
refusal thresholds (section 9) or wording with no transcript overlap, not
the language.

### Case B: "this video has no caption tracks at all"

First check whether the fallback already recovered it: the import tries the
fast caption API first, then yt-dlp subtitles, which together cover most
videos. If the video still fails, there is no text to read. Upload captions
to YouTube or pick videos that have them, then re-run the import. Already
indexed videos are skipped, so nothing is done twice.

### Case C: "YouTube refused the listing request"

YouTube is blocking or throttling the requests, usually because too many
videos were requested at once. Slow the import down in `.env`:

    VIDEO_RAG_INGEST_CONCURRENCY=2

Wait a few minutes and run the import again. Already indexed videos are
skipped, so nothing is done twice.

After changing `.env`, stop the server with Ctrl+C and start it again.
Settings are read once at startup.


## 5. "Caption request throttled, backing off"

This is a warning, not a failure. The system waits and retries with growing
delays. If it appears constantly, lower `VIDEO_RAG_INGEST_CONCURRENCY`.


## 6. "CaptionTranscriptProvider.__init__() got an unexpected keyword argument"

Cause: only some of the source files were replaced during an update. The
container is passing an option that the older adapter does not understand.

Fix: replace `config.py`, `container.py` and `adapters/youtube_captions.py`
together, they are one change. Then confirm:

    .\run.cmd test


## 7. Questions about the .env file

The project ships `.env.example`, not `.env`, because a real `.env` can hold
secrets and must never be committed. Everything has a working default, so the
file is optional. Create it only when you want to change something:

    Copy-Item .env.example .env

Check what the application actually loaded:

    .\run.cmd config

Secrets are masked in that output on purpose.


## 8. "address already in use" on port 8000

Another program, often an older server of this project, still holds the port.

Find it:

    Get-NetTCPConnection -LocalPort 8000 | Select-Object -Property OwningProcess

Stop it, replacing PID with the number from above:

    Stop-Process -Id PID

Or just use a different port:

    .\run.cmd serve --port 8010


## 9. Every question answers "The topic is not covered in the given course"

Check these in order.

1. Indexing actually stored something. The import report must show a chunk
   count above zero. If it is zero, go to section 4.
2. The course id in the question box matches the one shown after indexing.
3. Your wording overlaps the topic in the video. If nothing matches, try
   plainer wording or the other language (Hindi/English): the embedder is
   multilingual, but a query sharing no vocabulary with the transcript
   still scores low.
4. The refusal thresholds are still the shipped placeholders. Measure them on
   your own questions before trusting them:

        .\run.cmd eval

   Then adjust `VIDEO_RAG_THRESHOLD_HIGH` and `VIDEO_RAG_THRESHOLD_LOW`.

A refusal is not always a bug. Asking a Kubernetes question of a data
structures course should be refused, that is the feature working.


## 10. The answer looks right but the embedded player does not play

If you ran `smoke`, the sample course uses invented video ids, so there is
nothing for YouTube to play. Index a real playlist and ask again.

If a real video does not play embedded, its owner has disabled embedding.
The timestamp link next to the player still opens it on YouTube at the right
moment.


## 11. Starting over from a clean state

Deletes the local database and the stored vectors. The code
and your `.env` are untouched.

    .\run.cmd reset-data

Re-index afterwards.


## 12. Checking that the project itself is healthy

Run the test suite and the end to end check:

    .\run.cmd test
    .\run.cmd smoke

The smoke check answers one covered question with a timestamp and refuses one
uncovered question. If both of those behave and your own course still fails,
the problem is in getting transcripts, not in the search pipeline.
