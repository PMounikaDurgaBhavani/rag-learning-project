# error_before_after — the model handling the same failing call

The server is `mcp_servers/kb_server.py` (our own, server one) and the tool is `get_article`. The failing call is replayed identically as lap 1 in every run: `get_article({"article_id": "HC-8"})`. The real id is `HC-008`. From lap 2 on, the model, Qwen2.5-1.5B in the host, is on its own. Script: `python experiments/week9_error_transcript.py before|after`. Runs: `week9/runs/error_*.json`.

## The docstring, before → after (what the model reads in tools/list)

Before:

```
Get article.
```

After (the docstring is written as a prompt: when to call, the id format, what to do instead):

```
Fetch the full text of ONE help-centre article by its exact id.

    Use this when you already know the article id. Ids are "HC-" plus three
    digits, for example HC-008. If you only have a topic, or an id written
    another way ("HC-8", "article 8"), call search_kb first and use the
    article_id it returns.

    Returns the article as markdown. If the id does not exist, the error says
    which ids do, so you can call get_article once more with a correct one.
    
```

## The error, before → after (what the model reads in the tools/call result)

| version | text the model receives | how it reaches the model |
|---|---|---|
| before | `Error executing tool get_article: Error 3` | raised; the SDK adds the prefix |
| v1 | `Error executing tool get_article: article 'HC-8' not found: ids look like HC-nnn with three digits. Did you mean HC-008? Known ids: … Call get_article again with one of these, or call search_kb …` | raised |
| v2 | `Error executing tool get_article: Retry now: call get_article with article_id "HC-008". Reason: …` | raised, action first |
| **v3 (kept)** | `Retry now: call get_article with article_id "HC-008". Reason: article 'HC-8' not found; ids are HC- plus three digits. Known ids: HC-001, …, HC-008.` | returned as `isError: true`, no prefix |

## Transcripts

### Before: "Error 3"

```
[host] query: 'Show me the refunds and escalation article, HC-8, and tell me the refund window.'
[host] lap 1: replayed call {"name": "get_article", "arguments": {"article_id": "HC-8"}}
[host]   tools/call get_article -> server clouddesk-kb args={"article_id": "HC-8"} ERROR 'Error executing tool get_article: Error 3'
[host] lap 2: model in=617 out=40 4584ms
[host] answer: 'I encountered an error while trying to fetch the article with ID HC-8. Please check the article ID and try again. If the issue persists, please contact our support team for further assistance.'
```

The model cannot tell a malformed id from a dead server. It blames the user ("check the article ID and try again") and escalates to support for an error it could have fixed in one call.

### After v1: explains, ends with the fix

```
[host] query: 'Show me the refunds and escalation article, HC-8, and tell me the refund window.'
[host] lap 1: replayed call {"name": "get_article", "arguments": {"article_id": "HC-8"}}
[host]   tools/call get_article -> server clouddesk-kb args={"article_id": "HC-8"} ERROR "Error executing tool get_article: article 'HC-8' not found: ids look like HC-nnn with three digits. Did you mean HC-008? Known ids: HC-001, HC-002, HC-003, HC-004, HC-005, HC-006, HC-008. Call get_art"
[host] lap 2: model in=834 out=106 12751ms
[host] answer: "I see that the article ID you provided ('HC-8') is incorrect. The IDs should be in the format HC-nnn where nnn is a three-digit number. I've checked the known valid IDs and they include HC-001 through HC-008. Please try using one of these valid IDs instead of HC-8. If you're looking for information "
```

### After v2: retry instruction first, still behind the SDK's "Error executing tool" prefix

```
[host] query: 'Show me the refunds and escalation article, HC-8, and tell me the refund window.'
[host] lap 1: replayed call {"name": "get_article", "arguments": {"article_id": "HC-8"}}
[host]   tools/call get_article -> server clouddesk-kb args={"article_id": "HC-8"} ERROR 'Error executing tool get_article: Retry now: call get_article with article_id "HC-008". Reason: article \'HC-8\' not found; ids are HC- plus three digits. Known ids: HC-001, HC-002, HC-003, HC-004, HC-0'
[host] lap 2: model in=821 out=48 6387ms
[host] answer: "I'm sorry, but I don't have information about the article with ID HC-8. The IDs for known articles start with HC-001 through HC-008. Could you please provide the correct article ID?"
```

### After v3 (kept): returned, not raised, retry instruction first

```
[host] query: 'Show me the refunds and escalation article, HC-8, and tell me the refund window.'
[host] lap 1: replayed call {"name": "get_article", "arguments": {"article_id": "HC-8"}}
[host]   tools/call get_article -> server clouddesk-kb args={"article_id": "HC-8"} ERROR 'Retry now: call get_article with article_id "HC-008". Reason: article \'HC-8\' not found; ids are HC- plus three digits. Known ids: HC-001, HC-002, HC-003, HC-004, HC-005, HC-006, HC-008.'
[host] lap 2: model in=815 out=74 9361ms
[host] answer: "I'm sorry, but I couldn't find the article with ID HC-8. The known valid IDs are HC-001, HC-002, HC-003, HC-004, HC-005, HC-006, and HC-008. Please try again with a different ID."
```

## What changed, and what did not

- **Handling changed.** Before, the model learned nothing from the error and sent the user to support. After, it states the id format and the valid ids, so the user can fix the request in one turn instead of filing a support ticket.
- **The model never retried.** In three rewrites the error got more explicit each time, and v3 opens with the exact call to make. The 1.5B model still answered the user after the error instead of calling `get_article("HC-008")`. In every run in this project, this model treats a tool error as the end of the task. The error is now recoverable, because a model that retries has everything it needs in the first sentence. This model is not one that retries.
- **Why v3 is the one kept.** A raised error reaches the model as `Error executing tool get_article: …`, which reads as a dead tool, not a fixable request. Returning the result with `isError: true` keeps the protocol's failure flag and drops the misleading prefix.
