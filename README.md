# Smart Financial Data Backend

A backend system that lets users upload financial data files (CSV/Excel), automatically understands the data structure using AI, proactively generates 10–15 relevant financial KPIs, and provides a natural language query interface to ask custom questions about the data.

## Features

- **Multi-file upload** — Accepts multiple CSV/Excel files in a single request and dynamically creates queryable database tables.
- **AI-powered schema mapping** — Uses an LLM to automatically detect relationships between uploaded tables (e.g., shared keys like `customer_id`).
- **Proactive KPI engine** — Automatically suggests and calculates 10–15 relevant financial metrics based on the uploaded data, without any user prompt.
- **Text-to-SQL query interface** — Users can ask questions in plain English; the LLM converts them into SQL, executes them, and returns both the raw result and a natural language summary.
- **Dashboard caching** — KPI results are cached after first computation and only regenerated when new data is uploaded, avoiding redundant LLM calls.
- **Graceful error handling** — Individual metric or query failures don't crash the system; errors are isolated and reported per-item.

## Tech Stack

| Component | Choice | Why |
|---|---|---|
| Framework | FastAPI | Async support, automatic interactive docs (`/docs`), minimal boilerplate |
| Data processing | Pandas | Standard for reading/parsing CSV and Excel files |
| Database | DuckDB | Runs analytical SQL directly on Pandas dataframes with no separate DB server needed — ideal for this use case |
| LLM Layer | Groq (Llama 3.3 70B) | Fast inference, generous free tier, native JSON-mode output for reliable structured responses |

**Note:** The LLM layer is abstracted through a single client initialization, so switching providers (e.g., to Gemini or OpenAI) would only require changing the client setup and API call syntax, not the overall logic.
## Architecture

```
[Upload CSV/Excel] ---> [Pandas Parsing] ---> [DuckDB Table Creation]
                                                      |
                                                      v
                                        [LLM: Schema & Relationship Mapping]
                                                      |
                                                      v
                                   [LLM: Proactive KPI Suggestion + SQL Generation]
                                                      |
                                                      v
                                     [Cached Dashboard: /dashboard endpoint]

[Natural Language Question] ---> [LLM: Text-to-SQL] ---> [DuckDB Execution] ---> [LLM: NL Summary]
```

## Setup Instructions

1. **Clone the repository**
   ```
   git clone <your-repo-url>
   cd finance-backend
   ```

2. **Create and activate a virtual environment**
   ```
   python -m venv venv
   venv\Scripts\activate      # Windows
   source venv/bin/activate   # Mac/Linux
   ```

3. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```

4. **Set up environment variables**

   Create a `.env` file in the project root:
   ```
   GROQ_API_KEY=your_groq_api_key_here
   ```
   Get a free API key at [console.groq.com](https://console.groq.com/keys).

5. **Run the server**
   ```
   uvicorn main:app --reload
   ```

6. **Access interactive API docs**

   Open `http://127.0.0.1:8000/docs` in your browser to test all endpoints directly.

## API Endpoints

### `POST /upload`
Uploads one or more CSV/Excel files, parses them, and stores each as a table in DuckDB. Also returns the auto-detected relationships between tables.

**Example response:**
```json
{
  "uploaded_tables": [
    {"file": "sales.csv", "table_name": "sales", "rows": 15, "columns": ["invoice_id", "customer_id", "date", "amount", "category"]}
  ],
  "schema_map": {
    "relationships": [
      {"table_a": "sales", "table_b": "expenses", "shared_key": "customer_id"}
    ]
  }
}
```

### `GET /dashboard`
Returns 10–15 proactively generated financial KPIs, calculated directly from the uploaded data. Results are cached until new data is uploaded.

**Example response (truncated):**
```json
{
  "dashboard_metrics": [
    {"name": "Total Revenue", "description": "Sum of all revenue", "value": [[196900]]},
    {"name": "Customer Acquisition Cost", "description": "Average expense per customer", "value": [[2573.78]]}
  ]
}
```

### `POST /query`
Accepts a natural language question, converts it to SQL via the LLM, executes it, and returns the result along with a plain-English summary.

**Request:**
```json
{"question": "What is the total revenue?"}
```

**Response:**
```json
{
  "question": "What is the total revenue?",
  "sql_query": "SELECT SUM(amount) AS total_revenue FROM sales",
  "columns": ["total_revenue"],
  "result": [[196900]],
  "summary": "The total revenue is $196,900."
}
```

## Design Decisions

- **DuckDB over PostgreSQL**: Since the scope assumes clean, well-formatted data and no persistent multi-user concerns, DuckDB's ability to query Pandas dataframes directly (without a separate database server) made development significantly faster while still supporting full analytical SQL.
- **Groq over OpenAI/Gemini**: Groq's free tier offered more reliable rate limits during development compared to Gemini, and its OpenAI-compatible JSON-mode output made structured responses (schema maps, KPI lists, SQL queries) consistently parseable.
- **SQL generation over hardcoded metrics**: Rather than hardcoding what a "financial metric" is, the LLM is given the schema and asked to both name relevant metrics and write the SQL to compute them — making the system generalize to any uploaded dataset, not just a fixed schema.
- **Per-metric error isolation**: Each KPI's SQL query runs inside its own try/except block, so one malformed query (e.g., from an LLM misreading a column type) doesn't take down the entire dashboard.
- **In-memory caching**: A simple global variable caches dashboard results between calls, invalidated on new uploads. This was sufficient for the MVP scope; a production system would use a proper cache store (Redis) or persistent storage.

## Known Limitations / What I'd Improve With More Time

- **Date handling**: Uploaded date columns are currently stored as strings (VARCHAR) rather than proper DATE types, which causes LLM-generated queries using date functions (e.g., `EXTRACT(MONTH FROM date)`) to occasionally fail. These failures are caught gracefully and reported per-metric rather than crashing the system, but a production version would explicitly parse and cast date columns on ingestion.
- **SQL safety**: Current protection against destructive queries is a basic keyword blocklist (`DROP`, `DELETE`, `UPDATE`, `INSERT`). A production system should use a read-only database connection/role or a proper SQL parser to validate query structure rather than string matching.
- **No authentication**: The API is fully open; a real deployment would need user authentication and per-user data isolation.
- **Caching**: In-memory cache resets on server restart and doesn't scale across multiple server instances. Redis or a persistent cache table would be a better fit for production.
- **Large file handling**: Files are read entirely into memory before parsing; very large files would benefit from chunked/streaming processing.

## Testing

A helper script, `test_upload.py`, is included to quickly test multi-file uploads via the command line (useful since Swagger UI's interactive docs have a known rendering issue with multi-file inputs). It can also be tested directly through the `/docs` interface for single-file uploads and all other endpoints.

```
python test_upload.py
```
