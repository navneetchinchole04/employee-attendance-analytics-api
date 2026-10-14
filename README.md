# Employee Attendance & Analytics API

A REST API built with FastAPI and MongoDB for employee attendance management, punch-in/punch-out operations, attendance corrections with audit history, and attendance analytics.

## Tech Stack

- Python
- FastAPI
- MongoDB
- PyMongo

## Features

- Employee registration and duplicate employee-code validation
- Attendance punch-in and punch-out
- Attendance correction with audit history
- Monthly attendance analytics
- Department summaries and trends
- Employee leaderboard
- MongoDB indexes for query performance

## Local Setup

1. Install dependencies:

   pip install -r requirements.txt

2. Configure MONGO_URI and MONGO_DB using environment variables or a local .env file.

3. Start the API:

   uvicorn app.main:app --port 8000 --reload

4. Open http://127.0.0.1:8000/docs to explore the API.

## Documentation

- REVIEW.md — Starter-code review and findings.
- DECISIONS.md — Implementation decisions.
- DATA_MODEL.md — MongoDB data model.
- openapi.yaml — API contract.

## Security

Do not commit .env files, credentials, or other secrets.
