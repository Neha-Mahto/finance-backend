from pydantic import BaseModel
from fastapi import FastAPI, UploadFile, File
from typing import List
import pandas as pd
import duckdb
import io
from groq import Groq
from dotenv import load_dotenv
import os
import json

# Load API key from .env file
load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

app = FastAPI()

# Persistent DuckDB connection - tables are stored in data.db on disk
con = duckdb.connect("data.db")

# Simple in-memory cache for dashboard results; cleared on new uploads
dashboard_cache = None


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    """
    Accepts multiple CSV/Excel files, parses each into a DataFrame,
    and stores it as a table in DuckDB. Also runs schema mapping
    to detect relationships between the uploaded tables.
    """
    global dashboard_cache

    if not files:
        return {"error": "No files provided"}

    results = []

    for file in files:
        contents = await file.read()

        # Build a safe table name from the filename (no extension/spaces/dashes)
        table_name = file.filename.rsplit(".", 1)[0].replace(" ", "_").replace("-", "_")

        # Parse based on file type
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents))
        elif file.filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(contents))
        else:
            results.append({"file": file.filename, "status": "skipped - unsupported format"})
            continue

        # Create/replace a DuckDB table directly from the dataframe
        con.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df")

        results.append({
            "file": file.filename,
            "table_name": table_name,
            "rows": len(df),
            "columns": list(df.columns)
        })

    # Ask the LLM to find relationships between the newly uploaded tables
    schema_map = get_schema_map(results)

    # New data uploaded -> old cached dashboard is no longer valid
    dashboard_cache = None

    return {"uploaded_tables": results, "schema_map": schema_map}


def get_schema_map(tables_info):
    """
    Sends table names + columns to the LLM and asks it to identify
    relationships (shared keys) between tables, e.g. customer_id.
    Returns a JSON dict; falls back to empty relationships on parse failure.
    """
    prompt = f"""
You are given information about database tables. 
Tables info: {json.dumps(tables_info)}

Identify relationships between these tables (e.g., shared columns like customer_id, invoice_id).
Return ONLY valid JSON in this exact format, no extra text:
{{
  "relationships": [
    {{"table_a": "table1", "table_b": "table2", "shared_key": "column_name"}}
  ]
}}
If no relationships found, return {{"relationships": []}}
"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}  # forces the LLM to return valid JSON only
    )

    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        # LLM didn't return valid JSON - fail safe instead of crashing
        return {"relationships": []}


def get_proactive_metrics(tables_info, schema_map):
    """
    Asks the LLM to suggest 10-15 relevant financial KPIs for the given
    tables/relationships, along with the actual SQL query to compute each one.
    """
    prompt = f"""
You are a financial data analyst. Given these tables and their relationships, suggest 10-15 relevant financial metrics/KPIs that should be tracked.

Tables info: {json.dumps(tables_info)}
Relationships: {json.dumps(schema_map)}

For each metric, provide:
- name: short name of the metric
- description: what it measures
- sql_query: an actual DuckDB SQL query to calculate it using the exact table/column names given above

Return ONLY valid JSON in this exact format, no extra text:
{{
  "metrics": [
    {{"name": "Total Revenue", "description": "Sum of all revenue", "sql_query": "SELECT SUM(amount) as total FROM sales"}}
  ]
}}
"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )

    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        # LLM didn't return valid JSON - fail safe instead of crashing
        return {"metrics": []}


@app.get("/dashboard")
def get_dashboard():
    """
    Returns 10-15 proactively generated financial KPIs computed from
    the currently uploaded data. Result is cached until new data is
    uploaded, to avoid repeated LLM calls on every request.
    """
    global dashboard_cache

    # Serve from cache if available - avoids recalculating on every call
    if dashboard_cache is not None:
        return dashboard_cache

    # Get all current tables and their columns from DuckDB
    tables = con.execute("SHOW TABLES").fetchall()
    tables_info = []

    for (table_name,) in tables:
        columns = con.execute(f"DESCRIBE {table_name}").fetchall()
        column_names = [col[0] for col in columns]
        tables_info.append({"table_name": table_name, "columns": column_names})

    schema_map = get_schema_map(tables_info)
    metrics_data = get_proactive_metrics(tables_info, schema_map)

    results = []
    for metric in metrics_data.get("metrics", []):
        try:
            # Run each metric's SQL query individually so one bad query
            # (e.g. wrong column/type from the LLM) doesn't break the rest
            query_result = con.execute(metric["sql_query"]).fetchall()
            results.append({
                "name": metric["name"],
                "description": metric["description"],
                "value": query_result
            })
        except Exception as e:
            results.append({
                "name": metric["name"],
                "description": metric["description"],
                "error": str(e)
            })

    dashboard_cache = {"dashboard_metrics": results}
    return dashboard_cache


class QueryRequest(BaseModel):
    question: str


@app.post("/query")
def query_data(request: QueryRequest):
    """
    Accepts a natural language question, asks the LLM to convert it into
    a SQL query against the current schema, runs it, and returns both the
    raw result and a plain-English summary of the answer.
    """
    # Get current schema so the LLM knows what tables/columns exist
    tables = con.execute("SHOW TABLES").fetchall()
    tables_info = []

    for (table_name,) in tables:
        columns = con.execute(f"DESCRIBE {table_name}").fetchall()
        column_names = [col[0] for col in columns]
        tables_info.append({"table_name": table_name, "columns": column_names})

    prompt = f"""
You are a SQL expert. Given the database schema and a user's question, write a DuckDB SQL query to answer it.

Schema: {json.dumps(tables_info)}
User question: {request.question}

Return ONLY valid JSON in this exact format, no extra text:
{{"sql_query": "SELECT ..."}}

IMPORTANT: Only use SELECT statements. Never use DROP, DELETE, UPDATE, or INSERT.
"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )

    result = json.loads(response.choices[0].message.content)
    sql_query = result["sql_query"]

    # Safety check - block destructive queries even though we only asked for SELECT
    forbidden = ["DROP", "DELETE", "UPDATE", "INSERT"]
    if any(word in sql_query.upper() for word in forbidden):
        return {"error": "Unsafe query detected, request rejected"}

    # Run the LLM-generated query
    try:
        query_result = con.execute(sql_query).fetchall()
        columns = [desc[0] for desc in con.description]

        # Second LLM call: turn the raw SQL result into a plain-English answer
        summary_prompt = f"""
User asked: {request.question}
SQL result: {query_result}
Give a 1-2 sentence natural language answer to the user's question based on this data.
"""
        summary_response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": summary_prompt}]
        )
        summary = summary_response.choices[0].message.content

        return {
            "question": request.question,
            "sql_query": sql_query,
            "columns": columns,
            "result": query_result,
            "summary": summary
        }
    except Exception as e:
        return {"error": str(e), "sql_query": sql_query}