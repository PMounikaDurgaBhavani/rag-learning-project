# Why a public benchmark would have missed my top three modes

A public RAG benchmark scores whether the right passage was retrieved and
whether the generated answer matches a reference string, so it would have
recorded my top mode — refusing a question the articles do answer — as a
*correct* refusal or simply a low score, never as the specific finding that
the answering chunk was ranked **first** at 0.7659 and discarded by a
threshold I chose; the failure lives in my gate, not in the model or the
corpus, and no benchmark ships with my threshold. My second and third modes
are invisible to benchmarks by construction: benchmark questions are
well-formed single questions, so "billing" as a bare keyword and "whats the
max file size and which formats are allowed?" as two questions in one
message never appear in the input distribution, and a reply that stops
mid-sentence at 160 tokens is scored on the tokens it did produce rather
than flagged as truncated. What made these findings possible was reading
what real messages actually looked like and what the system actually
returned — a benchmark measures a system against someone else's questions,
whereas error analysis measures it against mine.
