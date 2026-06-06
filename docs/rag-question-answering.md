# RAG Document Question Answering

This document explains how the document Q&A feature works in this project. The
goal is to make the implementation easy to understand as a learning project, not
just to describe the final user experience.

## What This Feature Does

The Ask Documents page lets a signed-in viewer ask a natural-language question
across uploaded documents and receive an answer grounded in retrieved document
chunks.

The feature is intentionally citation-first:

- The answer is generated only after relevant chunks are retrieved.
- Each cited chunk is hydrated back to the canonical PostgreSQL `Document`.
- The UI shows source document links and excerpts beside the generated answer.
- If no useful context is retrieved, the app returns a no-context answer instead
  of asking the model to guess.

## RAG In This Project

RAG stands for retrieval-augmented generation.

In this project:

- Retrieval finds the most relevant document chunks.
- Augmentation places those chunks into the model prompt as context.
- Generation asks AWS Bedrock Nova Lite to answer using only that context.

The current RAG implementation uses AWS Bedrock only:

| Step | Service |
| --- | --- |
| Document chunk embeddings | AWS Bedrock Titan Text Embeddings V2 |
| Question embedding | AWS Bedrock Titan Text Embeddings V2 |
| Vector retrieval | OpenSearch k-NN chunk index |
| Source of truth | PostgreSQL `Document` records |
| Answer generation | AWS Bedrock Nova Lite |

## End-To-End Call Flow

```text
User opens /ask/
  -> Django routes to ask_documents()
  -> answer_question(question)
  -> retrieve_question_context(question)
  -> get_titan_embedding(question)
  -> AWS Bedrock Titan returns a question vector
  -> OpenSearch k-NN searches document chunk vectors
  -> PostgreSQL hydrates matching Document records
  -> build_rag_prompt(question, retrieved chunks)
  -> AWS Bedrock Nova Lite generates a grounded answer
  -> ask_documents.html renders answer and citations
```

## The Two Bedrock Model Roles

The Bedrock models do different jobs.

`amazon.titan-embed-text-v2:0` creates vectors. It is used when documents are
chunked and when a user asks a question. Titan does not write the final answer.

`amazon.nova-lite-v1:0` generates text. It receives the user question plus the
retrieved document excerpts and writes the final answer with citations.

That separation is important:

```text
Titan Embeddings = meaning comparison
Nova Lite = natural-language answer generation
```

## Retrieval Flow

Uploaded documents already have extracted text. During embedding rebuild or
upload processing, the text is split into paragraph-aware chunks and each chunk
is embedded with Titan.

Those chunk records are stored in PostgreSQL as `DocumentChunk` rows and indexed
into OpenSearch with a `knn_vector` field.

When a user asks a question:

1. The question is embedded with the same Titan model.
2. OpenSearch compares the question vector to indexed chunk vectors.
3. The top matching chunks are returned with `document_id`, chunk text, and
   scores.
4. Django loads the matching PostgreSQL `Document` rows with
   `Document.objects.in_bulk(...)`.

OpenSearch helps find candidates, but PostgreSQL remains the final authority for
document records.

## Prompt Flow

After retrieval, the app builds a prompt with the question and numbered document
excerpts.

The prompt tells Nova Lite to:

- answer only from the provided excerpts
- cite factual claims with bracketed ids such as `[1]`
- say when the excerpts do not contain enough information
- avoid using or implying knowledge from documents that are not included in the
  retrieved excerpts

Conceptually, the prompt looks like this:

```text
Question:
What are the onboarding requirements?

Document excerpts:
[1]
Document: documents/employee_onboarding_policy.docx
Department: HR
Type: Policy
Excerpt: New hire orientation, identity verification, equipment pickup...

[2]
Document: documents/benefits_enrollment_summary.docx
Department: HR
Type: Summary
Excerpt: Benefits enrollment must be completed during open enrollment...
```

The model sees only the retrieved context, not the whole database.

## Permission Boundary

The RAG service accepts an explicit accessible-document queryset from the Django
view. Today the application permissions are role-based through Okta groups, so a
Viewer can ask across the same documents that Viewer can search and open. The
retrieval step may find OpenSearch chunk candidates, but citations are only
created after those candidates hydrate through the accessible PostgreSQL
`Document` queryset.

That boundary matters because OpenSearch is a derived index. If future
row-level document ACLs are added, the accessible queryset can become narrower
without changing the Bedrock prompt or generation path.

## UI Flow

The user-facing page is:

```text
/ask/
```

The page displays:

- the generated answer
- citation ids
- source document names
- retrieval scores
- chunk excerpts
- links to open the cited documents

This is separate from AI Search:

| Page | Purpose |
| --- | --- |
| `/ai-search/` | Find semantically similar documents |
| `/ask/` | Generate an answer from retrieved document chunks |

## Configuration

The main RAG settings are:

| Setting | Purpose |
| --- | --- |
| `BEDROCK_EMBED_MODEL_ID` | Titan embedding model for chunks and questions |
| `BEDROCK_NOVA_MODEL_ID` | Nova Lite generation model for answers |
| `AI_RAG_TOP_K` | Number of retrieved chunks used as answer context |
| `AI_RAG_MAX_CONTEXT_CHARS` | Maximum characters included from each chunk |
| `AI_RAG_MAX_ANSWER_TOKENS` | Maximum generated answer tokens |
| `AI_RAG_MIN_CONTEXT_CHARS` | Minimum combined retrieved text required before generation |
| `AI_RAG_MIN_RETRIEVAL_SCORE` | Optional retrieval score floor before a chunk can be used |
| `BEDROCK_TIMEOUT_SECONDS` | Bedrock client timeout |
| `OPENSEARCH_CHUNK_INDEX` | OpenSearch chunk vector index |

For the current OpenShift configuration, the important model values are:

```yaml
BEDROCK_NOVA_MODEL_ID: "amazon.nova-lite-v1:0"
BEDROCK_EMBED_MODEL_ID: "amazon.titan-embed-text-v2:0"
AI_RAG_TOP_K: "5"
AI_RAG_MAX_CONTEXT_CHARS: "1800"
AI_RAG_MAX_ANSWER_TOKENS: "700"
AI_RAG_MIN_CONTEXT_CHARS: "80"
AI_RAG_MIN_RETRIEVAL_SCORE: "0"
```

## Failure Behavior

The feature is designed to fail clearly.

If there are no embeddings, the page tells the user to rebuild embeddings or
upload documents after Bedrock is configured.

If retrieval returns no chunks, the app returns:

```text
The available documents do not contain enough relevant information to answer this question.
```

The same refusal is returned before calling Bedrock if the retrieved accessible
chunks do not meet the minimum context length. This keeps low-context answers
from turning into guesses.

If Bedrock or OpenSearch fails, the Django view catches the error and shows a
warning message instead of breaking document browsing or search.

## Why PostgreSQL Hydration Matters

OpenSearch is a derived retrieval index. It is fast for keyword and vector
search, but it is not the system of record.

PostgreSQL remains the canonical source for:

- document metadata
- file references
- upload lifecycle
- audit history
- source document visibility

That is why RAG citations are not shown directly from OpenSearch alone. They are
hydrated back into `Document` records first.

## Files To Read

The main implementation files are:

| File | Role |
| --- | --- |
| `documents/rag.py` | Retrieval, prompt building, Bedrock answer generation |
| `documents/views.py` | `/ask/` view and error handling |
| `documents/templates/ask_documents.html` | Answer and citation UI |
| `documents/embeddings.py` | Titan embedding generation and chunking |
| `documents/opensearch_indexing.py` | OpenSearch chunk vector indexing |
| `documents/semantic_search.py` | Existing semantic retrieval path used as the foundation |

## Future Enhancements

Useful next steps for learning and production hardening:

- hybrid keyword plus vector retrieval
- citation id validation after generation
- streaming answers
- answer audit events
- per-question cost and latency metrics
