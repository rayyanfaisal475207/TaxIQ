# System Architecture

This document provides a high-level overview of the RAG Chatbot architecture.

## Three-Tier Architecture

The system is built on a modern three-tier architecture, designed for modularity and scalability:

1.  **Frontend (Next.js / React)**:
    - Provides a modern, responsive, and dynamic chat interface.
    - Manages user sessions, authentication tokens, and chat history rendering.
    - Built using React components with Tailwind CSS for styling.

2.  **Backend (FastAPI)**:
    - Serves as the core orchestration layer, exposing REST API endpoints for the frontend.
    - Handles JWT authentication and user session management.
    - Implements the complete RAG pipeline, including query rewriting, routing, evaluation, and response generation.
    - Manages LLM connections via Google GenAI SDK and Groq SDK, including token and API key rotation.

3.  **Data Tier (Supabase / ChromaDB)**:
    - **Supabase**: Serves as the primary source of truth for structured data, managing user identities, chat sessions, message histories, and the `tax_rates` table.
    - **ChromaDB**: Acts as the local vector store for holding document embeddings generated during the ingestion phase, enabling fast semantic retrieval for RAG operations.

## Request Routing Logic

When a user submits a query, the backend orchestrator employs a multi-branch routing strategy to determine the most effective way to fulfill the request. The router LLM classifies the intent into one of four distinct routes:

1.  **RAG Route (Retrieval-Augmented Generation)**:
    - **Purpose**: For questions requiring specific knowledge from the ingested document corpus (e.g., tax policies, guidelines).
    - **Flow**: The query is rewritten, embeddings are generated, and a semantic search is performed against ChromaDB. The retrieved chunks are reranked using Reciprocal Rank Fusion (RRF), evaluated for relevance, and finally injected into the LLM context for generation.

2.  **SQL Route (Structured Data)**:
    - **Purpose**: For analytical or structured data lookups against the `tax_rates` database table.
    - **Flow**: The orchestrator extracts parameters (e.g., `tax_type`, `category`, `filer_status`) from the query and constructs a SQL query. It then retrieves the relevant rows directly from Supabase and passes them to the LLM to ground the response.

3.  **WEB Route (External Knowledge)**:
    - **Purpose**: For questions requiring up-to-date or external information not present in the internal knowledge base.
    - **Flow**: The query is routed to the Tavily Web Search API to fetch external web content, which is then provided to the LLM to generate an informed response.

4.  **DIRECT Route (Conversational / Tools)**:
    - **Purpose**: For casual conversation, greetings, simple logic, or live calculations (e.g., weather, math, date/time).
    - **Flow**: The query bypasses external retrieval systems entirely. It relies on the LLM's internal knowledge and native tool-calling capabilities. The LLM may optionally invoke external generic tools (Weather, Calculator, Datetime) mid-generation to fulfill the user's request.
