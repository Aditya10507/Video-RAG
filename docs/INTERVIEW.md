# How to present this project

Notes for talking about this project in an interview or a portfolio review.
Everything here is true of the code in this repository.

## The 40 second pitch

"Students learn from YouTube playlists but do not take notes, so revising means
scrubbing through hours of video. I built a retrieval system that indexes what
is actually spoken in every video of a playlist and answers questions from that
course only. Every answer comes with the video and the exact second where the
topic is explained, so you click and watch that moment. If the course does not
cover the topic, it says so instead of guessing, because a study tool that
invents answers is worse than no tool."

Then demonstrate: paste a playlist, ask a covered question, click the timestamp,
ask something the course never mentions and show the refusal.

## The three hard problems

These are the parts worth talking about, because they are where the engineering
is. Ordinary RAG solves none of them.

**1. Returning a timestamp, not just text.**
A normal RAG pipeline returns text and loses everything else. Here, each chunk
carries its video id and its start second from the moment it is created, so the
timestamp is metadata that travels with the chunk into the vector store and back
out with the search result. The word level timings from the caption or the speech
model are never rewritten during cleaning, so the timestamp cannot drift.

**2. Picking the right video, not just the right sentence.**
The best single chunk is not always the best video. A video is scored on its best
chunk plus a bonus for how many of its chunks matched, so a lecture that explains
a topic for five minutes outranks a video that mentions it once in passing.

**3. Knowing when to refuse.**
Retrieval always returns something, so "nothing found" never happens by itself.
The decision is a rule in code: if the top score is above the high threshold the
system answers, if it is below the low threshold and no chunk contains the key
terms it refuses with a fixed message, and in between it answers with a caveat.
A language model is never asked whether the course covers the topic, because it
would guess. Retrieval is filtered by course id, so it is impossible to answer
one course with another course's content.

## Design decisions and their tradeoffs

| Decision | Why | What it costs |
| --- | --- | --- |
| Captions first, yt-dlp subtitles as designed fallback | Captions are seconds per video, transcription is minutes (the fallback parser is not yet configured) | Caption quality varies, so text is cleaned before chunking |
| Chunks of about a minute with overlap, split on sentences | An explanation rarely fits in one sentence, and cutting mid sentence destroys meaning | Slightly more storage than tight chunks |
| Interfaces for every external service | Tests run offline with fakes, and the vector store or model can be swapped by changing one setting | One extra layer of indirection |
| Threshold in code, not model judgement | Refusals must be deterministic and testable | Thresholds must be calibrated per corpus |
| Indexing returns a job id | A playlist takes minutes, an HTTP request should not | A polling endpoint and a job store to maintain |
| Frontend with no build step | Three readable files, nothing to install, instant to demo | No component model if the UI grows |

## Numbers to quote

- 47 tests, all offline, a few seconds to run.
- Four layers, with one interface at each boundary.
- A one hour video with captions indexes in well under a minute; the same video
  through local speech recognition takes minutes, which is why captions come
  first.
- A refusal is answered without any model call, so it returns in a few hundred
  milliseconds.

## Questions you will be asked

**"How do you know the timestamp is right?"**
It comes from the caption or speech model timings for the first word of the
chunk, refined toward the sentence that actually matched the question. Cleaning
steps never touch the timing data, only the text.

**"What stops it from hallucinating?"**
Three things: retrieval is filtered to one course, the answer is written only
from retrieved excerpts and always carries a citation, and the refusal decision
is made by a threshold in code before any model is involved.

**"Why not just use a vector database and a prompt?"**
That gives you an answer without a source, no way to refuse, and no timestamp.
The metadata design and the coverage gate are the actual product.

**"How would you scale it?"**
The vector store is behind an interface with a Qdrant implementation already
written, indexing is a job that can move to a queue and workers without touching
the API, and transcripts are cached so re-indexing is cheap. Chunking and
embedding are the cost centre, and both are per video and parallel.

**"What would you do next?"**
Calibrate the thresholds on labelled questions using the evaluation script,
switch the embedder to a sentence transformer with a cross encoder reranker,
and add per user accounts so courses are private.

## What to say about the parts that are not finished

Being straight about this reads as maturity, not weakness.

- Embeddings come from the Jina hosted API, so the demo needs network and a
  key. Retrieval, ranking, refusal and timestamps need no local model.
- The shipped thresholds are placeholders. The evaluation harness exists to
  replace them with measured values once labelled questions are added.
- A language model key is required: it writes the answers and verifies the
  gray zone. Only the refusal path avoids the LLM entirely.

## The seven principles behind the design

1. Abstention is a code decision, not a model decision.
2. Timing truth is immutable: raw word timings are never rewritten.
3. Every chunk knows where it came from in time.
4. One contract per layer boundary.
5. Indexing is idempotent and resumable.
6. Perceived speed beats batch completeness.
7. Measure before optimising.
