# YojanaSetu – project rules for AI assistants

## What we build
An AI agent that helps farmers, women and senior citizens find the government
schemes they qualify for (central + Telangana), explains why or why not, shows
the document path, and remembers citizens across sessions.
FalkorDB is the source of truth for all eligibility logic.

## Hard rules
- Eligibility is decided ONLY by Cypher queries in graph/queries.py.
  The LLM never invents schemes, criteria or documents.
- Never generate or modify scheme data in data/*.csv – humans enter and verify it.
- Synthetic citizens only. No real names, Aadhaar or phone numbers.
- Every scheme answer must include its source_url.
- Never hardcode passwords or hosts. Read FALKORDB_HOST, FALKORDB_PORT,
  FALKORDB_USERNAME and FALKORDB_PASSWORD from environment variables.
- Keep functions small and add a docstring explaining WHY, so we can explain
  every part to the judges.
- Plan before coding: show the plan and wait for approval on bigger changes.

## Structure
data/   CSV files (scheme data)
graph/  loader + Cypher queries
agent/  LLM agent + tools
api/    FastAPI backend
web/    React frontend

## Ownership
data/ and graph/ = Rusheek. agent/, api/ and web/ = Shailu.
Don't edit the other person's folders; suggest changes instead.

## Stack
Python 3.11+, FastAPI, falkordb, litellm, React + Vite + Tailwind, react-force-graph.